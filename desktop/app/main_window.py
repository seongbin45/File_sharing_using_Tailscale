"""Main window.

Role is no longer a combo box in this window - it's a first-run wizard
decision (app/wizard.py), because a sender and a receiver are two devices
doing different jobs, not two settings of the same run. What's left here is
role-specific: a sender sees its own status/targets/history, a receiver
sees its own silence-detection banner/stats/received list. Switching role
after the fact is a deliberate "설정 -> 역할 바꾸기" trip, not a stray combo
click - see _open_settings().

The log view is real but collapsed by default: the person opening this
window is usually asking "is it okay?", answered by the status card above
it, not by four screens of timestamps. Closing the window does not quit: it
hides to the tray and the engine keeps going.
"""

from __future__ import annotations

import socket
import time
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QVBoxLayout, QWidget,
)

from tsbackup import pairing
from tsbackup.config import ROLE_RECEIVER, ROLE_SENDER, config_dir

from .icons import app_icon
from .settings_dialog import SettingsDialog

PHASE_LABEL = {"compress": "압축 중", "transfer": "전송 중"}
STAT_CHECK_INTERVAL_MS = 30_000  # how often the receiver home re-scans


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def _dir_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


class _Card(QFrame):
    """A bordered panel - the mockup's white boxes on a light-gray window."""

    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "_Card { background: white; border: 1px solid #dcdcdc; border-radius: 6px; }")


