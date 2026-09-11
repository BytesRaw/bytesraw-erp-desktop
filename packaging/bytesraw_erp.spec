# -*- mode: python ; coding: utf-8 -*-
"""One-folder PyInstaller build for Bytesraw ERP (roadmap M7.1).

Run it from the repository root::

    .venv/Scripts/python.exe -m PyInstaller packaging/bytesraw_erp.spec --noconfirm

and the app lands in ``dist/BytesrawERP/``.

Why one folder and not ``--onefile``
------------------------------------
``--onefile`` is a self-extracting archive: it unpacks the whole bundle into
``%TEMP%\_MEIxxxxxx`` before the first line of Python runs. QtWebEngine makes
that untenable - ``QtWebEngineCore.dll`` alone is over 100 MB, so every launch
would pay seconds of extraction on a till's disk, Chromium would have to spawn
its sandboxed ``QtWebEngineProcess.exe`` out of a randomly named temp
directory, and the extract-and-execute pattern is the one antivirus engines
flag. The installer is what turns the folder into a single thing to hand over.

Why UPX is off
--------------
Compressing Qt's DLLs is a known way to break them, and a UPX-packed binary is
itself a heuristic antivirus flags. The installer's LZMA does the compressing.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR.parent
SRC = ROOT / "src"

# The constants module is the single source of truth for the product name and
# version, and it imports nothing but ``typing`` - safe to read at spec time.
sys.path.insert(0, str(SRC))
from bytesraw_erp.constants import APP_NAME, APP_VERSION, ORG_NAME  # noqa: E402

#: Basename of the executable and of the folder it sits in. No space: this
#: string ends up in paths that build scripts and the installer quote by hand.
EXE_NAME = "BytesrawERP"

ICON = SRC / "bytesraw_erp" / "resources" / "icon.ico"


def _version_tuple(version: str) -> tuple[int, int, int, int]:
    """``"0.1.7"`` -> ``(0, 1, 7, 0)``. Windows wants exactly four parts."""
    parts = [int(part) for part in version.split(".") if part.isdigit()]
    parts = (parts + [0, 0, 0, 0])[:4]
    return tuple(parts)


def _write_version_resource() -> str:
    """Generate the VERSIONINFO resource Windows shows on the exe's Properties.

    Without it the file's Details tab is blank, which looks like malware to an
    administrator and gives an installer nothing to compare on an upgrade.
    """
    numbers = _version_tuple(APP_VERSION)
    info = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=numbers,
            prodvers=numbers,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,  # VOS_NT_WINDOWS32
            fileType=0x1,  # VFT_APP
            subtype=0x0,
        ),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",  # US English, Unicode
                        [
                            StringStruct("CompanyName", ORG_NAME),
                            StringStruct("FileDescription", f"{APP_NAME} - desktop shell for Odoo"),
                            StringStruct("FileVersion", APP_VERSION),
                            StringStruct("InternalName", EXE_NAME),
                            StringStruct("LegalCopyright", f"Copyright (c) {ORG_NAME}"),
                            StringStruct("OriginalFilename", f"{EXE_NAME}.exe"),
                            StringStruct("ProductName", APP_NAME),
                            StringStruct("ProductVersion", APP_VERSION),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )
    target = SPEC_DIR / "file_version_info.txt"
    # A PyInstaller version file is the repr of this structure, read back with
    # eval(), so str() round-trips exactly.
    target.write_text(str(info), encoding="utf-8")
    return str(target)


analysis = Analysis(
    [str(SPEC_DIR / "entry.py")],
    pathex=[str(SRC)],
    binaries=[],
    # ``core.resources`` reads these through importlib.resources, which finds
    # nothing PyInstaller was not told to carry - a static import scan cannot
    # see a read_bytes() call.
    datas=[
        (str(SRC / "bytesraw_erp" / "resources"), "bytesraw_erp/resources"),
    ],
    # keyring finds its Windows backend through entry points, which need the
    # package metadata on disk. PyInstaller's own hook-keyring.py copies that
    # and collects keyring.backends.*, so nothing is needed here - but the
    # failure mode is silent (the vault simply appears empty), which is why
    # build.ps1's smoke test signs in rather than only launching.
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Qt modules nothing in src/ imports, directly or transitively. Do not add
    # QtQml, QtQuick, QtWebChannel, QtPositioning, QtOpenGL or QtNetwork here:
    # QtWebEngine is built on them and drops them on the floor at runtime with
    # a blank web view rather than an import error.
    excludes=[
        "PySide6.Qt3DAnimation",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DExtras",
        "PySide6.Qt3DInput",
        "PySide6.Qt3DLogic",
        "PySide6.Qt3DRender",
        "PySide6.QtBluetooth",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtDesigner",
        "PySide6.QtGraphs",
        "PySide6.QtHelp",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtNfc",
        "PySide6.QtQuick3D",
        "PySide6.QtRemoteObjects",
        "PySide6.QtScxml",
        "PySide6.QtSensors",
        "PySide6.QtSerialPort",
        "PySide6.QtSpatialAudio",
        "PySide6.QtSql",
        "PySide6.QtStateMachine",
        "PySide6.QtTest",
        "PySide6.QtTextToSpeech",
        "PySide6.QtUiTools",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtWebSockets",
        "tkinter",
        "pytest",
        "_pytest",
        "pytestqt",
    ],
    noarchive=False,
    optimize=0,
)

# PySide6's wheel ships debug-build copies of Chromium's resource packs beside
# the release ones. A release build never reads them, and they are not small:
# measured at 74.8 MB, of which qtwebengine_devtools_resources.debug.pak alone
# is 72.3. Dropping them took dist/ from 571 MB to the figure build.ps1 prints.
_DEBUG_PAK = ".debug.pak"
analysis.datas = [item for item in analysis.datas if not item[0].endswith(_DEBUG_PAK)]
analysis.binaries = [item for item in analysis.binaries if not item[0].endswith(_DEBUG_PAK)]

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # A GUI app: no console window, and no stdout/stderr either. Harmless
    # here because core.logging_setup only attaches a stream handler when
    # sys.stderr is not None, and always logs to a file besides.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON),
    version=_write_version_resource(),
)

COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=EXE_NAME,
)
