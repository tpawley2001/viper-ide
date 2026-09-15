"""Discover Python interpreters, probe them, and download new ones on Windows."""
from __future__ import annotations

import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .paths import child_env, downloads_dir, helper, pythons_dir, subprocess_flags

IS_WIN = os.name == "nt"
USER_AGENT = "ViperIDE"

PROBE = (
    "import sys,json,os,importlib.util as u;"
    "print(json.dumps({'version':sys.version.split()[0],'prefix':sys.prefix,"
    "'base_prefix':getattr(sys,'base_prefix',sys.prefix),'executable':sys.executable,"
    "'pip':u.find_spec('pip') is not None,'bits':64 if sys.maxsize>2**32 else 32,"
    "'conda':os.path.exists(os.path.join(sys.prefix,'conda-meta'))}))"
)


@dataclass
class Interpreter:
    path: str
    version: str = ""
    prefix: str = ""
    is_venv: bool = False
    is_conda: bool = False
    has_pip: bool = True
    bits: int = 64
    managed: bool = False

    @property
    def kind(self) -> str:
        if self.is_venv:
            return "venv"
        if self.is_conda:
            return "conda"
        if self.managed:
            return "Viper"
        return "system"

    @property
    def short_version(self) -> str:
        return ".".join(self.version.split(".")[:2])

    def label(self) -> str:
        where = Path(self.prefix).name if self.is_venv else self.kind
        return f"Python {self.version} ({where})"

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def probe(path: str, timeout: float = 10.0) -> Interpreter | None:
    if not path or not os.path.isfile(path):
        return None
    if IS_WIN and "WindowsApps" in path:
        return None  # Microsoft Store stub that opens the Store instead of running
    try:
        out = subprocess.run([path, "-c", PROBE], capture_output=True, text=True,
                             timeout=timeout, env=child_env(), **subprocess_flags())
        info = json.loads(out.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None
    managed = os.path.normcase(os.path.abspath(path)).startswith(os.path.normcase(str(pythons_dir())))
    return Interpreter(
        path=os.path.abspath(path), version=info["version"], prefix=info["prefix"],
        is_venv=os.path.normcase(info["prefix"]) != os.path.normcase(info["base_prefix"]),
        is_conda=info["conda"], has_pip=info["pip"], bits=info["bits"], managed=managed,
    )


def venv_python(venv_dir: str | Path) -> Path:
    venv_dir = Path(venv_dir)
    return venv_dir / ("Scripts/python.exe" if IS_WIN else "bin/python")


def project_venvs(folder: str | None) -> list[str]:
    if not folder:
        return []
    out = []
    for name in (".venv", "venv", "env", ".env", "virtualenv"):
        p = venv_python(Path(folder) / name)
        if p.is_file():
            out.append(str(p))
    return out


def _py_launcher() -> list[str]:
    exe = shutil.which("py")
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "-0p"], capture_output=True, text=True, timeout=10,
                             **subprocess_flags()).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        m = re.match(r"^\s*-\S+(?:\s+\*)?\s+(.+?\.exe)\s*$", line)
        if m:
            found.append(m.group(1))
    return found


def _registry() -> list[str]:
    try:
        import winreg
    except ImportError:
        return []
    found = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                root = winreg.OpenKey(hive, r"Software\Python", 0, winreg.KEY_READ | view)
            except OSError:
                continue
            with root:
                for company in _enum_keys(winreg, root):
                    if company == "PyLauncher":
                        continue
                    try:
                        ck = winreg.OpenKey(root, company)
                    except OSError:
                        continue
                    with ck:
                        for tag in _enum_keys(winreg, ck):
                            try:
                                with winreg.OpenKey(ck, tag + r"\InstallPath") as ip:
                                    try:
                                        exe = winreg.QueryValueEx(ip, "ExecutablePath")[0]
                                    except OSError:
                                        exe = os.path.join(winreg.QueryValueEx(ip, "")[0], "python.exe")
                                    found.append(exe)
                            except OSError:
                                continue
    return found


def _enum_keys(winreg, key):
    i = 0
    while True:
        try:
            yield winreg.EnumKey(key, i)
        except OSError:
            return
        i += 1


