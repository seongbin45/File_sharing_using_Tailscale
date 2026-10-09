"""나루 main window - the homes drawn in 나루 디자인 시스템 §09 화면.

    보내는 컴퓨터 · 홈      verdict (✓ 잘 되고 있어요 + 다음 전송), 보내는 폴더,
                           받는 컴퓨터 N대, 지금 보내기 / 잠시 멈추기, 지난 전송
    보내는 컴퓨터 · 오류    ✕ 건너가지 못했어요, 이렇게 해 보세요 1-2-3, what is kept,
                           자세한 기록 / 다시 보내기
    받는 컴퓨터 · 홈        verdict (! …에서 3일째 오지 않아요 + 확인했어요),
                           보관 중 / 쓴 공간 / 보내는 컴퓨터 tiles, 건너온 것,
                           짝 코드 만들기 + 5초마다 확인하고 있어요
    받는 컴퓨터 · 빈 상태   아직 건너온 게 없어요

The verdict line is the answer to "is it okay?" and leads every home
(principle ①). Closing the window hides it to the tray; the engine keeps
going. Everything here reads the engine and the files on disk - it never
reaches into the engine's private state.
"""

from __future__ import annotations

import shutil
import socket
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QLabel, QMainWindow, QMessageBox,
    QPlainTextEdit, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from tsbackup import history, pairing
from tsbackup.config import ROLE_SENDER, config_dir

from . import wording
from .icons import window_icon
from .widgets import (
    Card, CodePanel, Dot, ElidedLabel, ListCard, LogoMark, NoteBar, NumberedSteps, StatTile, StatusLine,
    VerdictCard, button, hbox, label, page_widget,
)
from .workers import FolderStats, dir_size

STAT_CHECK_INTERVAL_MS = 30_000  # how often the receiver home re-reads the disk


def _open_path(path: str | Path) -> None:
    if path:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def _copula(text: str) -> str:
    return text + ("예요" if text.endswith("시") else "이에요")