class PairingCodeDialog(QDialog):
    """"짝 코드 만들기" - the receiver home's way to pair another sender
    later without re-running the whole first-run wizard."""

    def __init__(self, cfg, parent=None, log=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self._log = log
        self.setWindowTitle("짝 코드 만들기")
        self.setMinimumWidth(360)
        self._listener: pairing.PairingListener | None = None

        lay = QVBoxLayout(self)
        code_row = QHBoxLayout()
        self.code_label = QLabel("...")
        self.code_label.setStyleSheet(
            "font-family: 'Cascadia Mono','D2Coding',Consolas,monospace; "
            "font-size: 24px; font-weight: 700;")
        code_row.addWidget(self.code_label)
        copy_btn = QPushButton("복사")
        copy_btn.clicked.connect(self._copy_code)
        code_row.addWidget(copy_btn)
        code_row.addStretch(1)
        lay.addLayout(code_row)
        lay.addWidget(QLabel("tailnet 안에서만 유효 · 15분 후 만료 · 한 번 쓰면 소멸"))
        self.pair_status = QLabel("대기 중...")
        lay.addWidget(self.pair_status)

        row = QHBoxLayout()
        regen = QPushButton("새 코드")
        regen.clicked.connect(self._regenerate)
        close = QPushButton("닫기")
        close.clicked.connect(self.accept)
        row.addWidget(regen)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)

        self._poll = QTimer(self)
        self._poll.timeout.connect(self._poll_paired)
        self._start()

    def _start(self) -> None:
        ip = pairing.local_tailscale_ip()
        if not ip:
            self.code_label.setText("-")
            self.pair_status.setText("Tailscale 이 연결되어 있는지 확인하십시오.")
            return
        known_path = config_dir() / pairing.KNOWN_SENDERS_FILENAME
        self._listener = pairing.PairingListener(self.cfg, known_path, log=self._log)
        self.code_label.setText(self._listener.start(ip))
        self._poll.start(1000)

    def _regenerate(self) -> None:
        if self._listener:
            self.code_label.setText(self._listener.regenerate())
            self.pair_status.setText("대기 중...")
            if not self._poll.isActive():
                self._poll.start(1000)

    def _copy_code(self) -> None:
        text = self.code_label.text()
        if text and text != "...":
            QApplication.clipboard().setText(text)

    def _poll_paired(self) -> None:
        if not self._listener:
            return
        # Same reasoning as the wizard's receiver page: is_paired() alone
        # only means the code was accepted - the sender's mandatory test-
        # transfer still needs this listener alive to answer /confirm. This
        # dialog's "닫기" isn't gated on it (there's no mandatory-wizard
        # block here), but the status text should say so accurately rather
        # than implying "connected" already means "done."
        if self._listener.is_confirmed():
            self.pair_status.setText("시험 전송까지 확인되었습니다.")
            self._poll.stop()
        elif self._listener.is_paired():
            self.pair_status.setText("연결됨 - 보내는 쪽의 시험 전송을 기다리는 중...")

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._poll.stop()
        if self._listener:
            self._listener.stop()
        super().closeEvent(event)

    def accept(self) -> None:
        self._poll.stop()
        if self._listener:
            self._listener.stop()
        super().accept()


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

        self._stat_timer = QTimer(self)
        self._stat_timer.timeout.connect(self._refresh_receiver_stats)

        self._build()
        self._wire_engine()
        self._refresh_role_ui()
        for line in self.log.tail(200):
            self.log_view.appendPlainText(line)

    # ------------------------------------------------------------- layout

    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        top = QHBoxLayout()
        self.role_badge = QLabel()
        self.role_badge.setStyleSheet(
            "padding: 3px 10px; background: #eaf2fb; border-radius: 10px; "
            "color: #0067c0; font-weight: 600;")
        top.addWidget(self.role_badge)
        top.addWidget(QLabel(socket.gethostname()))
        top.addStretch(1)
        self.btn_settings = QPushButton("설정")
        self.btn_settings.clicked.connect(self._open_settings)
        top.addWidget(self.btn_settings)
        root.addLayout(top)

        # status card - the one thing worth knowing at a glance
        card = _Card()
        card_lay = QVBoxLayout(card)
        self.status = QLabel("대기")
        self.status.setStyleSheet("font-size: 15px; font-weight: 600;")
        card_lay.addWidget(self.status)
        self.status_detail = QLabel("")
        self.status_detail.setStyleSheet("color: #5d5d5d;")
        card_lay.addWidget(self.status_detail)
        self.next_run = QLabel("")
        self.next_run.setStyleSheet("color: #767676;")
        card_lay.addWidget(self.next_run)
        root.addWidget(card)

        self.phase = QLabel("")
        root.addWidget(self.phase)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        root.addWidget(self.progress)

        # role-specific body, rebuilt whenever role changes
        self.body_container = QVBoxLayout()
        root.addLayout(self.body_container, 1)
        self._build_role_body()

        # buttons
        btns = QHBoxLayout()
        self.btn_start = QPushButton("시작")
        self.btn_pause = QPushButton("일시중지")
        self.btn_run = QPushButton("지금 실행")
        for b in (self.btn_start, self.btn_pause, self.btn_run):
            btns.addWidget(b)
        btns.addStretch(1)
        root.addLayout(btns)

        # log, collapsed by default - see module docstring
        self.log_toggle = QPushButton("▶ 자세한 기록")
        self.log_toggle.setCheckable(True)
        self.log_toggle.setFlat(True)
        self.log_toggle.setStyleSheet("text-align: left; border: none; color: #0067c0;")
        self.log_toggle.toggled.connect(self._toggle_log)
        root.addWidget(self.log_toggle)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setStyleSheet(
            "font-family: 'Cascadia Mono','D2Coding',Consolas,monospace; font-size: 12px;")
        self.log_view.setVisible(False)
        root.addWidget(self.log_view, 1)

        # menu
        file_menu = self.menuBar().addMenu("파일")
        act_settings = QAction("설정", self)
        act_settings.triggered.connect(self._open_settings)
        act_role = QAction("역할 바꾸기", self)
        act_role.triggered.connect(self._open_settings)
        act_quit = QAction("종료", self)
        act_quit.triggered.connect(self._quit)
        file_menu.addAction(act_settings)
        file_menu.addAction(act_role)
        file_menu.addSeparator()
        file_menu.addAction(act_quit)

        self.btn_start.clicked.connect(self._toggle_start)
        self.btn_pause.clicked.connect(self._toggle_pause)
        self.btn_run.clicked.connect(self._run_now)

    def _toggle_log(self, checked: bool) -> None:
        self.log_view.setVisible(checked)
        self.log_toggle.setText(f"{'▼' if checked else '▶'} 자세한 기록")

    # -------------------------------------------------------- role bodies

    def _clear_body(self) -> None:
        while self.body_container.count():
            item = self.body_container.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _build_role_body(self) -> None:
        self._clear_body()
        self._stat_timer.stop()
        if self.cfg.role == ROLE_SENDER:
            self._build_sender_body()
        else:
            self._build_receiver_body()
            self._refresh_receiver_stats()
            self._stat_timer.start(STAT_CHECK_INTERVAL_MS)

    def _build_sender_body(self) -> None:
        row = QHBoxLayout()

        folder_card = _Card()
        fc = QVBoxLayout(folder_card)
        fc.addWidget(QLabel("보내는 폴더"))
        self.sender_folder_label = QLabel("")
        self.sender_folder_label.setWordWrap(True)
        fc.addWidget(self.sender_folder_label)
        row.addWidget(folder_card, 1)

        targets_card = _Card()
        tc = QVBoxLayout(targets_card)
        tc.addWidget(QLabel("받는 쪽"))
        self.sender_targets_label = QLabel("")
        self.sender_targets_label.setWordWrap(True)
        tc.addWidget(self.sender_targets_label)
        row.addWidget(targets_card, 1)

        self.body_container.addLayout(row)
        self._refresh_sender_info()

    def _refresh_sender_info(self) -> None:
        s = self.cfg.sender
        self.sender_folder_label.setText(
            f"{s.source_dir or '(설정되지 않음)'}\n{s.interval_minutes}분마다")
        targets = ", ".join(s.taildrop_targets) if s.taildrop_targets else (s.host or "(대상 없음)")
        self.sender_targets_label.setText(f"{targets}\n전송 방식: {s.transport}")

    def _build_receiver_body(self) -> None:
        self.silence_banner = QLabel("")
        self.silence_banner.setWordWrap(True)
        self.silence_banner.setStyleSheet(
            "padding: 12px 14px; background: #fff9e6; border: 1px solid #e8d9a0; "
            "border-radius: 7px; color: #6b5600;")
        self.silence_banner.setVisible(False)
        self.body_container.addWidget(self.silence_banner)

        stats_row = QHBoxLayout()
        self.stat_stored = self._stat_tile("보관 중")
        self.stat_space = self._stat_tile("쓴 공간")
        self.stat_senders = self._stat_tile("보내는 쪽")
        for tile in (self.stat_stored, self.stat_space, self.stat_senders):
            stats_row.addWidget(tile["card"], 1)
        self.body_container.addLayout(stats_row)

        received_card = _Card()
        rc = QVBoxLayout(received_card)
        rc.addWidget(QLabel("받은 것"))
        self.received_list = QListWidget()
        rc.addWidget(self.received_list, 1)
        self.body_container.addWidget(received_card, 1)

        pair_btn = QPushButton("짝 코드 만들기")
        pair_btn.clicked.connect(self._open_pairing_dialog)
        pair_row = QHBoxLayout()
        pair_row.addWidget(pair_btn)
        pair_row.addStretch(1)
        self.body_container.addLayout(pair_row)

    def _stat_tile(self, title: str) -> dict:
        card = _Card()
        lay = QVBoxLayout(card)
        lay.addWidget(QLabel(title))
        value = QLabel("-")
        value.setStyleSheet("font-size: 18px; font-weight: 600;")
        lay.addWidget(value)
        detail = QLabel("")
        detail.setStyleSheet("color: #767676; font-size: 11px;")
        lay.addWidget(detail)
        return {"card": card, "value": value, "detail": detail}

    def _open_pairing_dialog(self) -> None:
        PairingCodeDialog(self.cfg, self, log=self.log.line).exec()
        self._refresh_receiver_stats()

    def _refresh_receiver_stats(self) -> None:
        if self.cfg.role != ROLE_RECEIVER:
            return
        r = self.cfg.receiver
        unpack_dir = Path(r.unpack_dir) if r.unpack_dir else None

        folders = sorted(
            [p for p in unpack_dir.iterdir() if p.is_dir()],
            key=lambda p: p.stat().st_mtime, reverse=True,
        ) if unpack_dir and unpack_dir.is_dir() else []
        self.stat_stored["value"].setText(f"{len(folders)}개")
        if folders:
            oldest = min(folders, key=lambda p: p.stat().st_mtime)
            self.stat_stored["detail"].setText(
                f"가장 오래된 것 {time.strftime('%m월 %d일', time.localtime(oldest.stat().st_mtime))}")

        total = _dir_size(unpack_dir) if unpack_dir else 0
        self.stat_space["value"].setText(_human_size(total))

        known_path = config_dir() / pairing.KNOWN_SENDERS_FILENAME
        registry = pairing.load_known_senders(known_path)
        overdue = pairing.overdue_senders(registry)
        self.stat_senders["value"].setText(f"{len(registry)}곳")
        if overdue:
            self.stat_senders["detail"].setText(f"{len(overdue)}곳 응답 없음")
            worst = overdue[0]
            days = int(worst["elapsed_seconds"] // 86400)
            self.silence_banner.setText(
                f"{worst.get('device_name', worst['device_id'])} 에서 "
                f"{max(days, 1)}일째 오지 않습니다. 그 컴퓨터가 꺼져 있거나 "
                "tailnet 에서 빠졌을 수 있습니다.")
            self.silence_banner.setVisible(True)
        else:
            self.stat_senders["detail"].setText("")
            self.silence_banner.setVisible(False)

        self.received_list.clear()
        for folder in folders[:50]:
            when = time.strftime("%m-%d %H:%M", time.localtime(folder.stat().st_mtime))
            QListWidgetItem(f"{when}   {folder.name}", self.received_list)

    # ------------------------------------------------------------- wiring

    def _wire_engine(self) -> None:
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
            old_role = self.cfg.role
            self.engine.stop()
            dlg.apply_to(self.cfg)
            self.cfg.save()
            if self.cfg.role != old_role:
                self._build_role_body()
            elif self.cfg.role == ROLE_SENDER:
                self._refresh_sender_info()
            else:
                self._refresh_receiver_stats()
            self._refresh_role_ui()
            self.log.line("설정을 저장했습니다.")
            if was_running:
                self._toggle_start()

    # ------------------------------------------------------------- engine

    def _on_status(self, text: str) -> None:
        self.status.setText(text)
        self._refresh_buttons()

    def _on_progress(self, phase: str, pct: int) -> None:
        self.phase.setText(f"{PHASE_LABEL.get(phase, phase)}  {pct}%")
        self.progress.setValue(pct)

    def _on_run_finished(self, result) -> None:
        if result.ok:
            self.phase.setText("")
            self.status_detail.setText(
                f"마지막 전송 {time.strftime('%H:%M')} · {result.size:,} bytes · {result.seconds:.1f}초 걸림")
        else:
            self.phase.setText(f"실패: {result.detail}")
        self.progress.setValue(0)
        if self.cfg.role == ROLE_SENDER:
            self._refresh_sender_info()
        else:
            self._refresh_receiver_stats()

    # -------------------------------------------------------------- state

    def _refresh_role_ui(self) -> None:
        is_sender = self.cfg.role == ROLE_SENDER
        self.role_badge.setText("보내는 쪽" if is_sender else "받는 쪽")
        self.btn_run.setText("지금 실행" if is_sender else "지금 확인")
        self.status.setText("대기")
        self.status_detail.setText("")
        self.next_run.setText("")
        self.phase.setText("")
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        running = self.engine.running
        self.btn_start.setText("중지" if running else "시작")
        self.btn_pause.setEnabled(running and self.cfg.role == ROLE_SENDER)

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
