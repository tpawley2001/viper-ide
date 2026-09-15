"""Find what a file imports and work out which PyPI distribution provides it."""
from __future__ import annotations

import ast
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# Import name -> PyPI distribution, only where the two differ.
MODULE_TO_DIST = {
    "cv2": "opencv-python", "PIL": "Pillow", "sklearn": "scikit-learn",
    "skimage": "scikit-image", "yaml": "PyYAML", "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil", "dotenv": "python-dotenv", "jwt": "PyJWT",
    "Crypto": "pycryptodome", "Cryptodome": "pycryptodomex", "OpenSSL": "pyOpenSSL",
    "serial": "pyserial", "serial_asyncio": "pyserial-asyncio", "usb": "pyusb",
    "magic": "python-magic", "docx": "python-docx", "pptx": "python-pptx",
    "fitz": "PyMuPDF", "pymupdf": "PyMuPDF", "gi": "PyGObject", "wx": "wxPython",
    "attr": "attrs", "telegram": "python-telegram-bot", "discord": "discord.py",
    "MySQLdb": "mysqlclient", "mysql.connector": "mysql-connector-python",
    "psycopg2": "psycopg2-binary", "pymysql": "PyMySQL", "jose": "python-jose",
    "multipart": "python-multipart", "socketio": "python-socketio",
    "engineio": "python-engineio", "websocket": "websocket-client",
    "Levenshtein": "python-Levenshtein", "slugify": "python-slugify",
    "speech_recognition": "SpeechRecognition", "pyaudio": "PyAudio",
    "git": "GitPython", "github": "PyGithub", "gitlab": "python-gitlab",
    "ldap": "python-ldap", "nmap": "python-nmap", "whois": "python-whois",
    "zmq": "pyzmq", "kafka": "kafka-python", "bson": "pymongo", "dns": "dnspython",
    "nacl": "PyNaCl", "Xlib": "python-xlib", "xdg": "pyxdg",
    "pkg_resources": "setuptools", "distutils": "setuptools", "_cffi_backend": "cffi",
    "markdown": "Markdown", "mpl_toolkits": "matplotlib", "jinja2": "Jinja2",
    "markupsafe": "MarkupSafe", "werkzeug": "Werkzeug", "pygments": "Pygments",
    "pyautogui": "PyAutoGUI", "kivy": "Kivy", "qtpy": "QtPy", "OpenGL": "PyOpenGL",
    "ffmpeg": "ffmpeg-python", "vlc": "python-vlc", "eyed3": "eyeD3",
    "exifread": "ExifRead", "barcode": "python-barcode", "fpdf": "fpdf2",
    "camelot": "camelot-py", "tabula": "tabula-py", "xlsxwriter": "XlsxWriter",
    "cpuinfo": "py-cpuinfo", "pynvml": "nvidia-ml-py", "wmi": "WMI",
    "requests_html": "requests-html", "scrapy": "Scrapy",
    "firebase_admin": "firebase-admin", "slack_sdk": "slack-sdk", "slack": "slackclient",
    "yt_dlp": "yt-dlp", "youtube_dl": "youtube-dl", "django": "Django",
    "rest_framework": "djangorestframework", "cherrypy": "CherryPy", "faker": "Faker",
    "factory": "factory-boy", "babel": "Babel", "charset_normalizer": "charset-normalizer",
    "cython": "Cython", "IPython": "ipython", "cairo": "pycairo", "cairosvg": "CairoSVG",
    "shapely": "Shapely", "osgeo": "GDAL", "igraph": "python-igraph",
    "whisper": "openai-whisper", "llama_cpp": "llama-cpp-python",
    "sentence_transformers": "sentence-transformers", "faiss": "faiss-cpu",
    "huggingface_hub": "huggingface-hub", "face_recognition": "face-recognition",
    "pygetwindow": "PyGetWindow", "bluetooth": "PyBluez", "can": "python-can",
    "RPi": "RPi.GPIO", "board": "adafruit-blinka", "paho": "paho-mqtt",
    "newspaper": "newspaper3k", "readability": "readability-lxml",
    "unidecode": "Unidecode", "antlr4": "antlr4-python3-runtime",
    "apscheduler": "APScheduler", "send2trash": "Send2Trash", "argon2": "argon2-cffi",
    "stable_baselines3": "stable-baselines3", "sb3_contrib": "sb3-contrib",
    "pygame_gui": "pygame-gui", "PySimpleGUI": "PySimpleGUI", "eel": "Eel",
    "webview": "pywebview", "PyInstaller": "pyinstaller", "nuitka": "Nuitka",
    "cx_Freeze": "cx-Freeze", "sphinx": "Sphinx", "docstring_parser": "docstring-parser",
    "googleapiclient": "google-api-python-client", "google_auth_oauthlib": "google-auth-oauthlib",
    "google.protobuf": "protobuf", "google.oauth2": "google-auth", "google.auth": "google-auth",
    "google.generativeai": "google-generativeai", "google.genai": "google-genai",
    "google.cloud.storage": "google-cloud-storage", "google.cloud.bigquery": "google-cloud-bigquery",
    "google.cloud.firestore": "google-cloud-firestore", "google.cloud.pubsub": "google-cloud-pubsub",
    "google.cloud.vision": "google-cloud-vision", "google.cloud.translate": "google-cloud-translate",
    "google.cloud.speech": "google-cloud-speech", "google.cloud.texttospeech": "google-cloud-texttospeech",
    "azure.storage.blob": "azure-storage-blob", "azure.identity": "azure-identity",
    "azure.keyvault.secrets": "azure-keyvault-secrets", "azure.cosmos": "azure-cosmos",
    "ruamel.yaml": "ruamel.yaml", "zope.interface": "zope.interface",
    "backports.zoneinfo": "backports.zoneinfo", "jaraco.text": "jaraco.text",
    "win32api": "pywin32", "win32con": "pywin32", "win32gui": "pywin32", "win32com": "pywin32",
    "win32file": "pywin32", "win32process": "pywin32", "win32clipboard": "pywin32",
    "win32service": "pywin32", "win32serviceutil": "pywin32", "win32event": "pywin32",
    "win32ui": "pywin32", "win32print": "pywin32", "win32timezone": "pywin32",
    "win32crypt": "pywin32", "win32pipe": "pywin32", "pythoncom": "pywin32",
    "pywintypes": "pywin32", "winerror": "pywin32",
    "tensorflow_hub": "tensorflow-hub", "tf_keras": "tf-keras", "sklearn_crfsuite": "sklearn-crfsuite",
    "lightning": "lightning", "pytorch_lightning": "pytorch-lightning",
    "Bio": "biopython", "mne": "mne", "vtkmodules": "vtk", "pyvista": "pyvista",
    "open3d": "open3d", "pptx_template": "python-pptx-templater",
}

