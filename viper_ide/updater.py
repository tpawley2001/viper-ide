"""Remote self-update: a version.json and an Inno Setup installer on a static web server.

Server side, one directory (published by scripts/publish_update.sh):

    version.json   {"versionName", "versionCode", "file", "sha256", "size", "publishedAt", "notes"}
    <setup>.exe    the installer named in "file"
    <portable>.zip optional; version.json then also has "portable": {"file", "sha256", "size"}

Installed copies try each update base in order (settings, VIPER_UPDATE_URLS, then
the built-in ones) and take the first that answers. The installer is refused
unless its sha256 matches the manifest, then run silently with Inno's restart
manager flags so the IDE closes and comes back on the new build. Portable copies
never run the installer: they take the zip instead and copy it over their own
folder after the IDE exits (see install_portable).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .paths import downloads_dir, is_portable, portable_root, resource

# Built-in feeds come from resources/update_feeds.txt (one URL per line, # comments),
# which a build ships but the repository doesn't: each distributor points their builds
# at their own server. Users add more in Settings > Update server URLs or
# VIPER_UPDATE_URLS (comma separated).
FEEDS_FILE = resource("update_feeds.txt")


def default_bases() -> list[str]:
    try:
        lines = FEEDS_FILE.read_text("utf-8").splitlines()
    except OSError:
        return []
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
USER_AGENT = f"ViperIDE/{__version__}"
_SAFE_FILE = re.compile(r"^[A-Za-z0-9._-]+\.exe$")
_SAFE_ZIP = re.compile(r"^[A-Za-z0-9._-]+\.zip$")


class UpdateError(Exception):
    pass


@dataclass
class Release:
    version: str
    file: str
    sha256: str
    size: int
    notes: str
    published: str
    base: str

    @property
    def url(self) -> str:
        return f"{self.base}/{self.file}"

    def newer_than(self, current: str) -> bool:
        return parse_version(self.version) > parse_version(current)


def parse_version(text: str) -> tuple[int, ...]:
    nums = [int(n) for n in re.findall(r"\d+", text or "")[:4]]
    return tuple(nums + [0] * (4 - len(nums)))


def _normalise(base: str) -> str:
    base = (base or "").strip().rstrip("/")
    if not base:
        return ""
    if not re.match(r"^https?://", base):
        base = "http://" + base
    if base.endswith("/version.json"):
        base = base[: -len("/version.json")]
    return base


def bases(settings=None) -> list[str]:
    configured = list(settings.get("update_urls") or []) if settings is not None else []
    configured += os.environ.get("VIPER_UPDATE_URLS", "").split(",")
    seen, out = set(), []
    for b in configured + default_bases():
        n = _normalise(b)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _get(url: str, timeout: float):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}),
                                  timeout=timeout)


def fetch_manifest(base: str, timeout: float = 5.0, portable: bool | None = None) -> Release:
    """The feed's release; a portable copy (default: this one, if it is) gets the zip, not the installer."""
    with _get(f"{base}/version.json", timeout) as r:
        data = json.load(r)
    portable = is_portable() if portable is None else portable
    entry = data.get("portable") if portable else data
    if not isinstance(entry, dict):
        raise UpdateError("this feed has no portable build")
    file = str(entry.get("file", ""))
    sha = str(entry.get("sha256", "")).lower()
    if not (_SAFE_ZIP if portable else _SAFE_FILE).match(file):
        raise UpdateError(f"manifest names an unsafe {'zip' if portable else 'installer'} file: {file!r}")
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise UpdateError("manifest has no valid sha256")
    return Release(version=str(data.get("versionName", "0")), file=file, sha256=sha,
                   size=int(entry.get("size") or 0), notes=str(data.get("notes") or ""),
                   published=str(data.get("publishedAt") or ""), base=base)


