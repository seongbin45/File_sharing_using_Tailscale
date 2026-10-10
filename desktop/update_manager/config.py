"""Constants for the silent background updater."""

from __future__ import annotations

GITHUB_OWNER = "seongbin45"
GITHUB_REPO = "File_sharing_using_Tailscale"
API_LATEST = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)

# Poll interval (seconds).
INTERVAL_SEC = 600

# The one release asset this updater ever fetches. Naru is a PyInstaller
# --onefile build, so the raw exe is already the whole artifact - no zip.
ASSET_NAME = "Naru.exe"

# Main window title (exact - app/main_window.py's setWindowTitle("나루")).
# The wizard's "나루 설정" dialog is a separate, non-main window and is
# intentionally not matched here.
MAIN_WINDOW_TITLE = "나루"

# Process image to stop before replacing files.
TSBACKUP_EXE_NAME = "Naru.exe"

# Never kill this (ourselves).
PROTECTED_EXE_NAMES = frozenset({"Naru_update_manager.exe"})

# Inno AppId, copied verbatim from desktop/build/naru.iss - fixed
# forever, never regenerate.
INNO_APP_ID = "{8F2C4A61-9E3B-4C7A-BD5E-1A6F3D9E2B47}"

USER_AGENT = "Naru-UpdateManager/0.1.0"
