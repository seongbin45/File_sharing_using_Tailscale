# PyInstaller spec for Naru (나루).
#
#   cd desktop
#   pyinstaller build/tsbackup.spec
#
# Produces dist/Naru.exe (one file). Build it ON WINDOWS - a PyInstaller
# binary is platform-specific, so a Linux build is a Linux ELF, not the .exe
# the release needs.
#
# Notes that save a broken build:
#  * The app's own window/tray icon is still painted in code (app/icons.py) -
#    no runtime path to get wrong in a --onefile temp dir. icon= below is a
#    separate thing: a build-time-only .ico (build/make_icon.py renders it
#    from that same code) embedded into this .exe's own Windows resources,
#    for Explorer/taskbar/Alt-Tab - it's never a runtime dependency.
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
    # Must be absolute: PyInstaller resolves a relative script path against
    # the .spec file's own directory (build/), not the cwd pathex uses, so
    # a bare "app/main.py" here looks for build/app/main.py and fails.
    [os.path.join(here, "app", "main.py")],
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
    name="Naru",
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
    # Absolute, same reasoning as the script path above (icon= is resolved
    # relative to the .spec file's own directory, not cwd, if given as a
    # bare relative string). build/make_icon.py writes this before this
    # spec runs (see desktop-release.yml).
    icon=os.path.join(here, "dist", "naru.ico"),
)