# Top-level packages shared by many distributions; resolve these by dotted name.
NAMESPACE_ROOTS = {"google", "azure", "zope", "backports", "jaraco", "ruamel", "sphinxcontrib"}

# Standard-library modules that pip cannot provide and deserve an explanation.
STDLIB_HINTS = {
    "tkinter": "Tkinter isn't in this Python. Reinstall Python with 'tcl/tk and IDLE' "
               "ticked (Viper's Python download includes it).",
    "_tkinter": "Tkinter isn't in this Python. Reinstall Python with 'tcl/tk and IDLE' ticked.",
    "sqlite3": "This Python was built without sqlite3.",
    "ssl": "This Python was built without OpenSSL support.",
}

_IMPORT_ERRORS = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}


@dataclass
class ImportRef:
    module: str        # name to probe with importlib (top-level or namespace-dotted)
    line: int          # 1-based
    optional: bool = False


@dataclass
class ResolvedPackage:
    module: str
    dist: str
    exists: bool | None = None   # None = PyPI unreachable
    version: str = ""
    summary: str = ""
    lines: list[int] = field(default_factory=list)


def _handler_names(handler: ast.ExceptHandler) -> set[str]:
    if handler.type is None:
        return {"BaseException"}
    nodes = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    names = set()
    for n in nodes:
        if isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            names.add(n.attr)
    return names


def _is_guard(test: ast.AST) -> bool:
    """`if TYPE_CHECKING:` / platform checks: imports inside aren't hard requirements."""
    src = ast.dump(test)
    return any(k in src for k in ("TYPE_CHECKING", "platform", "'os', ctx=Load()), attr='name'",
                                  "version_info"))


def _probe_name(module: str) -> str:
    parts = module.split(".")
    if parts[0] in NAMESPACE_ROOTS:
        return ".".join(parts[:3])
    return parts[0]


