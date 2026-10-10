"""Windows를 켤 때 나루도 켜기 - the per-user Startup shortcut.

The installer creates {userstartup}\\Naru.lnk when its "Windows 시작 시
자동 실행" task is ticked (build/naru.iss); this module reads and toggles
that same file, so the setting and the installer never disagree.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

LINK_NAME = "Naru.lnk"


def startup_dir() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def link_path() -> Path:
    return startup_dir() / LINK_NAME


def available() -> bool:
    """Only an installed build has an exe for the shortcut to point at."""
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def is_enabled() -> bool:
    return link_path().exists()


def set_enabled(on: bool) -> bool:
    """True if the shortcut now matches `on`."""
    path = link_path()
    if not on:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return False
        return True
    if not available():
        return False
    exe = sys.executable
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:NARU_LNK);"
        "$s.TargetPath=$env:NARU_EXE;$s.WorkingDirectory=(Split-Path $env:NARU_EXE);$s.Save()"
    )
    env = dict(os.environ, NARU_LNK=str(path), NARU_EXE=exe)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       env=env, capture_output=True, timeout=20,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return False
    return path.exists()
