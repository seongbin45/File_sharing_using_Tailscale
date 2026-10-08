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

from . import wording
from .icons import app_icon
from .theme import c
from .settings_dialog import SettingsDialog

PHASE_LABEL = {"compress": "압축하고 있어요", "transfer": "보내고 있어요"}
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
    """An interval the way the guide says it: "하루에 한 번", not "1440분마다"."""
    m = int(minutes or 0)
    if m == 1440:
        return "하루에 한 번"
    if m == 10080:
        return "일주일에 한 번"
    if m and m % 60 == 0:
        return f"{m // 60}시간마다"
    return f"{m}분마다"


def _month_day(ts: float) -> str:
    # Not strftime("%m월 %d일"): on Windows, strftime encodes its format in
    # the locale's code page, so Korean text in it raises UnicodeEncodeError
    # on any non-Korean Windows (CI's runner, for one).
    lt = time.localtime(ts)
    return f"{lt.tm_mon:02d}월 {lt.tm_mday:02d}일"


def _when(ts: float) -> str:
    """"오늘 04:26" / "어제 04:26" / "10월 06일 04:26"."""
    day = time.strftime("%Y-%m-%d", time.localtime(ts))
    if day == time.strftime("%Y-%m-%d"):
        prefix = "오늘"
    elif day == time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400)):
        prefix = "어제"
    else:
        prefix = _month_day(ts)
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
            self.done.emit(f"폴더가 없어요 · {suffix}")
            return
        subdirs = sum(1 for p in path.iterdir() if p.is_dir())
        self.done.emit(f"{subdirs}개 폴더 · {_human_size(_dir_size(path))} · {suffix}")


