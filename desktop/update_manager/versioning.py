"""Version parse / compare for installed TsBackup vs release tags."""

from __future__ import annotations

import re
from pathlib import Path

_SEMVER = re.compile(
    r"v?\s*(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?",
    re.IGNORECASE,
)


def normalize_version(raw: str | None) -> tuple[int, int, int] | None:
    if not raw:
        return None
    m = _SEMVER.search(str(raw).strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def version_tuple_to_str(v: tuple[int, int, int]) -> str:
    return f"{v[0]}.{v[1]}.{v[2]}"


def is_newer(remote: tuple[int, int, int], local: tuple[int, int, int]) -> bool:
    return remote > local


def read_installed_version(install_dir: Path) -> tuple[int, int, int] | None:
    """Prefer the plain ``VERSION`` file the installer drops beside
    TsBackup.exe; then the exe's own PE FileVersion; then ARP
    DisplayVersion."""
    ver_file = install_dir / "VERSION"
    try:
        if ver_file.is_file():
            got = normalize_version(ver_file.read_text(encoding="utf-8").strip())
            if got:
                return got
    except OSError:
        pass
    pe = _read_exe_file_version(install_dir / "TsBackup.exe")
    if pe is not None and pe != (0, 0, 0):
        return pe
    arp = _read_arp_display_version()
    if arp is not None:
        return arp
    return pe


def _read_arp_display_version() -> tuple[int, int, int] | None:
    """Windows Apps & Features DisplayVersion for TsBackup (Inno)."""
    import sys

    if sys.platform != "win32":
        return None
    try:
        import winreg

        from update_manager.config import INNO_APP_ID
    except Exception:
        return None
    roots = (
        (
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
    )
    guid = INNO_APP_ID.strip("{}")
    braced = f"{{{guid}}}"
    keys = {
        INNO_APP_ID,
        braced,
        f"{braced}_is1",
        f"{guid}_is1",
    }
    for hive, base in roots:
        for key_name in keys:
            try:
                with winreg.OpenKey(hive, f"{base}\\{key_name}") as key:
                    val, _ = winreg.QueryValueEx(key, "DisplayVersion")
                    got = normalize_version(str(val))
                    if got:
                        return got
            except OSError:
                continue
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
                            val, _ = winreg.QueryValueEx(key, "DisplayVersion")
                            got = normalize_version(str(val))
                            if got:
                                return got
                    except OSError:
                        continue
        except OSError:
            continue
    return None


def _read_exe_file_version(exe: Path) -> tuple[int, int, int] | None:
    if not exe.is_file():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        size = ctypes.windll.version.GetFileVersionInfoSizeW(str(exe), None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(str(exe), 0, size, buf):
            return None
        p = ctypes.c_void_p()
        length = wintypes.UINT()
        if not ctypes.windll.version.VerQueryValueW(
            buf, r"\\", ctypes.byref(p), ctypes.byref(length)
        ):
            return None

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", wintypes.DWORD),
                ("dwStrucVersion", wintypes.DWORD),
                ("dwFileVersionMS", wintypes.DWORD),
                ("dwFileVersionLS", wintypes.DWORD),
                ("dwProductVersionMS", wintypes.DWORD),
                ("dwProductVersionLS", wintypes.DWORD),
                ("dwFileFlagsMask", wintypes.DWORD),
                ("dwFileFlags", wintypes.DWORD),
                ("dwFileOS", wintypes.DWORD),
                ("dwFileType", wintypes.DWORD),
                ("dwFileSubtype", wintypes.DWORD),
                ("dwFileDateMS", wintypes.DWORD),
                ("dwFileDateLS", wintypes.DWORD),
            ]

        info = ctypes.cast(p, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        major = info.dwFileVersionMS >> 16
        minor = info.dwFileVersionMS & 0xFFFF
        build = info.dwFileVersionLS >> 16
        return int(major), int(minor), int(build)
    except Exception:
        return None
