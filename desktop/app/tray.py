"""System tray icon and menu.

The one screen the user specifically asked to have designed. The icon's state
dot mirrors the engine, so a glance at the tray says whether it is running
without opening the window. Its menu is the minimum needed to operate the app
while it lives in the tray: open, run now, pause/resume, quit.

Notifications are failure-only, and fire exactly once per incident (see
Engine.failed_after_retries) - there is no notification for a normal
success, including a success that follows a prior failure. showMessage()'s
messageClicked signal is reliable on Windows but not guaranteed on macOS or
under every Linux notification daemon, so the click action here is only a
convenience: the tray icon's "error" state is the persistent signal that
doesn't depend on the notification having been seen or clicked.
"""

from __future__ import annotations

import subprocess
import sys

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from tsbackup.engine_core import MAX_RETRIES
from update_manager.paths import UM_EXE_NAME, find_tsbackup_install_dir

from . import wording
from .icons import app_icon


def _find_update_manager_exe():
    """Module-level, not a Tray method: lets a test exercise this lookup
    without constructing a real QSystemTrayIcon (each one is a real OS
    resource - cheap to make one, not free to make several per test run)."""
    install_dir = find_tsbackup_install_dir()
    if install_dir is None:
        return None
    exe = install_dir / UM_EXE_NAME
    return exe if exe.is_file() else None


class Tray(QSystemTrayIcon):
    def __init__(self, window, engine, on_quit) -> None:
        super().__init__(app_icon("idle"))
        self.window = window
        self.engine = engine
        self._on_quit = on_quit
        self._error = False
        self._paused = False
        self.setToolTip("TS Backup")

        menu = QMenu()
        self.act_open = QAction("열기", menu)
        self.act_run = QAction(
            "지금 확인" if window.cfg.role == "receiver" else "지금 보내기", menu)
        self.act_pause = QAction("잠시 멈춤", menu)
        self.act_quit = QAction("종료", menu)
        menu.addAction(self.act_open)
        menu.addAction(self.act_run)
        menu.addAction(self.act_pause)

        # Only shown when the installer actually placed an update manager
        # exe (a source/dev run has none) - see update_manager/'s own docs
        # for what this triggers. Fire-and-forget: the update manager logs
        # its own result to update_manager.log; there is no UI polling
        # here by design, matching the plan's scoped-down tray integration.
        um_exe = _find_update_manager_exe()
        if um_exe is not None:
            menu.addSeparator()
            self.act_check_update = QAction("지금 업데이트 확인", menu)
            self.act_check_update.triggered.connect(
                lambda: self._check_for_update(um_exe))
            menu.addAction(self.act_check_update)

        menu.addSeparator()
        menu.addAction(self.act_quit)
        self.setContextMenu(menu)

        self.act_open.triggered.connect(self._open)
        self.act_run.triggered.connect(self.engine.run_now)
        self.act_pause.triggered.connect(self._toggle_pause)
        self.act_quit.triggered.connect(self._on_quit)
        self.activated.connect(self._on_activated)

        engine.status.connect(self._on_status)
        engine.run_finished.connect(self._on_run_result)
        engine.failed_after_retries.connect(self._on_failed_after_retries)

    def _check_for_update(self, um_exe) -> None:
        # creationflags is Windows-only (the sole platform this ever
        # actually runs on - um_exe only exists when tsbackup.iss's
        # [Files] placed it), but keep this callable without raising on a
        # dev/test invocation elsewhere.
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        try:
            subprocess.Popen([str(um_exe), "--once"], close_fds=True, **kwargs)
        except OSError:
            pass

    def _open(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def _on_activated(self, reason) -> None:
        # A double-click (or, on some desktops, a single trigger) opens the
        # window - the expected way to get a tray app back.
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self.window.isVisible():
                self.window.hide()
            else:
                self._open()

    def _toggle_pause(self) -> None:
        if self.engine.busy or not self._paused:
            self.engine.pause()
        else:
            self.engine.resume()

    def _on_status(self, text: str) -> None:
        self.setToolTip(f"TS Backup — {wording.status(text)}")
        self._paused = text == "일시중지"
        state = "idle"
        if text in ("실행 중", "압축·전송 중", "수신 대기"):
            state = "running"
        elif text == "일시중지":
            state = "paused"
        if self._error and state == "running":
            # A failure notification just fired for this incident - stay on
            # the error dot through the "running/waiting" status text that
            # follows it, rather than snapping straight back to normal.
            state = "error"
        self.setIcon(app_icon(state))
        self.act_pause.setText("다시 시작" if self._paused else "잠시 멈춤")

    def _on_run_result(self, result) -> None:
        if result.ok and self._error:
            self._error = False

    def _on_failed_after_retries(self, result) -> None:
        self._error = True
        self.setIcon(app_icon("error"))
        self.showMessage(
            "백업이 안 됐어요",
            # The archive is parked in pending/ and the next run resends it
            # (tsbackup/pending.py), so this promise is true. The raw reason
            # is in the log, not in a toast.
            f"받는 컴퓨터에 닿지 못했어요. {MAX_RETRIES}번 다시 해 봤어요. "
            "압축은 만들어 뒀고, 다음 전송 때 같이 보낼게요.",
            QSystemTrayIcon.Warning,
            10000,
        )
