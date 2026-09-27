"""Constants for the silent background updater."""

from __future__ import annotations

GITHUB_OWNER = "seongbin45"
GITHUB_REPO = "File_sharing_using_Tailscale"
API_LATEST = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)

# Poll interval (seconds).
INTERVAL_SEC = 600

# The one release asset this updater ever fetches. TsBackup is a PyInstaller
# --onefile build, so the raw exe is already the whole artifact - no zip.
ASSET_NAME = "TsBackup.exe"

# Main window title used by TsBackup (exact - app/main_window.py's
# setWindowTitle("TS Backup")). The wizard's "TS Backup 설정" dialog is a
# separate, non-main window and is intentionally not matched here.
MAIN_WINDOW_TITLE = "TS Backup"

# Process image to stop before replacing files.
TSBACKUP_EXE_NAME = "TsBackup.exe"

# Never kill this (ourselves).
PROTECTED_EXE_NAMES = frozenset({"TsBackup_update_manager.exe"})

# Inno AppId, copied verbatim from desktop/build/tsbackup.iss - fixed
# forever, never regenerate.
INNO_APP_ID = "{8F2C4A61-9E3B-4C7A-BD5E-1A6F3D9E2B47}"

USER_AGENT = "TsBackup-UpdateManager/0.1.0"