def _snapshot_label(name: str) -> str:
    """"10월 07일 04:00 기준" from an archive or folder name, instead of
    showing the file name itself."""
    from tsbackup.archiver import STAMP_RE

    m = STAMP_RE.search(name if name.endswith(".7z") else name + ".7z")
    if not m:
        return name or "-"
    y, mo, d, h, mi = m.group(1).split("_")
    return f"{mo}월 {d}일 {h}:{mi} 기준"


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
        lay.addWidget(QLabel("보내는 컴퓨터에 이 코드를 넣어 주세요. 10분 동안, 한 번만 쓸 수 있어요."))
        self.pair_status = QLabel("보내는 컴퓨터를 기다리고 있어요")
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
            self.pair_status.setText("Tailscale이 켜져 있는지 확인해 주세요.")
            return
        known_path = config_dir() / pairing.KNOWN_SENDERS_FILENAME
        self._listener = pairing.PairingListener(self.cfg, known_path, log=self._log)
        self.code_label.setText(self._listener.start(ip))
        self._poll.start(1000)

    def _regenerate(self) -> None:
        if self._listener:
            self.code_label.setText(self._listener.regenerate())
            self.pair_status.setText("보내는 컴퓨터를 기다리고 있어요")
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
            self.pair_status.setText("시험 파일까지 잘 받았어요. 이제 닫아도 돼요.")
            self._poll.stop()
        elif self._listener.is_expired():
            self.pair_status.setText("코드가 만료됐어요. 새 코드를 만들어 주세요.")
            self._poll.stop()
        elif self._listener.is_paired():
            self.pair_status.setText("연결됐어요. 시험 파일이 오기를 기다리고 있어요.")

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
        # The answer to "is it okay?" - the sender's from its run history,
        # the receiver's from what actually arrived and unpacked. Below it,
        # what the engine is doing right now; the raw engine string is kept
        # in _engine_status for comparisons, the screen gets wording.status.
        self.verdict = QLabel("")
        self.verdict.setStyleSheet("font-size: 17px; font-weight: 700;")
        card_lay.addWidget(self.verdict)
        self._engine_status = "대기"
        self.status = QLabel(wording.status("대기"))
        self.status.setStyleSheet(f"color: {c('muted')};")
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
        self.btn_start = QPushButton("켜기")
        self.btn_pause = QPushButton("잠시 멈춤")
        self.btn_run = QPushButton("지금 보내기")
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
        self.sender_targets_title = QLabel("받는 컴퓨터")
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
        self.history_list.setHeaderLabels(["보낸 때", "스냅샷", "결과"])
        self.history_list.setRootIsDecorated(False)
        self.history_list.setAlternatingRowColors(True)
        hc.addWidget(self.history_list, 1)
        self.body_container.addWidget(history_card, 1)

        self._folder_summary = _FolderSummary()
        self._folder_summary.done.connect(self.sender_folder_summary.setText)
        self._refresh_sender_info()

    def _refresh_sender_info(self) -> None:
        s = self.cfg.sender
        self.sender_folder_label.setText(s.source_dir or "(아직 정하지 않았어요)")
        self.sender_folder_summary.setText(f"세는 중이에요... · {_every(s.interval_minutes)}")
        self._folder_summary.start(s.source_dir, _every(s.interval_minutes))

        # Device names only: how they are reached (taildrop / sftp / http)
        # belongs to the advanced settings, not the home screen.
        targets = list(s.taildrop_targets) if s.taildrop_targets else ([s.host] if s.host else [])
        self.sender_targets_title.setText(f"받는 컴퓨터 {len(targets)}대" if targets else "받는 컴퓨터")
        self.sender_targets_label.setText("\n".join(targets) if targets else "(아직 없어요)")

        work = Path(s.work_dir) if s.work_dir else None
        used = sum(p.stat().st_size for p in work.rglob("*.7z")) if work and work.is_dir() else 0
        keep = f"최근 {s.keep_local}개만 남겨요" if s.keep_local > 0 else "보낸 압축은 바로 지워요"
        self.sender_space_label.setText(f"쓴 공간 {_human_size(used)} · {keep}")
        self._refresh_history()

    def _refresh_history(self) -> None:
        rows = history.load(config_dir() / history.FILENAME, 20)
        self.history_list.clear()
        for r in rows:
            if r.get("ok"):
                result = f"✓ {_human_size(r.get('size') or 0)} · {_duration(r.get('elapsed') or 0)}"
            else:
                result = "✗ 닿지 못했어요"
            item = QTreeWidgetItem(self.history_list, [
                _when(r.get("at", 0)), _snapshot_label(r.get("archive") or ""), result])
            if not r.get("ok") and r.get("detail"):
                item.setToolTip(2, r["detail"])
        for column in range(2):
            self.history_list.resizeColumnToContents(column)
        self._refresh_verdict(rows[0] if rows else None)

    def _refresh_verdict(self, last: dict | None) -> None:
        # The sender cannot see the far side unpack, so its best news is
        # "보냈어요" in plain text. The blue "잘 되고 있어요" is reserved for
        # the receiver, which has actually unpacked what arrived (design
        # guide, principle ③).
        if last is None:
            self._set_verdict("아직 보낸 적이 없어요", "muted")
            self.status_detail.setText("")
            self.status_detail.setToolTip("")
        elif last.get("ok"):
            self._set_verdict("✓ 보냈어요", None)
            self.status_detail.setText(
                f"마지막 전송 {_when(last['at'])} · {_human_size(last.get('size') or 0)} · "
                f"{_duration(last.get('elapsed') or 0)} 걸렸어요")
            self.status_detail.setToolTip("")
        else:
            self._set_verdict("! 이번 전송은 닿지 못했어요", "danger")
            self.status_detail.setText(f"{_when(last['at'])} · 압축은 만들어 뒀고, 다음 전송 때 같이 보낼게요.")
            self.status_detail.setToolTip(last.get("detail") or "")

    def _set_verdict(self, text: str, role: str | None) -> None:
        self.verdict.setText(text)
        color = f" color: {c(role)};" if role else ""
        self.verdict.setStyleSheet(f"font-size: 17px; font-weight: 700;{color}")

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
        self.silence_ack = QPushButton("확인했어요")
        self.silence_ack.clicked.connect(self._ack_silence)
        banner.addWidget(self.silence_ack, 0, Qt.AlignTop)
        self.silence_banner.setVisible(False)
        self.body_container.addWidget(self.silence_banner)
        # (device_id, last_seen) pairs the person dismissed. Keyed on
        # last_seen so a sender that comes back and then goes quiet again
        # is a new silence, shown again.
        self._acked_silence: set[tuple[str, float]] = set()
        self._shown_silence: tuple[str, float] | None = None

        latest_row = QHBoxLayout()
        self.latest_copy = QLabel("")
        self.latest_copy.setWordWrap(True)
        latest_row.addWidget(self.latest_copy, 1)
        self.latest_open = QPushButton("열어 보기")
        self.latest_open.clicked.connect(self._open_latest)
        latest_row.addWidget(self.latest_open)
        self.body_container.addLayout(latest_row)
        self._latest_folder: Path | None = None

        stats_row = QHBoxLayout()
        self.stat_stored = self._stat_tile("보관 중")
        self.stat_space = self._stat_tile("쓴 공간")
        self.stat_senders = self._stat_tile("보내는 컴퓨터")
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
        self.received_list.setHeaderLabels(["받은 때", "보낸 컴퓨터", "크기", "스냅샷"])
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

    def _open_latest(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        if self._latest_folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._latest_folder)))

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
                f"가장 오래된 것 {_month_day(oldest.stat().st_mtime)}")

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
        self.stat_senders["value"].setText(f"{len(registry)}대")
        self.stat_senders["detail"].setText(f"{len(overdue)}대가 오지 않아요" if overdue else "")
        unacked = [o for o in overdue
                   if (o["device_id"], o.get("last_seen")) not in self._acked_silence]
        if unacked:
            worst = unacked[0]
            days = int(worst["elapsed_seconds"] // 86400)
            self.silence_title.setText(
                f"{worst.get('device_name', worst['device_id'])}에서 {max(days, 1)}일째 오지 않아요")
            self.silence_detail.setText(
                f"{_every(worst.get('interval_minutes'))} 오기로 돼 있어요. "
                "그 컴퓨터가 꺼져 있거나 연결이 끊겼을 수 있어요.")
            self._shown_silence = (worst["device_id"], worst.get("last_seen"))
            self.silence_banner.setVisible(True)
        else:
            self._shown_silence = None
            self.silence_banner.setVisible(False)

        # The verdict stays on a silence even after 확인했어요: the banner can
        # be put away, the state cannot (design guide, principle ④).
        if overdue:
            self._set_verdict(f"! 오지 않는 컴퓨터가 {len(overdue)}대 있어요", "warn")
        elif folders:
            self._set_verdict("✓ 잘 되고 있어요", "primary")
        else:
            self._set_verdict("아직 받은 게 없어요", "muted")

        self._latest_folder = folders[0] if folders else None
        self.latest_open.setEnabled(bool(folders))
        if folders:
            newest = folders[0]
            inner = [p for p in newest.iterdir() if p.is_dir()]
            root = inner[0] if len(inner) == 1 else newest
            count = sum(1 for p in root.iterdir() if p.is_dir())
            self.latest_copy.setText(
                f"마지막으로 확인된 복사본: {_when(newest.stat().st_mtime)} · "
                f"{_sender_of(newest, registry)}" + (f" · {count}개 폴더" if count else ""))
        else:
            self.latest_copy.setText("아직 받아서 푼 복사본이 없어요.")

        self.received_list.clear()
        for folder in folders[:50]:
            QTreeWidgetItem(self.received_list, [
                _when(folder.stat().st_mtime), _sender_of(folder, registry),
                _human_size(sizes[folder]), _snapshot_label(folder.name)])
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
                QMessageBox.warning(self, "설정이 필요해요", "\n".join(problems))
                self._open_settings()
                return
            self.engine.start()
        self._refresh_buttons()

    def _toggle_pause(self) -> None:
        if not self.engine.running:
            return
        if self._engine_status == "일시중지":
            self.engine.resume()
        else:
            self.engine.pause()

    def _run_now(self) -> None:
        problems = self.cfg.problems()
        if problems:
            QMessageBox.warning(self, "설정이 필요해요", "\n".join(problems))
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
            self.log.line("설정을 저장했어요.")
            if was_running:
                self._toggle_start()

    # ------------------------------------------------------------- engine

    def _on_status(self, text: str) -> None:
        self._engine_status = text
        self.status.setText(wording.status(text))
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
            self.phase.setText("이번 전송은 닿지 못했어요. 다음 전송 때 같이 보낼게요.")
            self.phase.setToolTip(result.detail)
        self.progress.setValue(0)
        if self.cfg.role == ROLE_SENDER:
            self._refresh_sender_info()
        else:
            self._refresh_receiver_stats()

    # -------------------------------------------------------------- state

    def _refresh_role_ui(self) -> None:
        is_sender = self.cfg.role == ROLE_SENDER
        self.role_badge.setText("보내는 컴퓨터" if is_sender else "받는 컴퓨터")
        self._engine_status = "대기"
        self.status.setText(wording.status("대기"))
        self.status_detail.setText("")
        self.next_run.setText("")
        self.phase.setText("")
        self.btn_run.setText("지금 보내기" if is_sender else "지금 확인")
        if is_sender:
            self._refresh_history()
        else:
            self._refresh_receiver_stats()
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        running = self.engine.running
        self.btn_start.setText("끄기" if running else "켜기")
        self.btn_pause.setEnabled(running and self.cfg.role == ROLE_SENDER)
        self.btn_pause.setText("다시 시작" if self._engine_status == "일시중지" else "잠시 멈춤")
        if self.cfg.role == ROLE_RECEIVER:
            # engine.py polls the incoming folder every 5000 ms while running.
            polling = running and self._engine_status != "일시중지"
            self.poll_note.setText("5초마다 확인하고 있어요" if polling else "확인을 멈췄어요")
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
        self.log.line("창을 닫았어요. 백업은 트레이에서 계속 돌아요.")

    def _quit(self) -> None:
        self._really_quit = True
        self.close()
