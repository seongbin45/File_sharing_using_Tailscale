"""System tray icon and menu.

The one screen the user specifically asked to have designed. The icon's state
dot mirrors the engine, so a glance at the tray says whether it is running
without opening the window. Its menu is the minimum needed to operate the app
while it lives in the tray: open, run now, pause/resume, quit.
"""

from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .icons import app_icon


class Tray(QSystemTrayIcon):
    def __init__(self, window, engine, on_quit) -> None:
        super().__init__(app_icon("idle"))
        self.window = window
        self.engine = engine
        self._on_quit = on_quit
        self.setToolTip("TS Backup")

        menu = QMenu()
        self.act_open = QAction("열기", menu)
        self.act_run = QAction("지금 실행", menu)
        self.act_pause = QAction("일시중지", menu)
        self.act_quit = QAction("종료", menu)
        menu.addAction(self.act_open)
        menu.addAction(self.act_run)
        menu.addAction(self.act_pause)
        menu.addSeparator()
        menu.addAction(self.act_quit)
        self.setContextMenu(menu)

        self.act_open.triggered.connect(self._open)
        self.act_run.triggered.connect(self.engine.run_now)
        self.act_pause.triggered.connect(self._toggle_pause)
        self.act_quit.triggered.connect(self._on_quit)
        self.activated.connect(self._on_activated)

        engine.status.connect(self._on_status)

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
        if self.engine.busy or self.act_pause.text() == "일시중지":
            self.engine.pause()
        else:
            self.engine.resume()

    def _on_status(self, text: str) -> None:
        self.setToolTip(f"TS Backup — {text}")
        state = "idle"
        if text in ("실행 중", "압축·전송 중", "수신 대기"):
            state = "running"
        elif text == "일시중지":
            state = "paused"
        self.setIcon(app_icon(state))
        self.act_pause.setText("재개" if text == "일시중지" else "일시중지")
