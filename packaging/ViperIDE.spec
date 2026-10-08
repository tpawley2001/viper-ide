# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Viper IDE. Build with:  python -m PyInstaller --noconfirm packaging/ViperIDE.spec
# Paths are plain Python strings here, so nothing gets re-quoted by a shell.
import fnmatch
import os
import re
import sys

from PyInstaller.utils.hooks import collect_data_files

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
with open(os.path.join(ROOT, "viper_ide", "__init__.py"), encoding="utf-8") as f:
    VERSION = re.search(r'__version__ = "([^"]+)"', f.read()).group(1)

datas = [
    # Helpers run inside the user's interpreter, so they must exist as real files.
    (os.path.join(ROOT, "viper_ide", "helpers"), os.path.join("viper_ide", "helpers")),
    (os.path.join(ROOT, "viper_ide", "resources"), os.path.join("viper_ide", "resources")),
]
# Jedi runs a helper process in the *user's* Python: `python jedi/inference/compiled/
# subprocess/__main__.py`, importing jedi and parso from disk. Nothing in the IDE imports
# that __main__.py, so module-level collection drops it; ship both source trees whole.
datas += collect_data_files("jedi", include_py_files=True) + collect_data_files("parso", include_py_files=True)

a = Analysis(
    [os.path.join(ROOT, "packaging", "launcher.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=["pyflakes", "pyflakes.api", "pydoc", "pydoc_data.topics", "tomllib", "PyQt6.Qsci",
                   "PyQt6.QtNetwork", "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets",
                   "viper_ide.app", "viper_ide.selftest"],
    # Qt WebEngine stays in: the Microsoft Copilot window (copilotweb.py) is a browser.
    excludes=["tkinter", "_tkinter", "PyQt6.QtMultimedia", "PyQt6.QtPdf", "PyQt6.QtBluetooth", "PyQt6.Qt3DCore"],
    noarchive=False,
)

# Qt WebEngine drags in Chromium debug data, 50+ UI languages and Qt Quick/3D modules that a plain
# browser window never loads (~170 MB unpacked). The selftest's copilot_window check catches over-pruning.
_DROP = [
    "pyqt6/qt6/resources/*.debug.*", "pyqt6/qt6/resources/qtwebengine_devtools_resources.pak",
    "pyqt6/qt6/qml/*",
    *(f"pyqt6/qt6/bin/qt6{m}*.dll" for m in (
        "quick3d", "quickcontrols2", "quickdialogs", "quicktemplates2", "quickparticles", "quickeffects",
        "quicktimeline", "quickvectorimage", "pdfquick", "multimedia", "spatialaudio", "remoteobjects", "test",
        "shadertools", "sensors", "texttospeech", "websockets", "statemachine", "positioningquick")),
]
_KEEP_LOCALES = {"en-us.pak", "en-gb.pak"}


def _keep(entry):
    dest = entry[0].replace("\\", "/").lower()
    if "/qtwebengine_locales/" in dest:
        return dest.rsplit("/", 1)[-1] in _KEEP_LOCALES
    return not any(fnmatch.fnmatch(dest, pat) for pat in _DROP)


a.binaries = [e for e in a.binaries if _keep(e)]
a.datas = [e for e in a.datas if _keep(e)]
pyz = PYZ(a.pure)

version_info = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                     VarFileInfo, VarStruct, VSVersionInfo)

    nums = tuple(int(x) for x in (VERSION.split(".") + ["0", "0", "0"])[:4])
    version_info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "Hillyard Tech"),
                StringStruct("FileDescription", "Viper IDE"),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", "ViperIDE"),
                StringStruct("LegalCopyright", "Copyright (c) 2026 Hillyard Tech"),
                StringStruct("OriginalFilename", "ViperIDE.exe"),
                StringStruct("ProductName", "Viper IDE"),
                StringStruct("ProductVersion", VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ViperIDE",
    console=False,
    icon=os.path.join(ROOT, "packaging", "viper.ico"),
    version=version_info,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ViperIDE", upx=False)