def candidate_paths(project: str | None = None, extra: list[str] | None = None) -> list[str]:
    paths: list[str] = []
    paths += project_venvs(project)
    paths += extra or []
    exe_name = "python.exe" if IS_WIN else "python"
    paths += glob.glob(str(pythons_dir() / "*" / exe_name))
    home = Path.home()
    if IS_WIN:
        paths += _py_launcher() + _registry()
        local = os.environ.get("LOCALAPPDATA", str(home / "AppData/Local"))
        for pattern in (rf"{local}\Programs\Python\Python3*\python.exe", r"C:\Python3*\python.exe",
                        r"C:\Program Files\Python3*\python.exe"):
            paths += glob.glob(pattern)
        for conda in ("anaconda3", "miniconda3", "miniforge3"):
            for base in (home, Path(local), Path(r"C:\ProgramData")):
                paths += glob.glob(str(base / conda / "python.exe"))
                paths += glob.glob(str(base / conda / "envs" / "*" / "python.exe"))
        for d in os.environ.get("PATH", "").split(os.pathsep):
            p = os.path.join(d, "python.exe")
            if d and os.path.isfile(p):
                paths.append(p)
    else:
        for name in ("python3", "python"):
            if (w := shutil.which(name)):
                paths.append(w)
        for pattern in ("/usr/bin/python3.*", "/usr/local/bin/python3.*", "/opt/homebrew/bin/python3.*",
                        str(home / ".pyenv/versions/*/bin/python"),
                        str(home / "miniconda3/bin/python"), str(home / "anaconda3/bin/python")):
            paths += [p for p in glob.glob(pattern) if re.search(r"python3?(\.\d+)?$", p)]
    seen, unique = set(), []
    for p in paths:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen and os.path.isfile(p):
            seen.add(key)
            unique.append(os.path.abspath(p))
    return unique


def discover(project: str | None = None, extra: list[str] | None = None) -> list[Interpreter]:
    from concurrent.futures import ThreadPoolExecutor

    paths = candidate_paths(project, extra)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(probe, paths))
    interps = [r for r in results if r]
    # Same install reachable through several paths (PATH, registry, py launcher).
    seen, unique = set(), []
    for i in interps:
        key = (os.path.normcase(i.prefix), i.version, i.is_venv and os.path.normcase(i.path))
        if key not in seen:
            seen.add(key)
            unique.append(i)
    unique.sort(key=lambda i: (not i.is_venv, [-int(x) for x in re.findall(r"\d+", i.version)[:3]]))
    return unique


def create_venv(base: Interpreter | str, target: str) -> Interpreter | None:
    exe = base.path if isinstance(base, Interpreter) else base
    proc = subprocess.run([exe, "-m", "venv", target], capture_output=True, text=True,
                          timeout=300, env=child_env(), **subprocess_flags())
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip() or "python -m venv failed")
    return probe(str(venv_python(target)))


# ------------------------------------------------------------ Python downloads
def _get(url: str, timeout: float = 15.0):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}),
                                  timeout=timeout)


def _head_ok(url: str) -> bool:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except OSError:
        return False


def _arch_suffix() -> str:
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "-arm64"
    return "-amd64" if sys.maxsize > 2**32 or machine in ("amd64", "x86_64") else ""


def installer_url(version: str) -> str:
    return f"https://www.python.org/ftp/python/{version}/python-{version}{_arch_suffix()}.exe"


def available_python_versions(max_cycles: int = 3, progress=None) -> list[str]:
    """Newest patch release with a Windows installer for each supported cycle.

    Security-only branches stop shipping installers, so walk patch numbers down
    until python.org actually has one.
    """
    from datetime import date

    with _get("https://endoflife.date/api/python.json") as r:
        cycles = json.load(r)
    today = date.today().isoformat()
    versions = []
    for c in cycles:
        eol = c.get("eol")
        if eol is True or (isinstance(eol, str) and eol <= today):
            continue
        latest = c.get("latest", "")
        m = re.match(r"^(\d+)\.(\d+)\.(\d+)$", latest)
        if not m:
            continue
        major, minor, patch = map(int, m.groups())
        if progress:
            progress(f"Checking Python {major}.{minor}...")
        for p in range(patch, max(-1, patch - 20), -1):
            v = f"{major}.{minor}.{p}"
            if _head_ok(installer_url(v)):
                versions.append(v)
                break
        if len(versions) >= max_cycles:
            break
    return versions