def _sender_of(folder: Path, registry: dict) -> str:
    """Who sent an unpacked snapshot, from the .ts_sender.json marker each
    archive carries. "-" when there is none - never a guess."""
    import json

    try:
        info = json.loads((folder / ".ts_sender.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "-"
    device_id = str(info.get("device_id", ""))
    return registry.get(device_id, {}).get("device_name") or device_id or "-"


# ------------------------------------------------------------ dialogs


class PairingCodeDialog(QDialog):
    """짝 코드 만들기 - the receiver home's way to pair another sender later,
    the wizard's code page in a dialog of its own."""

    def __init__(self, cfg, parent=None, log=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self._log = log
        self.setWindowTitle("짝 코드 만들기")
        self.setWindowIcon(window_icon())
        self.setMinimumWidth(520)
        self._listener: pairing.PairingListener | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 28, 32, 24)
        lay.setSpacing(12)
        lay.addWidget(label("보내는 컴퓨터에 이 코드를 넣어 주세요", "title", wrap=True))
        lay.addWidget(label("같은 Tailscale 네트워크에 있는 컴퓨터에서만 쓸 수 있어요.", "body2", wrap=True))
        lay.addSpacing(8)
        self.panel = CodePanel()
        self.panel.copy.connect(self._copy_code)
        self.panel.renew.connect(self._regenerate)
        self.code_label = self.panel.code
        lay.addWidget(self.panel)
        self.pair_status = StatusLine()
        lay.addWidget(self.pair_status)
        lay.addSpacing(8)
        close = button("닫기", "primary", large=True)
        close.clicked.connect(self.accept)
        lay.addLayout(hbox(None, close))

        self._poll = QTimer(self)
        self._poll.timeout.connect(self._poll_paired)
        self._start()

    def _start(self) -> None:
        ip = pairing.local_tailscale_ip()
        if not ip:
            self.panel.set_code("—")
            self.pair_status.set("err", "Tailscale이 꺼져 있거나 로그인이 안 돼 있어요.")
            return
        known_path = config_dir() / pairing.KNOWN_SENDERS_FILENAME
        self._listener = pairing.PairingListener(self.cfg, known_path, log=self._log)
        self.panel.set_code(self._listener.start(ip))
        self.pair_status.set("wait", "보내는 컴퓨터를 기다리고 있어요")
        self._poll.start(1000)

    def _regenerate(self) -> None:
        if self._listener:
            self.panel.set_code(self._listener.regenerate())
            self.pair_status.set("wait", "보내는 컴퓨터를 기다리고 있어요")
            if not self._poll.isActive():
                self._poll.start(1000)

    def _copy_code(self) -> None:
        text = self.panel.code.text()
        if text and text != "—":
            QApplication.clipboard().setText(text)

    def _poll_paired(self) -> None:
        if not self._listener:
            return
        # is_paired() only means the code was accepted - the sender's test
        # transfer still needs this listener alive to answer /confirm.
        if self._listener.is_confirmed():
            self.pair_status.set("ok", "✓ 시험 파일까지 잘 받았어요. 이제 닫아도 돼요.")
            self._poll.stop()
        elif self._listener.is_expired():
            self.pair_status.set("warn", "코드가 만료됐어요. 새 코드를 만들어 주세요.")
            self._poll.stop()
        elif self._listener.is_paired():
            self.pair_status.set("linked", "연결됐어요. 시험 파일이 오기를 기다리고 있어요.")

    def closeEvent(self, event) -> None:  # noqa: N802
        self._shutdown()
        super().closeEvent(event)

    def accept(self) -> None:
        self._shutdown()
        super().accept()

    def _shutdown(self) -> None:
        self._poll.stop()
        if self._listener:
            self._listener.stop()
            self._listener = None


class RecordDialog(QDialog):
    """전체 기록: every recorded run, and the detailed log behind it."""

    def __init__(self, log, parent=None, *, runs: bool = True) -> None:
        super().__init__(parent)
        self.setWindowTitle("전체 기록")
        self.setWindowIcon(window_icon())
        self.resize(720, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 20)
        lay.setSpacing(12)
        if runs:
            lst = ListCard("지난 전송")
            rows = history.load(config_dir() / history.FILENAME, history.KEEP)
            for r in rows:
                _history_row(lst, r)
            lst.set_empty("" if rows else "아직 기록이 없어요.")
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.NoFrame)
            area.setWidget(lst)
            lay.addWidget(area, 1)
        lay.addWidget(label("자세한 기록", "section"))
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText("\n".join(log.tail(500)))
        text.moveCursor(text.textCursor().End)
        lay.addWidget(text, 1)
        close = button("닫기", "primary")
        close.clicked.connect(self.accept)
        lay.addLayout(hbox(button_open_log(log), None, close))


def button_open_log(log) -> QWidget:
    b = button("기록 파일 열기", "text")
    b.clicked.connect(lambda: _open_path(log.path))
    return b


def _history_row(lst: ListCard, r: dict) -> None:
    if r.get("ok"):
        lst.add_row("pr", wording.when_short(r.get("at", 0)), "1곳으로 건너갔어요",
                    wording.size(r.get("size")), tip=r.get("detail") or "")
    else:
        lst.add_row("er", wording.when_short(r.get("at", 0)), "건너가지 못했어요",
                    wording.size(r.get("size")) if r.get("size") else "", tip=r.get("detail") or "")


# ------------------------------------------------------------ homes


class _Home(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.cfg = win.cfg
        self.engine = win.engine


class SenderHome(_Home):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__(win)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.views = QStackedWidget()
        outer.addWidget(self.views)
        self.views.addWidget(self._build_normal())
        self.views.addWidget(self._build_error())
        self._stats = FolderStats()
        self._stats.done.connect(self._show_stats)
        self._progress = ("", 0)
        self.refresh(full=True)

    # normal ---------------------------------------------------------

    def _build_normal(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        self.verdict = VerdictCard()
        self.next_caption = label("다음 전송", "caption")
        self.next_caption.setAlignment(Qt.AlignRight)
        self.next_value = label("", "section")
        self.next_value.setStyleSheet("font-size: 16px;")
        self.next_value.setAlignment(Qt.AlignRight)
        nxt = QWidget()
        nl = QVBoxLayout(nxt)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(2)
        nl.addWidget(self.next_caption)
        nl.addWidget(self.next_value)
        self.verdict.set_right(nxt)
        self.next_box = nxt
        lay.addWidget(self.verdict)

        self.folder_card = Card(padding=16, spacing=4)
        self.folder_card.body.addWidget(label("보내는 폴더", "caption"))
        self.folder_path = ElidedLabel()
        self.folder_card.body.addWidget(self.folder_path)
        self.folder_summary = label("", "caption2")
        self.folder_card.body.addWidget(self.folder_summary)
        self.folder_card.body.addStretch(1)

        self.targets_card = Card(padding=16, spacing=4)
        self.targets_title = label("받는 컴퓨터", "caption")
        self.targets_card.body.addWidget(self.targets_title)
        self.targets_rows = QVBoxLayout()
        self.targets_rows.setSpacing(2)
        self.targets_card.body.addLayout(self.targets_rows)
        self.targets_card.body.addStretch(1)
        cards = hbox(self.folder_card, self.targets_card, spacing=16)
        cards.setStretch(0, 1)
        cards.setStretch(1, 1)
        lay.addLayout(cards)

        self.send_btn = button("지금 보내기", "primary")
        self.send_btn.clicked.connect(self.win._run_now)
        self.pause_btn = button("잠시 멈추기")
        self.pause_btn.clicked.connect(self.win._toggle_pause)
        self.kept = label("", "caption")
        lay.addLayout(hbox(self.send_btn, self.pause_btn, None, self.kept, spacing=8))

        self.history_card = ListCard("지난 전송", "전체 기록")
        self.history_card.action.clicked.connect(self.win._open_records)
        lay.addWidget(self.history_card)
        lay.addStretch(1)
        return w

    # error ----------------------------------------------------------

    def _build_error(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        self.err_verdict = VerdictCard()
        lay.addWidget(self.err_verdict)
        lay.addWidget(NumberedSteps("이렇게 해 보세요", [
            "받는 컴퓨터가 켜져 있는지 확인해 주세요",
            "두 컴퓨터 모두 Tailscale에 로그인돼 있는지 확인해 주세요",
            "그래도 안 되면 자세한 기록을 열어 보세요",
        ]))
        self.err_note = NoteBar()
        lay.addWidget(self.err_note)
        lay.addStretch(1)
        records = button("자세한 기록", "text")
        records.clicked.connect(self.win._open_records)
        self.retry_btn = button("다시 보내기", "primary", large=True)
        self.retry_btn.clicked.connect(self.win._run_now)
        lay.addLayout(hbox(records, None, self.retry_btn))
        return w

    # data -----------------------------------------------------------

    def refresh(self, full: bool = False) -> None:
        s = self.cfg.sender
        rows = history.load(config_dir() / history.FILENAME, 20)
        last = rows[0] if rows else None
        running = self.engine.busy
        paused = self.engine.paused

        if last and not last.get("ok") and not running and not paused:
            self._show_error(last)
            return
        self.views.setCurrentIndex(0)

        due = self.engine.due
        self.next_value.setText(wording.when(due) if due else "-")
        self.next_box.setVisible(bool(due) and not running)
        if running:
            phase, pct = self._progress
            what = {"compress": "압축하고 있어요", "transfer": "건너편으로 보내고 있어요"}.get(phase, "준비하고 있어요")
            self.verdict.set_verdict("link", "보내고 있어요", f"{what} · {pct}%" if phase else what)
        elif paused:
            self.verdict.set_verdict("none", "잠시 멈췄어요", "다시 시작하기를 누르면 정해진 때에 다시 보내요")
        elif last and last.get("ok"):
            self.verdict.set_verdict(
                "ok", "잘 되고 있어요",
                f"{wording.when(last['at'])}에 건너갔어요 · {wording.size(last.get('size'))} · "
                f"{wording.duration(last.get('elapsed') or 0)}")
        else:
            self.verdict.set_verdict("none", "아직 보낸 적이 없어요", "정해진 때가 되면 첫 전송을 시작해요")

        self.send_btn.setEnabled(not running)
        self.pause_btn.setText("다시 시작하기" if paused else "잠시 멈추기")

        self.folder_path.setText(s.source_dir or "(아직 정하지 않았어요)")
        if full:
            self.folder_summary.setText(f"세는 중이에요 · {wording.schedule_name(s.interval_minutes)}")
            self._stats.start(s.source_dir)

        targets = list(s.taildrop_targets) if s.taildrop_targets else ([s.host] if s.host else [])
        self.targets_title.setText(f"받는 컴퓨터 {len(targets)}대" if targets else "받는 컴퓨터")
        while self.targets_rows.count():
            it = self.targets_rows.takeAt(0)
            if it.layout():
                while it.layout().count():
                    sub = it.layout().takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()
        for t in targets:
            seen = next((r for r in rows if r.get("ok") and r.get("detail") == t), None)
            when = label(wording.when_short(seen["at"]) if seen else "아직 없어요", "caption")
            when.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row = hbox(Dot("pr" if seen else "bd2"), ElidedLabel(t, mode=Qt.ElideRight), when, spacing=6)
            row.setStretch(1, 1)
            self.targets_rows.addLayout(row)
        if not targets:
            self.targets_rows.addLayout(hbox(label("(아직 없어요)", "body2")))

        work = Path(s.work_dir) if s.work_dir else None
        kept = list(work.rglob("*.7z")) if work and work.is_dir() else []
        self.kept.setText(f"남겨 둔 압축 {len(kept)}개 · {wording.size(sum(p.stat().st_size for p in kept))}"
                          if kept else "")

        self.history_card.clear()
        for r in rows[:5]:
            _history_row(self.history_card, r)
        self.history_card.set_empty("" if rows else "아직 보낸 적이 없어요. 첫 전송이 끝나면 여기에 보여요.")

    def _show_stats(self, folder: str, dirs: int, total: int) -> None:
        if folder != self.cfg.sender.source_dir:
            return
        every = wording.schedule_name(self.cfg.sender.interval_minutes)
        self.folder_summary.setText(f"{dirs}개 폴더 · {wording.size(total)} · {every}"
                                    if Path(folder).is_dir() else f"폴더를 찾을 수 없어요 · {every}")

    def _show_error(self, last: dict) -> None:
        self.views.setCurrentIndex(1)
        target = (self.cfg.sender.taildrop_targets or [self.cfg.sender.host or "받는 컴퓨터"])[0]
        self.err_verdict.set_verdict("err", "건너가지 못했어요",
                                     f"{wording.when(last['at'])} · {target}에 닿지 못했어요")
        self.err_verdict.setToolTip(last.get("detail") or "")
        pend = Path(self.cfg.sender.work_dir or ".") / "pending"
        parked = list(pend.glob("*.7z")) if pend.is_dir() else []
        if parked:
            size = wording.size(sum(p.stat().st_size for p in parked))
            self.err_note.text.setText(f"만들어 둔 압축({size})은 그대로 있어요. 다음 전송 때 같이 보내요.")
        else:
            self.err_note.text.setText("다음 전송 때 다시 보내요.")
        self.retry_btn.setEnabled(not self.engine.busy)

    def set_progress(self, phase: str, pct: int) -> None:
        self._progress = (phase, pct)
        if self.engine.busy:
            self.refresh()


class ReceiverHome(_Home):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__(win)
        # (device_id, last_seen) pairs dismissed with 확인했어요 - keyed on
        # last_seen so a sender that returns and goes quiet again is a new
        # silence. Kept on the engine object because the tray reads it too:
        # 확인했어요 also turns off the tray's 주의 badge (§03).
        if not hasattr(self.engine, "acked_silence"):
            self.engine.acked_silence = set()
        self._acked: set[tuple[str, float]] = self.engine.acked_silence
        self._shown: tuple[str, float] | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.views = QStackedWidget()
        outer.addWidget(self.views)
        self.views.addWidget(self._build_normal())
        self.views.addWidget(self._build_empty())
        self.refresh()

    def _build_normal(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        self.verdict = VerdictCard()
        self.ack_btn = button("확인했어요")
        self.ack_btn.clicked.connect(self._ack)
        self.verdict.set_right(self.ack_btn)
        lay.addWidget(self.verdict)

        self.stat_stored = StatTile("보관 중")
        self.stat_space = StatTile("쓴 공간")
        self.stat_senders = StatTile("보내는 컴퓨터")
        lay.addLayout(hbox(self.stat_stored, self.stat_space, self.stat_senders, spacing=16))

        self.received = ListCard("건너온 것", "폴더 열기")
        self.received.action.clicked.connect(lambda: _open_path(self.cfg.receiver.unpack_dir))
        lay.addWidget(self.received)

        pair = button("짝 코드 만들기")
        pair.clicked.connect(self.win._open_pairing_dialog)
        self.poll_dot = Dot("pr")
        self.poll_note = label("", "caption")
        lay.addLayout(hbox(pair, None, self.poll_dot, self.poll_note, spacing=4))
        lay.addStretch(1)
        return w

    def _build_empty(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addStretch(2)
        lay.addWidget(LogoMark(80, alpha=0.45), 0, Qt.AlignHCenter)
        lay.addSpacing(8)
        t = label("아직 건너온 게 없어요", "title")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        d = label("보내는 컴퓨터에서 첫 전송이 끝나면 여기에 보여요. 보내는 쪽에서 정한 시각에 와요.",
                  "body2", wrap=True)
        d.setAlignment(Qt.AlignCenter)
        lay.addWidget(d)
        lay.addSpacing(8)
        pair = button("짝 코드 만들기")
        pair.clicked.connect(self.win._open_pairing_dialog)
        open_dir = button("받는 폴더 열기", "text")
        open_dir.clicked.connect(lambda: _open_path(self.cfg.receiver.incoming_dir))
        lay.addLayout(hbox(None, pair, open_dir, None, spacing=8))
        lay.addStretch(3)
        return w

    def _ack(self) -> None:
        if self._shown:
            self._acked.add(self._shown)
        self.refresh()

    def refresh(self) -> None:
        r = self.cfg.receiver
        unpack_dir = Path(r.unpack_dir) if r.unpack_dir else None
        try:
            folders = sorted((p for p in unpack_dir.iterdir() if p.is_dir()),
                             key=lambda p: p.stat().st_mtime, reverse=True) \
                if unpack_dir and unpack_dir.is_dir() else []
        except OSError:
            folders = []
        failed = dict(getattr(self.engine.receiver, "failed", {}))
        if not folders and not failed:
            self.views.setCurrentIndex(1)
            return
        self.views.setCurrentIndex(0)

        known_path = config_dir() / pairing.KNOWN_SENDERS_FILENAME
        registry = pairing.load_known_senders(known_path)
        overdue = pairing.overdue_senders(registry)
        unacked = [o for o in overdue if (o["device_id"], o.get("last_seen")) not in self._acked]

        if unacked:
            worst = unacked[0]
            name = worst.get("device_name", worst["device_id"])
            self.verdict.set_verdict(
                "warn", f"{name}에서 {wording.days_since(worst['last_seen'])}일째 오지 않아요",
                f"{wording.schedule_name(worst.get('interval_minutes'))} 오기로 했어요. "
                "그 컴퓨터가 꺼져 있는지 확인해 주세요.")
            self._shown = (worst["device_id"], worst.get("last_seen"))
            self.ack_btn.show()
        elif failed and (not folders or max(failed.values()) > folders[0].stat().st_mtime):
            self.verdict.set_verdict("err", "풀지 못한 압축이 있어요",
                                     "다음 전송 때 다시 와요. 계속되면 자세한 기록을 열어 보세요.")
            self._shown = None
            self.ack_btn.hide()
        else:
            newest = folders[0]
            self.verdict.set_verdict(
                "ok", "잘 되고 있어요",
                f"{wording.when(newest.stat().st_mtime)}에 건너왔어요 · {_sender_of(newest, registry)}")
            self._shown = None
            self.ack_btn.hide()

        sizes = {f: dir_size(f) for f in folders}
        if folders:
            oldest = min(folders, key=lambda p: p.stat().st_mtime)
            self.stat_stored.set(str(len(folders)), f"가장 오래된 것 {wording.month_day(oldest.stat().st_mtime)}")
        else:
            self.stat_stored.set("0", "")
        try:
            free = shutil.disk_usage(unpack_dir).free if unpack_dir and unpack_dir.is_dir() else None
        except OSError:
            free = None
        self.stat_space.set(wording.size(sum(sizes.values())),
                            f"남은 공간 {wording.size(free)}" if free is not None else "")
        self.stat_senders.set(str(len(registry)), f"{len(overdue)}대 소식 없음" if overdue else "",
                              "wn" if overdue else None)

        self.received.clear()
        rows = [(f.stat().st_mtime, "ok", f) for f in folders[:20]]
        rows += [(t, "err", name) for name, t in failed.items()]
        for when_ts, kind, item in sorted(rows, key=lambda x: x[0], reverse=True)[:20]:
            if kind == "ok":
                self.received.add_row("pr", wording.when_short(when_ts), _sender_of(item, registry),
                                      wording.size(sizes[item]), "풀어 뒀어요")
            else:
                self.received.add_row("er", wording.when_short(when_ts), item, "",
                                      "풀지 못했어요", "er")
        self.refresh_poll()

    def refresh_poll(self) -> None:
        polling = self.engine.running and not self.engine.paused
        self.poll_dot.set_tone("pr" if polling else "bd2")
        self.poll_note.setText("5초마다 확인하고 있어요" if polling else "확인을 멈췄어요")


# ------------------------------------------------------------ window


class MainWindow(QMainWindow):
    def __init__(self, cfg, log, engine, on_quit) -> None:
        super().__init__()
        self.cfg = cfg
        self.log = log
        self.engine = engine
        self._on_quit = on_quit
        self._really_quit = False

        self.setWindowTitle("나루")
        self.setWindowIcon(window_icon())
        self.setMinimumSize(720, 560)
        self.resize(760, 640)

        page = page_widget()
        self.root = QVBoxLayout(page)
        self.root.setContentsMargins(24, 20, 24, 24)
        self.root.setSpacing(16)

        self.role_pill = QLabel()
        self.role_pill.setProperty("pill", "role")
        self.host_label = label(socket.gethostname(), "body2")
        settings = button("설정", "text")
        settings.clicked.connect(self._open_settings)
        self.root.addLayout(hbox(self.role_pill, 4, self.host_label, None, settings, spacing=8))

        self.body = QVBoxLayout()
        self.root.addLayout(self.body, 1)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setWidget(page)
        self.setCentralWidget(area)

        self.home: SenderHome | ReceiverHome | None = None
        self._stat_timer = QTimer(self)
        self._stat_timer.timeout.connect(self._refresh_receiver_stats)
        self._wire_engine()
        self._build_role_body()

    # role -------------------------------------------------------------

    def _build_role_body(self) -> None:
        if self.home is not None:
            self.home.setParent(None)
            self.home.deleteLater()
        self._stat_timer.stop()
        is_sender = self.cfg.role == ROLE_SENDER
        self.role_pill.setText("보내는 컴퓨터" if is_sender else "받는 컴퓨터")
        self.home = SenderHome(self) if is_sender else ReceiverHome(self)
        self.body.addWidget(self.home)
        if not is_sender:
            self._stat_timer.start(STAT_CHECK_INTERVAL_MS)

    def _refresh_role_ui(self) -> None:
        self._build_role_body()

    def _refresh_receiver_stats(self) -> None:
        if isinstance(self.home, ReceiverHome):
            self.home.refresh()

    def _refresh_sender_info(self) -> None:
        if isinstance(self.home, SenderHome):
            self.home.refresh(full=True)

    # engine -----------------------------------------------------------

    def _wire_engine(self) -> None:
        self.engine.status.connect(self._on_status)
        self.engine.progress.connect(self._on_progress)
        self.engine.run_finished.connect(self._on_run_finished)
        self.engine.next_due.connect(lambda _d: self._on_status(""))

    def _on_status(self, _text: str) -> None:
        if isinstance(self.home, SenderHome):
            self.home.refresh()
        elif isinstance(self.home, ReceiverHome):
            self.home.refresh_poll()

    def _on_progress(self, phase: str, pct: int) -> None:
        if isinstance(self.home, SenderHome):
            self.home.set_progress(phase, pct)

    def _on_run_finished(self, _result) -> None:
        if isinstance(self.home, SenderHome):
            self.home.refresh(full=True)
        else:
            self._refresh_receiver_stats()

    # actions ----------------------------------------------------------

    def _run_now(self) -> None:
        problems = self.cfg.problems()
        if problems:
            QMessageBox.warning(self, "설정이 필요해요", "\n".join(problems))
            return
        if not self.engine.running:
            self.engine.start()
        self.engine.run_now()

    def _toggle_pause(self) -> None:
        if not self.engine.running:
            self.engine.start()
            return
        if self.engine.paused:
            self.engine.resume()
        else:
            self.engine.pause()

    def _open_records(self) -> None:
        RecordDialog(self.log, self, runs=self.cfg.role == ROLE_SENDER).exec()

    def _open_pairing_dialog(self) -> None:
        PairingCodeDialog(self.cfg, self, log=self.log.line).exec()
        self._refresh_receiver_stats()

    def _open_settings(self) -> None:
        from .settings_window import SettingsWindow

        SettingsWindow(self.cfg, self.log, self.engine, self, on_role_changed=self._build_role_body,
                       on_changed=self._settings_changed).exec()

    def _settings_changed(self) -> None:
        if isinstance(self.home, SenderHome):
            self.home.refresh(full=True)
        else:
            self._refresh_receiver_stats()

    # close ------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._really_quit or not self.cfg.minimize_to_tray:
            self._on_quit()
            event.accept()
            return
        event.ignore()
        self.hide()
        self.log.line("창을 닫았어요. 나루는 트레이에서 계속 일해요.")

    def _quit(self) -> None:
        self._really_quit = True
        self.close()

