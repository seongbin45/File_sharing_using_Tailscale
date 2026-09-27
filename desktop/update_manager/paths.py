"""Locate the installed TsBackup application directory (before any update).

Unlike CloneUp, there is no separate manager_install_dir(): the update
manager's own exe/launchers live in the same {app} folder as TsBackup.exe
(see the plan's "Install location" section for why that's safe here -
the apply step only ever replaces the single named file TsBackup.exe,
never wipes the folder, so there's nothing for the update manager's own
files to collide with)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import winreg
except ImportError:  # non-Windows (dev/test sandbox) - registry lookups
    winreg = None  # are guarded by sys.platform checks before any use

from update_manager.config import INNO_APP_ID

UM_EXE_NAME = "TsBackup_update_manager.exe"
UM_BAT_NAME = "TsBackup_update_manager.bat"
UM_VBS_NAME = "TsBackup_update_manager_hidden.vbs"


def _local_app_data() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base)
    return Path.home() / "AppData" / "Local"


def manager_hidden_vbs_path(install_dir: Path) -> Path:
    """Login shortcut / one-time [Run] launch should run this VBS (window
    style 0), not the exe directly - see the plan's "Hidden background
    execution" section for why."""
    return install_dir / UM_VBS_NAME


def pending_root(install_dir: Path) -> Path:
    """Persistent download staging (Tier 2: survives a defer-while-UI-open
    across ticks). No ACL tuning needed - single user owns this install."""
    root = install_dir / "UpdateManager" / "pending"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def status_root(install_dir: Path) -> Path:
    """Status tree for the tray's "check now" polling."""
    root = install_dir / "UpdateManager" / "status"
    root.mkdir(parents=True, exist_ok=True)
    (root / "runs").mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _looks_like_tsbackup_dir(folder: Path) -> bool:
    """True if folder appears to be a TsBackup onefile install (the exe is
    the whole artifact - no _internal/onedir tree to check)."""
    try:
        if not folder.is_dir():
            return False
    except OSError:
        return False
    return (folder / "TsBackup.exe").is_file()


def _uninstall_display_icon_dir(icon_path: str) -> Path | None:
    # UninstallDisplayIcon={app}\TsBackup.exe (tsbackup.iss)
    p = Path(icon_path.strip().strip('"'))
    if p.suffix.lower() in {".ico", ".exe"}:
        parent = p.parent
        if _looks_like_tsbackup_dir(parent):
            return parent.resolve()
    return None


def _parse_uninstall_key(key) -> Path | None:
    for value_name in ("InstallLocation", "Inno Setup: App Path"):
        try:
            val, _ = winreg.QueryValueEx(key, value_name)
            p = Path(str(val).strip().strip('"'))
            if _looks_like_tsbackup_dir(p):
                return p.resolve()
        except OSError:
            pass
    try:
        icon, _ = winreg.QueryValueEx(key, "DisplayIcon")
        got = _uninstall_display_icon_dir(str(icon))
        if got is not None:
            return got
    except OSError:
        pass
    try:
        uni, _ = winreg.QueryValueEx(key, "UninstallString")
        p = Path(str(uni).strip().strip('"').split(" /")[0].strip('"'))
        if p.name.lower().startswith("unins") and _looks_like_tsbackup_dir(p.parent):
            return p.parent.resolve()
    except OSError:
        pass
    return None


def _read_uninstall_install_location() -> Path | None:
    """Read Inno / ARP uninstall keys for TsBackup InstallLocation or icon
    path. Checks both HKLM (admin/per-machine install) and HKCU (per-user
    install, since tsbackup.iss uses PrivilegesRequired=lowest)."""
    if sys.platform != "win32":
        return None
    roots = (
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
        (
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
    )
    guid = INNO_APP_ID.strip("{}")
    braced = f"{{{guid}}}"
    app_id_keys = {
        INNO_APP_ID,
        guid,
        braced,
        f"{braced}_is1",
        f"{guid}_is1",
    }
    for hive, base in roots:
        for key_name in app_id_keys:
            try:
                with winreg.OpenKey(hive, f"{base}\\{key_name}") as key:
                    got = _parse_uninstall_key(key)
                    if got is not None:
                        return got
            except OSError:
                pass
        try:
            with winreg.OpenKey(hive, base) as root:
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(root, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with winreg.OpenKey(root, name) as key:
                            try:
                                display, _ = winreg.QueryValueEx(key, "DisplayName")
                            except OSError:
                                continue
                            if not str(display).strip().startswith("TsBackup"):
                                continue
                            got = _parse_uninstall_key(key)
                            if got is not None:
                                return got
                    except OSError:
                        continue
        except OSError:
            continue
    return None


def _candidate_dirs() -> list[Path]:
    local = _local_app_data()
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    return [
        local / "Programs" / "TsBackup",
        Path(pf) / "TsBackup",
        Path(pf86) / "TsBackup",
        local / "TsBackup",  # mistaken / legacy
    ]


def find_tsbackup_install_dir() -> Path | None:
    """
    Resolve the folder that contains TsBackup.exe.

    Order:
      1. ``TSBACKUP_INSTALL_DIR`` env (tests / override)
      2. Uninstall / ARP registry (Inno AppId + DisplayName) - checks both
         HKLM (admin install) and HKCU (per-user install)
      3. Well-known default paths with ``TsBackup.exe`` present
    """
    env = os.environ.get("TSBACKUP_INSTALL_DIR", "").strip()
    if env:
        p = Path(env).expanduser()
        if _looks_like_tsbackup_dir(p):
            return p.resolve()
        return None

    from_reg = _read_uninstall_install_location()
    if from_reg is not None:
        return from_reg

    for cand in _candidate_dirs():
        if _looks_like_tsbackup_dir(cand):
            return cand.resolve()
    return None
