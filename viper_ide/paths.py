"""Filesystem locations for bundled resources and per-user data."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

APP_DIR_NAME = "ViperIDE"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def package_dir() -> Path:
    if is_frozen():
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        return Path(base) / "viper_ide"
    return Path(__file__).resolve().parent


def helper(name: str) -> Path:
    """Scripts that run inside the *user's* interpreter, so they must be real files."""
    return package_dir() / "helpers" / name


def resource(name: str) -> Path:
    return package_dir() / "resources" / name


def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def _home_override() -> Path | None:
    v = os.environ.get("VIPER_IDE_HOME")
    return Path(v) if v else None


def config_dir() -> Path:
    if (o := _home_override()):
        return _ensure(o / "config")
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return _ensure(base / APP_DIR_NAME)


def data_dir() -> Path:
    if (o := _home_override()):
        return _ensure(o / "data")
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return _ensure(base / APP_DIR_NAME)


def pythons_dir() -> Path:
    """Interpreters Viper downloaded itself."""
    return _ensure(data_dir() / "pythons")


def tools_dir() -> Path:
    """Formatters etc. installed per interpreter version, outside the user's environments."""
    return _ensure(data_dir() / "tools")


def downloads_dir() -> Path:
    return _ensure(data_dir() / "downloads")


_BUNDLE_VARS = ("QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "TCL_LIBRARY",
                "TK_LIBRARY", "PYTHONHOME", "SSL_CERT_FILE")


def child_env(extra: dict | None = None) -> dict:
    """Environment for the user's programs, minus anything the frozen IDE injected.

    A PyInstaller build puts its own Qt DLLs on PATH and sets _PYI_* variables;
    leaking those into a user's PyQt program (or their own PyInstaller exe)
    breaks it in confusing ways.
    """
    env = dict(os.environ)
    if is_frozen():
        bundle = os.path.normcase(os.path.abspath(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))))
        env["PATH"] = os.pathsep.join(
            p for p in env.get("PATH", "").split(os.pathsep)
            if p and not os.path.normcase(os.path.abspath(p)).startswith(bundle))
        for key in list(env):
            if key.startswith(("_PYI_", "_MEIPASS")) or key in _BUNDLE_VARS:
                env.pop(key, None)
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    env.pop("VIPER_IDE_HOME", None)
    env.update(extra or {})
    return env


def subprocess_flags() -> dict:
    """Keep console windows from flashing up when the GUI shells out on Windows."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}