def check(settings=None, timeout: float = 5.0) -> tuple[Release | None, list[str]]:
    """The release from the first base that answers, plus the errors from those that didn't."""
    errors = []
    for base in bases(settings):
        try:
            return fetch_manifest(base, timeout), errors
        except (OSError, ValueError, UpdateError) as e:
            errors.append(f"{base}: {e}")
    return None, errors


def download(release: Release, progress=None, dest_dir: Path | None = None) -> Path:
    """Stream the installer to disk, hashing as it goes; a mismatch deletes it."""
    dest_dir = Path(dest_dir or downloads_dir())
    target = dest_dir / release.file
    part = target.with_suffix(".part")
    digest = hashlib.sha256()
    done = 0
    try:
        with _get(release.url, 60) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or release.size or 0)
            while chunk := r.read(1 << 16):
                f.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress and total:
                    progress((done, total))
    except (OSError, urllib.error.URLError):
        part.unlink(missing_ok=True)
        raise
    if digest.hexdigest() != release.sha256 or (release.size and done != release.size):
        part.unlink(missing_ok=True)
        raise UpdateError("the download didn't match its checksum, so it was discarded")
    os.replace(part, target)
    return target


def install(installer: Path, log: Path | None = None) -> subprocess.Popen:
    """Start the installer detached; it closes this app and restarts it when done."""
    if os.name != "nt":
        raise UpdateError("updates can only be installed on Windows")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
    args = [str(installer), "/SILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS",
            "/NORESTART"] + ([f"/LOG={log}"] if log else [])
    return subprocess.Popen(args, close_fds=True, creationflags=flags)


def extract_portable(archive: Path, dest: Path) -> Path:
    """Unpack a portable zip into dest (emptied first); returns the folder holding ViperIDE.exe."""
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    root = dest.resolve()
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            target = (dest / name).resolve()
            if target != root and root not in target.parents:
                raise UpdateError(f"the portable zip has an unsafe path: {name!r}")
        z.extractall(dest)
    exes = sorted(dest.rglob("ViperIDE.exe"), key=lambda p: len(p.parts))
    if not exes:
        raise UpdateError("the portable zip has no ViperIDE.exe")
    return exes[0].parent


# Runs after the IDE exits: copy the new files over the app folder and start it again.
# robocopy /E only adds and overwrites, so Data\ (settings, Pythons, venvs) is never touched.
_SWAP_PS1 = r"""param([int]$WaitPid, [string]$Source, [string]$Target, [string]$Log)
Start-Transcript -Path $Log -Force | Out-Null
Wait-Process -Id $WaitPid -Timeout 120 -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500
& robocopy $Source $Target /E /R:10 /W:1 /NP /NFL /NDL
$rc = $LASTEXITCODE
Write-Output "robocopy exit code $rc"
if ($rc -lt 8) { Remove-Item -LiteralPath $Source -Recurse -Force -ErrorAction SilentlyContinue }
Start-Process -FilePath (Join-Path $Target 'ViperIDE.exe')
Stop-Transcript | Out-Null
"""


def install_portable(archive: Path, log: Path | None = None) -> subprocess.Popen:
    """Unpack the zip, then hand off to a script that swaps it in once this process exits."""
    if os.name != "nt":
        raise UpdateError("updates can only be installed on Windows")
    target = portable_root()
    if target is None:
        raise UpdateError("this copy of Viper isn't portable")
    staged = extract_portable(archive, downloads_dir() / "portable-update")
    script = downloads_dir() / "portable-update.ps1"
    script.write_text(_SWAP_PS1, encoding="utf-8")
    log = log or downloads_dir() / "portable-update.log"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
    args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
            "-File", str(script), "-WaitPid", str(os.getpid()), "-Source", str(staged), "-Target", str(target),
            "-Log", str(log)]
    return subprocess.Popen(args, close_fds=True, creationflags=flags)


def apply(path: Path, log: Path | None = None) -> subprocess.Popen:
    """Install a downloaded update the way this copy needs it."""
    return install_portable(path, log) if is_portable() else install(path, log)
