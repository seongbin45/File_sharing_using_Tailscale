# PyInstaller spec for Naru_update_manager.
#
#   cd desktop
#   pyinstaller build/update_manager.spec
#
# Produces dist/Naru_update_manager.exe (one file), a fully independent
# background process from Naru.exe itself - see docs in
# update_manager/__init__.py and the plan this was built from.
#
# console=False: a GUI-subsystem exe with no console of its own - but it is
# still always launched through the hidden VBS wrapper
# (update_manager/launchers/Naru_update_manager_hidden.vbs), never
# directly from a shortcut/Task - CloneUp's own installer comment records
# that some Task-Scheduler/AV configurations still flash a console for a
# onefile exe launched directly at logon even when console=False, which the
# VBS wrapper avoids. Never bypass it.
#
# upx=False (unlike tsbackup.spec's upx=True): CloneUp's own
# docs/UPDATE_MANAGER.md documents that UPX-packed onefile updaters get
# quarantined by some Defender/AV configurations, which then looks like
# "the updater never got installed" - keep this exe unpacked.

# -*- mode: python ; coding: utf-8 -*-
import os

block_cipher = None

here = os.path.abspath(os.getcwd())            # run from desktop/

a = Analysis(
    [os.path.join(here, "update_manager", "__main__.py")],
    pathex=[here],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # This package has no Qt dependency at all - keep it that way, and
        # keep the build small by excluding it explicitly.
        "PySide6",
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
    name="Naru_update_manager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version_file=None,
    # Same reasoning as tsbackup.spec - a build-time-only .ico embedded into
    # this exe's own Windows resources, nothing this process loads at runtime.
    icon=os.path.join(here, "dist", "naru.ico"),
)
