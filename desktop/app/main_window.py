"""Main window.

One screen carries the whole app: which role, whether it is running, when the
next run is, a live progress bar, and the log. The role toggle at the top is
the "하나의 앱 + 역할 토글" the user asked for - switching it stops the engine
and reconfigures, because a sender and a receiver are not the same run.

Closing the window does not quit: it hides to the tray and the engine keeps
going. Quitting is a deliberate act from the tray or the File menu, so a
backup that is meant to run unattended is not ended by a stray click on the X.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from tsbackup.config import ROLE_RECEIVER, ROLE_SENDER

from .icons import app_icon
from .settings_dialog import SettingsDialog

PHASE_LABEL = {"compress": "압축 중", "transfer": "전송 중"}


class MainWindow(QMainWindow):
    def __init__(self, cfg, log, engine, on_quit) -> None:
        super().__init__()
        self.cfg = cfg
        self.log = log
        self.engine = engine
        self._on_quit = on_quit
        self._really_quit = False

        self.setWindowTitle("TS Backup")
        self.setWindowIcon(app_icon("idle"))
        self.resize(720, 560)

        self._build()
        self._wire()
        self._refresh_role_ui()
        for line in self.log.tail(200):
            self.log_view.appendPlainText(line)

    # ------------------------------------------------------------- layout

    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # role + status row
        top = QHBoxLayout()
        top.addWidget(QLabel("역할"))
        self.role = QComboBox()
        self.role.addItem("보내는 쪽", ROLE_SENDER)
        self.role.addItem("받는 쪽", ROLE_RECEIVER)
        self.role.setCurrentIndex(0 if self.cfg.role == ROLE_SENDER else 1)
        top.addWidget(self.role)
        top.addStretch(1)
        self.status = QLabel("대기")
        self.status.setStyleSheet("font-weight: bold;")
        top.addWidget(QLabel("상태"))
        top.addWidget(self.status)
        root.addLayout(top)

        self.next_run = QLabel("")
        self.next_run.setStyleSheet("color: #767676;")
        root.addWidget(self.next_run)

        # progress
        self.phase = QLabel("")
        root.addWidget(self.phase)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        root.addWidget(self.progress)

        # buttons
        btns = QHBoxLayout()
        self.btn_start = QPushButton("시작")
        self.btn_pause = QPushButton("일시중지")
        self.btn_run = QPushButton("지금 실행")
        self.btn_settings = QPushButton("설정")
        for b in (self.btn_start, self.btn_pause, self.btn_run, self.btn_settings):
            btns.addWidget(b)
        root.addLayout(btns)

        # log
        root.addWidget(QLabel("로그"))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setStyleSheet(
            "font-family: 'Cascadia Mono','D2Coding',Consolas,monospace; font-size: 12px;")
        root.addWidget(self.log_view, 1)

        # menu
        file_menu = self.menuBar().addMenu("파일")
        act_settings = QAction("설정", self)
        act_settings.triggered.connect(self._open_settings)
        act_quit = QAction("종료", self)
        act_quit.triggered.connect(self._quit)
        file_menu.addAction(act_settings)
        file_menu.addSeparator()
        file_menu.addAction(act_quit)

    def _wire(self) -> None:
        self.btn_start.clicked.connect(self._toggle_start)
        self.btn_pause.clicked.connect(self._toggle_pause)
        self.btn_run.clicked.connect(self._run_now)
        self.btn_settings.clicked.connect(self._open_settings)
        self.role.currentIndexChanged.connect(self._change_role)

        self.engine.status.connect(self._on_status)
        self.engine.progress.connect(self._on_progress)
        self.engine.line.connect(self.log_view.appendPlainText)
        self.engine.next_run.connect(self.next_run.setText)
        self.engine.run_finished.connect(self._on_run_finished)

    # ------------------------------------------------------------ actions

    def _toggle_start(self) -> None:
        if self.engine.running:
            self.engine.stop()
        else:
            problems = self.cfg.problems()
            if problems:
                QMessageBox.warning(self, "설정이 필요합니다", "\n".join(problems))
                self._open_settings()
                return
            self.engine.start()
        self._refresh_buttons()

    def _toggle_pause(self) -> None:
        if not self.engine.running:
            return
        # paused state is reflected by the status text; toggle on it
        if self.status.text() in ("일시중지",):
            self.engine.resume()
        else:
            self.engine.pause()

    def _run_now(self) -> None:
        problems = self.cfg.problems()
        if problems:
            QMessageBox.warning(self, "설정이 필요합니다", "\n".join(problems))
            return
        self.engine.run_now()

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec():
            was_running = self.engine.running
            self.engine.stop()
            dlg.apply_to(self.cfg)
            self.cfg.save()
            self.role.blockSignals(True)
            self.role.setCurrentIndex(0 if self.cfg.role == ROLE_SENDER else 1)
            self.role.blockSignals(False)
            self._refresh_role_ui()
            self.log.line("설정을 저장했습니다.")
            if was_running:
                self._toggle_start()

    def _change_role(self) -> None:
        new_role = self.role.currentData()
        if new_role == self.cfg.role:
            return
        self.engine.stop()
        self.cfg.role = new_role
        self.cfg.save()
        self._refresh_role_ui()
        self.log.line(f"역할 변경: {new_role}")

    # ------------------------------------------------------------- engine

    def _on_status(self, text: str) -> None:
        self.status.setText(text)
        self._refresh_buttons()

    def _on_progress(self, phase: str, pct: int) -> None:
        self.phase.setText(f"{PHASE_LABEL.get(phase, phase)}  {pct}%")
        self.progress.setValue(pct)

    def _on_run_finished(self, result) -> None:
        if result.ok:
            self.phase.setText(f"완료: {result.archive} ({result.size:,} bytes, {result.seconds:.1f}s)")
        else:
            self.phase.setText(f"실패: {result.detail}")
        self.progress.setValue(0)

    # -------------------------------------------------------------- state

    def _refresh_role_ui(self) -> None:
        is_sender = self.cfg.role == ROLE_SENDER
        self.btn_run.setText("지금 실행" if is_sender else "지금 확인")
        self.status.setText("대기")
        self.next_run.setText("")
        self.phase.setText("")
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        running = self.engine.running
        self.btn_start.setText("중지" if running else "시작")
        self.btn_pause.setEnabled(running and self.cfg.role == ROLE_SENDER)
        self.role.setEnabled(not running)

    # -------------------------------------------------------------- close

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._really_quit or not self.cfg.minimize_to_tray:
            self._on_quit()
            event.accept()
            return
        event.ignore()
        self.hide()
        self.log.line("트레이로 최소화되었습니다. 백업은 계속 실행됩니다.")

    def _quit(self) -> None:
        self._really_quit = True
        self.close()
