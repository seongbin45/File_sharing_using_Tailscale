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

import shutil
import socket
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from tsbackup import history, pairing
from tsbackup.config import ROLE_RECEIVER, ROLE_SENDER, config_dir

from .icons import app_icon
from .theme import c
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


def _every(minutes) -> str:
    """An interval the way the mockup says it: "하루 한 번", not "1440분마다"."""
    m = int(minutes or 0)
    if m == 1440:
        return "하루 한 번"
    if m == 10080:
        return "일주일에 한 번"
    if m and m % 60 == 0:
        return f"{m // 60}시간마다"
    return f"{m}분마다"


def _when(ts: float) -> str:
    """"오늘 04:26" / "어제 04:26" / "10월 06일 04:26"."""
    day = time.strftime("%Y-%m-%d", time.localtime(ts))
    if day == time.strftime("%Y-%m-%d"):
        prefix = "오늘"
    elif day == time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400)):
        prefix = "어제"
    else:
        prefix = time.strftime("%m월 %d일", time.localtime(ts))
    return f"{prefix} {time.strftime('%H:%M', time.localtime(ts))}"


def _duration(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return f"{s}초"
    if s < 3600:
        return f"{s // 60}분 {s % 60}초"
    return f"{s // 3600}시간 {s % 3600 // 60}분"


class _FolderSummary(QObject):
    """"23개 폴더 · 8.4 GB", computed off the GUI thread - walking a tree of
    tens of thousands of files takes seconds."""

    done = Signal(str)

    def start(self, folder: str, suffix: str) -> None:
        threading.Thread(target=self._run, args=(folder, suffix), daemon=True).start()

    def _run(self, folder: str, suffix: str) -> None:
        path = Path(folder)
        if not folder or not path.is_dir():
            self.done.emit(f"폴더가 없습니다 · {suffix}")
            return
        subdirs = sum(1 for p in path.iterdir() if p.is_dir())
        self.done.emit(f"{subdirs}개 폴더 · {_human_size(_dir_size(path))} · {suffix}")


def _sender_of(folder: Path, registry: dict) -> str:
    """Who sent an unpacked snapshot, from the .ts_sender.json marker the
    sender embeds in every archive. "-" when it carries none (an unpaired
    sender) - never a guess."""
    import json

    try:
        info = json.loads((folder / ".ts_sender.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "-"
    device_id = str(info.get("device_id", ""))
    return registry.get(device_id, {}).get("device_name") or device_id or "-"


class _Card(QFrame):
    """A bordered panel - the mockup's white boxes on a light-gray window."""

    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            f"_Card {{ background: {c('card')}; border: 1px solid {c('border')}; border-radius: 6px; }}")


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
            f"font-size: 24px; font-weight: 700; color: {c('teal')};")
        code_row.addWidget(self.code_label)
        copy_btn = QPushButton("복사")
        copy_btn.clicked.connect(self._copy_code)
        code_row.addWidget(copy_btn)
        code_row.addStretch(1)
        lay.addLayout(code_row)
        lay.addWidget(QLabel("tailnet 안에서만 유효 · 10분 후 만료 · 한 번 쓰면 소멸"))
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
            f"padding: 3px 10px; background: {c('primary_bg')}; border-radius: 10px; "
            f"color: {c('primary')}; font-weight: 600;")
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
        # Sender only: the answer to "is it okay?" from the last recorded
        # run. self.status stays the engine's own state (대기 / 압축·전송 중).
        self.verdict = QLabel("")
        self.verdict.setStyleSheet("font-size: 17px; font-weight: 700;")
        card_lay.addWidget(self.verdict)
        self.status = QLabel("대기")
        self.status.setStyleSheet("font-size: 15px; font-weight: 600;")
        card_lay.addWidget(self.status)
        self.status_detail = QLabel("")
        self.status_detail.setStyleSheet(f"color: {c('muted')};")
        card_lay.addWidget(self.status_detail)
        self.next_run = QLabel("")
        self.next_run.setStyleSheet(f"color: {c('faint')};")
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
        self.poll_note = QLabel("")
        self.poll_note.setStyleSheet(f"color: {c('faint')};")
        btns.addWidget(self.poll_note)
        root.addLayout(btns)

        # log, collapsed by default - see module docstring
        self.log_toggle = QPushButton("▶ 자세한 기록")
        self.log_toggle.setCheckable(True)
        self.log_toggle.setFlat(True)
        self.log_toggle.setStyleSheet(f"text-align: left; border: none; color: {c('primary')};")
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
        self.sender_folder_summary = QLabel("")
        self.sender_folder_summary.setStyleSheet(f"color: {c('faint')};")
        fc.addWidget(self.sender_folder_summary)
        row.addWidget(folder_card, 1)

        targets_card = _Card()
        tc = QVBoxLayout(targets_card)
        self.sender_targets_title = QLabel("받는 쪽")
        tc.addWidget(self.sender_targets_title)
        self.sender_targets_label = QLabel("")
        self.sender_targets_label.setWordWrap(True)
        tc.addWidget(self.sender_targets_label)
        row.addWidget(targets_card, 1)
        self.body_container.addLayout(row)

        self.sender_space_label = QLabel("")
        self.sender_space_label.setStyleSheet(f"color: {c('faint')};")
        self.body_container.addWidget(self.sender_space_label)

        history_card = _Card()
        hc = QVBoxLayout(history_card)
        hc.addWidget(QLabel("지난 전송"))
        self.history_list = QTreeWidget()
        self.history_list.setHeaderLabels(["시각", "보낸 것", "결과"])
        self.history_list.setRootIsDecorated(False)
        self.history_list.setAlternatingRowColors(True)
        hc.addWidget(self.history_list, 1)
        self.body_container.addWidget(history_card, 1)

        self._folder_summary = _FolderSummary()
        self._folder_summary.done.connect(self.sender_folder_summary.setText)
        self._refresh_sender_info()

    def _refresh_sender_info(self) -> None:
        s = self.cfg.sender
        self.sender_folder_label.setText(s.source_dir or "(설정되지 않음)")
        self.sender_folder_summary.setText(f"세는 중... · {_every(s.interval_minutes)}")
        self._folder_summary.start(s.source_dir, _every(s.interval_minutes))

        targets = list(s.taildrop_targets) if s.taildrop_targets else ([s.host] if s.host else [])
        self.sender_targets_title.setText(f"받는 쪽 {len(targets)}곳" if targets else "받는 쪽")
        self.sender_targets_label.setText(
            ("\n".join(targets) if targets else "(대상 없음)") + f"\n전송 방식: {s.transport}")

        work = Path(s.work_dir) if s.work_dir else None
        used = sum(p.stat().st_size for p in work.rglob("*.7z")) if work and work.is_dir() else 0
        keep = f"최근 {s.keep_local}개만 남깁니다" if s.keep_local > 0 else "보낸 압축은 바로 지웁니다"
        self.sender_space_label.setText(f"쓴 공간 {_human_size(used)} · {keep}")
        self._refresh_history()

    def _refresh_history(self) -> None:
        rows = history.load(config_dir() / history.FILENAME, 20)
        self.history_list.clear()
        for r in rows:
            if r.get("ok"):
                result = f"✓ {_human_size(r.get('size') or 0)} · {_duration(r.get('elapsed') or 0)}"
                if r.get("transport"):
                    result += f" · {r['transport']}"
            else:
                result = f"✗ {r.get('detail') or '실패'}"
            QTreeWidgetItem(self.history_list, [_when(r.get("at", 0)), r.get("archive") or "-", result])
        for column in range(2):
            self.history_list.resizeColumnToContents(column)
        self._refresh_verdict(rows[0] if rows else None)

    def _refresh_verdict(self, last: dict | None) -> None:
        if last is None:
            self.verdict.setText("아직 보낸 적이 없습니다")
            self.verdict.setStyleSheet(f"font-size: 17px; font-weight: 700; color: {c('muted')};")
            self.status_detail.setText("")
        elif last.get("ok"):
            self.verdict.setText("✓ 잘 되고 있습니다")
            self.verdict.setStyleSheet(f"font-size: 17px; font-weight: 700; color: {c('primary')};")
            self.status_detail.setText(
                f"마지막 전송 {_when(last['at'])} · {_human_size(last.get('size') or 0)} · "
                f"{_duration(last.get('elapsed') or 0)} 걸림")
        else:
            self.verdict.setText("! 마지막 전송이 실패했습니다")
            self.verdict.setStyleSheet(f"font-size: 17px; font-weight: 700; color: {c('danger')};")
            self.status_detail.setText(f"{_when(last['at'])} · {last.get('detail') or '원인 미상'}")

    def _build_receiver_body(self) -> None:
        self.silence_banner = QFrame()
        self.silence_banner.setObjectName("silence")
        self.silence_banner.setStyleSheet(
            f"#silence {{ background: {c('warn_bg')}; border: 1px solid {c('warn_border')}; border-radius: 7px; }}")
        banner = QHBoxLayout(self.silence_banner)
        text = QVBoxLayout()
        self.silence_title = QLabel("")
        self.silence_title.setWordWrap(True)
        self.silence_title.setStyleSheet(f"color: {c('warn')}; font-weight: 600;")
        self.silence_detail = QLabel("")
        self.silence_detail.setWordWrap(True)
        self.silence_detail.setStyleSheet(f"color: {c('warn')};")
        text.addWidget(self.silence_title)
        text.addWidget(self.silence_detail)
        banner.addLayout(text, 1)
        self.silence_ack = QPushButton("확인했음")
        self.silence_ack.clicked.connect(self._ack_silence)
        banner.addWidget(self.silence_ack, 0, Qt.AlignTop)
        self.silence_banner.setVisible(False)
        self.body_container.addWidget(self.silence_banner)
        # (device_id, last_seen) pairs the person dismissed. Keyed on
        # last_seen so a sender that comes back and then goes quiet again
        # is a new silence, shown again.
        self._acked_silence: set[tuple[str, float]] = set()
        self._shown_silence: tuple[str, float] | None = None

        stats_row = QHBoxLayout()
        self.stat_stored = self._stat_tile("보관 중")
        self.stat_space = self._stat_tile("쓴 공간")
        self.stat_senders = self._stat_tile("보내는 쪽")
        for tile in (self.stat_stored, self.stat_space, self.stat_senders):
            stats_row.addWidget(tile["card"], 1)
        self.body_container.addLayout(stats_row)

        received_card = _Card()
        rc = QVBoxLayout(received_card)
        head = QHBoxLayout()
        head.addWidget(QLabel("받은 것"))
        head.addStretch(1)
        open_btn = QPushButton("폴더 열기")
        open_btn.clicked.connect(self._open_unpack_dir)
        head.addWidget(open_btn)
        rc.addLayout(head)
        self.received_list = QTreeWidget()
        self.received_list.setHeaderLabels(["받은 시각", "보낸 쪽", "크기", "폴더"])
        self.received_list.setRootIsDecorated(False)
        self.received_list.setAlternatingRowColors(True)
        rc.addWidget(self.received_list, 1)
        self.body_container.addWidget(received_card, 1)

        pair_btn = QPushButton("짝 코드 만들기")
        pair_btn.clicked.connect(self._open_pairing_dialog)
        pair_row = QHBoxLayout()
        pair_row.addWidget(pair_btn)
        pair_row.addStretch(1)
        self.body_container.addLayout(pair_row)

    def _ack_silence(self) -> None:
        if self._shown_silence:
            self._acked_silence.add(self._shown_silence)
        self.silence_banner.setVisible(False)

    def _open_unpack_dir(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        if self.cfg.receiver.unpack_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.cfg.receiver.unpack_dir))

    def _stat_tile(self, title: str) -> dict:
        card = _Card()
        lay = QVBoxLayout(card)
        lay.addWidget(QLabel(title))
        value = QLabel("-")
        value.setStyleSheet("font-size: 18px; font-weight: 600;")
        lay.addWidget(value)
        detail = QLabel("")
        detail.setStyleSheet(f"color: {c('faint')}; font-size: 11px;")
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

        sizes = {f: _dir_size(f) for f in folders}
        self.stat_space["value"].setText(_human_size(sum(sizes.values())))
        try:
            free = shutil.disk_usage(unpack_dir).free if unpack_dir and unpack_dir.is_dir() else None
        except OSError:
            free = None
        self.stat_space["detail"].setText(f"남은 공간 {_human_size(free)}" if free is not None else "")

        known_path = config_dir() / pairing.KNOWN_SENDERS_FILENAME
        registry = pairing.load_known_senders(known_path)
        overdue = pairing.overdue_senders(registry)
        self.stat_senders["value"].setText(f"{len(registry)}곳")
        self.stat_senders["detail"].setText(f"{len(overdue)}곳 응답 없음" if overdue else "")
        unacked = [o for o in overdue
                   if (o["device_id"], o.get("last_seen")) not in self._acked_silence]
        if unacked:
            worst = unacked[0]
            days = int(worst["elapsed_seconds"] // 86400)
            self.silence_title.setText(
                f"{worst.get('device_name', worst['device_id'])} 에서 {max(days, 1)}일째 오지 않습니다")
            self.silence_detail.setText(
                f"{_every(worst.get('interval_minutes'))} 오기로 되어 있습니다. "
                "그 컴퓨터가 꺼져 있거나 tailnet 에서 빠졌을 수 있습니다.")
            self._shown_silence = (worst["device_id"], worst.get("last_seen"))
            self.silence_banner.setVisible(True)
        else:
            self._shown_silence = None
            self.silence_banner.setVisible(False)

        self.received_list.clear()
        for folder in folders[:50]:
            when = time.strftime("%m-%d %H:%M", time.localtime(folder.stat().st_mtime))
            QTreeWidgetItem(self.received_list, [
                when, _sender_of(folder, registry), _human_size(sizes[folder]), folder.name])
        for column in range(3):
            self.received_list.resizeColumnToContents(column)

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
        # The status card's verdict and 마지막 전송 line are redrawn from the
        # run history below (_refresh_sender_info), which the engine has
        # already appended this run to.
        if result.ok:
            self.phase.setText("")
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
        self.status.setText("대기")
        self.status_detail.setText("")
        self.next_run.setText("")
        self.phase.setText("")
        self.btn_run.setText("지금 보내기" if is_sender else "지금 확인")
        self.verdict.setVisible(is_sender)
        if is_sender:
            self._refresh_history()
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        running = self.engine.running
        self.btn_start.setText("중지" if running else "시작")
        self.btn_pause.setEnabled(running and self.cfg.role == ROLE_SENDER)
        self.btn_pause.setText("다시 시작" if self.status.text() == "일시중지" else "잠시 멈춤")
        if self.cfg.role == ROLE_RECEIVER:
            # engine.py polls the incoming folder every 5000 ms while running.
            polling = running and self.status.text() != "일시중지"
            self.poll_note.setText("5초마다 확인 중" if polling else "확인 멈춤")
        else:
            self.poll_note.setText("")

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
