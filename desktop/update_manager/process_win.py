"""Detect TsBackup's main window; kill TsBackup.exe process tree (never
the update manager itself)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from update_manager.config import (
    MAIN_WINDOW_TITLE,
    PROTECTED_EXE_NAMES,
    TSBACKUP_EXE_NAME,
)

log = logging.getLogger("tsbackup_update_manager")


def main_window_visible() -> bool:
    """True if a visible top-level window titled exactly MAIN_WINDOW_TITLE
    exists. The wizard's "TS Backup 설정" dialog does not match this and
    is not treated as "the app is open" - it's mandatory-first-run only
    and never coexists with an update tick worth deferring for."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found = ctypes.c_int(0)

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value == MAIN_WINDOW_TITLE:
                found.value = 1
                return False
            return True

        user32.EnumWindows(_enum, 0)
        return bool(found.value)
    except Exception as e:
        log.warning("EnumWindows failed: %s", e)
        return False


def _create_no_window_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def kill_tsbackup_processes(*, wait_sec: float = 30.0) -> bool:
    """
    Force-stop TsBackup.exe and its children. Never targets the update
    manager itself.

    Returns True if no TsBackup.exe remains (or none existed).
    """
    if sys.platform != "win32":
        return True
    me = Path(sys.executable).name.lower()
    if me in {n.lower() for n in PROTECTED_EXE_NAMES}:
        pass

    flags = _create_no_window_flags()
    try:
        subprocess.run(
            ["taskkill", "/IM", TSBACKUP_EXE_NAME, "/T", "/F"],
            capture_output=True,
            text=False,
            timeout=60,
            creationflags=flags,
            check=False,
        )
    except Exception as e:
        log.warning("taskkill error: %s", e)

    deadline = time.time() + max(1.0, wait_sec)
    while time.time() < deadline:
        if not _tsbackup_exe_running():
            return True
        time.sleep(0.4)
    still = _tsbackup_exe_running()
    if still:
        log.error("TsBackup.exe still running after kill wait")
    return not still


def _tsbackup_exe_running() -> bool:
    """True if TsBackup.exe appears in tasklist. Uses raw bytes (not
    text=True): a Korean-locale Windows tasklist is cp949 and UTF-8
    decoding can raise, which would otherwise make the kill-wait exit
    early on a false "not running" read."""
    flags = _create_no_window_flags()
    try:
        r = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {TSBACKUP_EXE_NAME}", "/NH"],
            capture_output=True,
            text=False,
            timeout=30,
            creationflags=flags,
            check=False,
        )
        out = (r.stdout or b"").lower()
        needle = TSBACKUP_EXE_NAME.lower().encode("ascii")
        return needle in out
    except Exception:
        return False


def is_tray_autostart_registered() -> bool:
    """Whether the *main app's* own login-autostart is on - the Startup-
    folder shortcut tsbackup.iss's [Icons] "startupicon" task creates
    (Name: "{userstartup}\\{#MyAppName}"), not this update manager's own
    autostart shortcut. Used to decide whether to relaunch TsBackup after
    applying an update - if the person never asked for autostart, don't
    add a tray icon they didn't have running before."""
    if sys.platform != "win32":
        return False
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return False
    shortcut = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "TsBackup.lnk"
    return shortcut.is_file()


def restart_tsbackup_tray(install_dir: Path) -> None:
    """Relaunch TsBackup plain, no CLI flags - unlike CloneUp, this app has
    no --tray switch. A bare launch already reproduces exactly what the
    Startup-folder shortcut does on a normal login: it goes straight to
    _gui(), and cfg.minimize_to_tray (the user's own saved preference)
    decides whether the window shows or starts hidden in the tray."""
    exe = install_dir / TSBACKUP_EXE_NAME
    if not exe.is_file():
        log.warning("cannot restart - missing %s", exe)
        return
    flags = _create_no_window_flags()
    try:
        subprocess.Popen(
            [str(exe)],
            cwd=str(install_dir),
            creationflags=flags,
            close_fds=True,
        )
        log.info("restarted TsBackup")
    except Exception as e:
        log.warning("restart failed: %s", e)