def download(url: str, dest: Path, progress=None) -> Path:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with _get(url, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 16):
            f.write(chunk)
            done += len(chunk)
            if progress and total:
                progress((done, total))
    os.replace(tmp, dest)
    return dest


def verify_signature(path: Path) -> tuple[bool, str]:
    """Authenticode check: the installer must be validly signed by the PSF."""
    if not IS_WIN:
        return True, "signature check skipped (not Windows)"
    script = (f"$s = Get-AuthenticodeSignature -LiteralPath '{path}'; "
              "Write-Output $s.Status; Write-Output $s.SignerCertificate.Subject")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, timeout=60, **subprocess_flags()).stdout
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"could not run signature check: {e}"
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    status = lines[0] if lines else ""
    subject = lines[1] if len(lines) > 1 else ""
    ok = status == "Valid" and "Python Software Foundation" in subject
    return ok, f"{status or 'Unknown'}: {subject or 'no signer'}"


def install_python(version: str, progress=None) -> Interpreter:
    """Download the official installer and install per-user (no admin) into Viper's folder."""
    if not IS_WIN:
        raise RuntimeError("Automatic Python download is only available on Windows. "
                           "Install Python with your system package manager.")
    target = pythons_dir() / version
    exe = target / "python.exe"
    if (existing := probe(str(exe))):
        return existing
    say = progress or (lambda _msg: None)
    installer = downloads_dir() / Path(installer_url(version)).name
    try:
        if not installer.is_file():
            say(f"Downloading Python {version}...")
            download(installer_url(version), installer, progress)
        say("Verifying installer signature...")
        ok, detail = verify_signature(installer)
        if not ok:
            installer.unlink(missing_ok=True)
            raise RuntimeError(f"Installer signature check failed ({detail}).")
        say(f"Installing Python {version} (per-user)...")
        subprocess.run([str(installer), "/quiet", "InstallAllUsers=0", "PrependPath=0",
                        "Include_launcher=0", "Include_test=0", "Shortcuts=0", "AssociateFiles=0",
                        f"TargetDir={target}"], timeout=900, **subprocess_flags())
        if (interp := probe(str(exe))):
            return interp
        # Same version already installed elsewhere: the installer goes into
        # maintenance mode and ignores TargetDir.
        for i in discover():
            if i.version == version and not i.is_venv:
                return i
    except RuntimeError:
        raise
    except Exception as e:  # noqa: BLE001 - fall through to the portable package
        say(f"Installer route failed ({e}); trying the portable package...")
    return install_nuget_python(version, progress)


def install_nuget_python(version: str, progress=None) -> Interpreter:
    """Portable fallback: python.org's NuGet package is a plain zip of a full install."""
    say = progress or (lambda _msg: None)
    target = pythons_dir() / version
    pkg = downloads_dir() / f"python.{version}.nupkg"
    say(f"Downloading portable Python {version}...")
    download(f"https://www.nuget.org/api/v2/package/python/{version}", pkg, progress)
    with zipfile.ZipFile(pkg) as z:
        members = [m for m in z.namelist() if m.startswith("tools/")]
        for m in members:
            rel = m[len("tools/"):]
            if not rel or rel.endswith("/"):
                continue
            out = target / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(m) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
    exe = target / "python.exe"
    subprocess.run([str(exe), "-m", "ensurepip", "--upgrade"], capture_output=True, timeout=600,
                   **subprocess_flags())
    interp = probe(str(exe))
    if not interp:
        raise RuntimeError(f"Python {version} did not install correctly.")
    return interp


def find_missing(interp: str, modules: list[str], dists: list[str], search_paths: list[str],
                 timeout: float = 30.0) -> dict:
    """Ask the target interpreter which imports/distributions it can't satisfy."""
    payload = json.dumps({"modules": modules, "dists": dists, "paths": search_paths})
    out = subprocess.run([interp, str(helper("find_missing.py"))], input=payload, capture_output=True,
                         text=True, timeout=timeout, cwd=search_paths[0] if search_paths else None,
                         env=child_env({"PYTHONIOENCODING": "utf-8"}), **subprocess_flags())
    lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
    if not lines:
        raise RuntimeError(out.stderr.strip() or "find_missing produced no output")
    return json.loads(lines[-1])