def extract_imports(source: str) -> list[ImportRef]:
    """Absolute imports in ``source``. Falls back to a regex when it doesn't parse."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return _regex_imports(source)

    found: dict[str, ImportRef] = {}

    def add(name: str, line: int, optional: bool) -> None:
        if not name or name == "__future__":
            return
        prev = found.get(name)
        if prev is None or (prev.optional and not optional):
            found[name] = ImportRef(name, line, optional)

    def visit(nodes, optional: bool) -> None:
        for node in nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    add(_probe_name(alias.name), node.lineno, optional)
            elif isinstance(node, ast.ImportFrom):
                if node.level or not node.module:
                    continue
                parts = node.module.split(".")
                if parts[0] in NAMESPACE_ROOTS and len(parts) <= 2:
                    for alias in node.names:
                        if alias.name != "*":
                            add(_probe_name(f"{node.module}.{alias.name}"), node.lineno, optional)
                else:
                    add(_probe_name(node.module), node.lineno, optional)
            elif isinstance(node, ast.Try) or type(node).__name__ == "TryStar":
                guarded = any(_handler_names(h) & _IMPORT_ERRORS for h in node.handlers)
                visit(node.body, optional or guarded)
                for h in node.handlers:
                    visit(h.body, True)
                visit(node.orelse, optional)
                visit(node.finalbody, optional)
            elif isinstance(node, ast.If):
                guard = _is_guard(node.test)
                visit(node.body, optional or guard)
                visit(node.orelse, optional or guard)
            else:
                for name in ("body", "orelse", "finalbody", "handlers", "cases"):
                    child = getattr(node, name, None)
                    if isinstance(child, list):
                        visit(child, optional)

    visit(tree.body, False)
    return sorted(found.values(), key=lambda r: r.line)


_RX_IMPORT = re.compile(r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import|import\s+([A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*))",
                        re.M)


def _regex_imports(source: str) -> list[ImportRef]:
    out: dict[str, ImportRef] = {}
    for m in _RX_IMPORT.finditer(source):
        line = source.count("\n", 0, m.start()) + 1
        names = [m.group(1)] if m.group(1) else [n.strip() for n in m.group(2).split(",")]
        for n in names:
            probe = _probe_name(n)
            out.setdefault(probe, ImportRef(probe, line))
    return list(out.values())


def dist_for_module(module: str) -> str:
    """Best guess at the PyPI name for an import name (longest dotted match wins)."""
    parts = module.split(".")
    for i in range(len(parts), 0, -1):
        key = ".".join(parts[:i])
        if key in MODULE_TO_DIST:
            return MODULE_TO_DIST[key]
    if parts[0] in NAMESPACE_ROOTS:
        return "-".join(parts)
    return parts[0]


_RX_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def requirement_name(spec: str) -> str | None:
    spec = spec.split("#", 1)[0].strip()
    if not spec or spec.startswith(("-", "git+", "http:", "https:", "file:", ".", "/")):
        return None
    if " @ " in spec:
        spec = spec.split(" @ ", 1)[0]
    m = _RX_REQ_NAME.match(spec)
    return m.group(1) if m else None


def parse_requirements(text: str) -> list[str]:
    names = []
    for raw in text.splitlines():
        name = requirement_name(raw)
        if name and name not in names:
            names.append(name)
    return names


_RX_PEP723 = re.compile(r"(?m)^# /// script\s*$\s(?P<content>(^#(| .*)$\s)+)^# ///\s*$")


def inline_script_dependencies(source: str) -> list[str]:
    """PEP 723 ``# /// script`` dependency names."""
    m = _RX_PEP723.search(source)
    if not m:
        return []
    body = "\n".join(line[2:] if line.startswith("# ") else line[1:]
                     for line in m.group("content").splitlines())
    try:
        import tomllib
        data = tomllib.loads(body)
    except Exception:  # noqa: BLE001 - malformed block is simply ignored
        return []
    return [n for n in (requirement_name(d) for d in data.get("dependencies", []) if isinstance(d, str)) if n]


# ---------------------------------------------------------------- PyPI lookups
_pypi_cache: dict[str, dict | None] = {}
USER_AGENT = "ViperIDE (+https://pypi.org)"


def pypi_info(name: str, timeout: float = 8.0) -> dict | None:
    """PyPI metadata for ``name``; None when the project doesn't exist.

    Raises OSError when PyPI can't be reached so callers can tell "missing"
    from "offline".
    """
    key = name.lower()
    if key in _pypi_cache:
        return _pypi_cache[key]
    req = urllib.request.Request(f"https://pypi.org/pypi/{name}/json", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            _pypi_cache[key] = None
            return None
        raise
    info = data.get("info", {})
    releases = [v for v, files in data.get("releases", {}).items() if files]
    result = {
        "name": info.get("name", name),
        "version": info.get("version", ""),
        "summary": info.get("summary") or "",
        "home": info.get("home_page") or (info.get("project_urls") or {}).get("Homepage", ""),
        "requires_python": info.get("requires_python") or "",
        "releases": releases,
    }
    _pypi_cache[key] = result
    return result


def resolve_missing(modules: list[str]) -> list[ResolvedPackage]:
    """Map missing import names to distributions and confirm each exists on PyPI."""
    by_dist: dict[str, ResolvedPackage] = {}
    for module in modules:
        dist = dist_for_module(module)
        key = dist.lower()
        if key in by_dist:
            continue
        pkg = ResolvedPackage(module=module, dist=dist)
        try:
            info = pypi_info(dist)
        except OSError:
            info, pkg.exists = None, None
        else:
            pkg.exists = info is not None
        if info:
            pkg.dist, pkg.version, pkg.summary = info["name"], info["version"], info["summary"]
        by_dist[key] = pkg
    return list(by_dist.values())


_RX_MODULE_NOT_FOUND = re.compile(r"ModuleNotFoundError: No module named '([\w.]+)'")


def missing_module_from_output(text: str) -> str | None:
    m = _RX_MODULE_NOT_FOUND.search(text)
    return _probe_name(m.group(1)) if m else None
