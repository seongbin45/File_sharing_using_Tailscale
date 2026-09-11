# PyInstaller spec for TsBackup.
#
#   cd desktop
#   pyinstaller build/tsbackup.spec
#
# Produces dist/TsBackup.exe (one file). Build it ON WINDOWS - a PyInstaller
# binary is platform-specific, so a Linux build is a Linux ELF, not the .exe
# the release needs.
#
# Notes that save a broken build:
#  * The icon is painted in code (app/icons.py), so there is no .ico/.png to
#    bundle and no runtime path to get wrong in a --onefile temp dir.
#  * py7zr pulls in compression backends that PyInstaller's hooks usually find,
#    but if a "no module named _lzma / brotli / zstandard" appears at runtime,
#    add it to hiddenimports below.
#  * console=False so no black window flashes when Task Scheduler launches it.

# -*- mode: python ; coding: utf-8 -*-
import os
import sys

block_cipher = None

here = os.path.abspath(os.getcwd())            # run from desktop/

a = Analysis(
    ["app/main.py"],
    pathex=[here],
    binaries=[],
    datas=[],
    hiddenimports=[
        "tsbackup.transports.taildrop",
        "tsbackup.transports.sftp",
        "tsbackup.transports.http_push",
        # py7zr codec backends, in case a hook misses one:
        "_lzma", "bz2", "zlib",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Trim Qt modules this app never touches; keeps the binary smaller and
        # the build faster. Remove an entry if something fails to import.
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts",
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="TsBackup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,            # no console window on launch
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version_file=None,
)
