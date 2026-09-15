# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Viper IDE. Build with:  python -m PyInstaller --noconfirm packaging/ViperIDE.spec
# Paths are plain Python strings here, so nothing gets re-quoted by a shell.
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
                   "PyQt6.QtNetwork", "viper_ide.app", "viper_ide.selftest"],
    excludes=["tkinter", "_tkinter", "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtQml",
              "PyQt6.QtQuick", "PyQt6.QtMultimedia", "PyQt6.QtPdf", "PyQt6.QtBluetooth", "PyQt6.Qt3DCore"],
    noarchive=False,
)
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
