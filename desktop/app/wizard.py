"""나루 설정 - the first-run wizard, as drawn in 나루 디자인 시스템 §09 설치 마법사.

    0  이 컴퓨터는 어느 쪽인가요?            two choice cards
    sender    1 받는 컴퓨터에 뜬 코드를 넣어 주세요   code input, connects when complete
              2 어떤 폴더를 보낼까요?                path + 바꾸기, three tiles
              3 얼마나 자주 보낼까요?                option rows, 하루에 한 번 추천
              4 건너가는지 확인하고 있어요 -> 잘 건너갔어요   the real test transfer
    receiver  1 보내는 컴퓨터에 이 코드를 넣어 주세요   teal code panel, waiting line

One question and one main button (bottom right) per page; 이전 bottom left;
step dots on top; pages turn with the 250ms slide (widgets.SlideStack).
The code is consumed by /pair on page 1; the interval chosen on page 3
reaches the receiver with /confirm (pairing.confirm_test_transfer).

Launched by main.py before MainWindow whenever cfg.onboarded is False.
There is no cancel button (onboarding is mandatory), but closing the window
still releases the pairing listener's socket: accept() and reject() both
clean up. The test transfer runs on a QThread so it never freezes the page.
"""

from __future__ import annotations

import socket
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from tsbackup import pairing
from tsbackup.config import ROLE_RECEIVER, ROLE_SENDER, config_dir

from . import wording
from .icons import window_icon
from .theme import set_prop
from .widgets import (
    ChoiceCard, CodePanel, OptionRow, SlideStack, StatTile, StatusLine, StepDots, StepList,
    button, hbox, label, page_widget, set_code_font,
)

PAGE_ROLE, PAGE_PAIR, PAGE_FOLDER, PAGE_SCHEDULE, PAGE_TEST, PAGE_CODE = range(6)
SENDER_PAGES = [PAGE_ROLE, PAGE_PAIR, PAGE_FOLDER, PAGE_SCHEDULE, PAGE_TEST]
RECEIVER_PAGES = [PAGE_ROLE, PAGE_CODE]

SCHEDULE_PRESETS = [   # (minutes, badge)
    (360, ""),
    (1440, "추천"),
    (10080, ""),
]

# pairing.run_test_transfer()'s step keys, in order, as the page says them.
STEPS = [("압축", "작은 시험 파일 만들기"),
         ("전송", "건너편으로 보내기"),
         ("수신·해제 확인", "받는 컴퓨터가 풀어 보기")]


class _TestTransferWorker(QObject):
    """Qt wrapper around pairing.run_test_transfer() - the logic lives there,
    Qt-free, covered by the headless selftest. This only runs it on a worker
    thread and turns its step()/return value into signals."""

    step = Signal(str, str)        # label, "running" | "ok" | "fail"
    finished = Signal(bool, str)   # ok, detail

    def __init__(self, cfg, code: str, confirm_token: str, log=None) -> None:
        super().__init__()
        self._log = log or (lambda _line: None)
        self._cfg = cfg
        self._code = code
        self._confirm_token = confirm_token

    def run(self) -> None:
        ok, detail = pairing.run_test_transfer(
            self._cfg.sender, self._code, self._confirm_token, step=self.step.emit, log=self._log)
        self.finished.emit(ok, detail)


def _copula(text: str) -> str:
    """"오늘 새벽 4시" + 예요, "오늘 새벽 4:26" + 이에요."""
    return text + ("예요" if text.endswith("시") else "이에요")


def _valid_code(text: str) -> bool:
    raw = text.strip().upper().replace("-", "").replace(" ", "")
    return len(raw) == pairing.CODE_CHARS


class SetupWizard(QDialog):
    def __init__(self, cfg, parent=None, log=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self._log = log
        self.setWindowTitle("나루 설정")
        self.setWindowIcon(window_icon())
        self.setMinimumSize(720, 560)
        self.resize(720, 560)

        self._listener: pairing.PairingListener | None = None
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_paired)
        self._pair_debounce = QTimer(self)
        self._pair_debounce.setSingleShot(True)
        self._pair_debounce.setInterval(400)
        self._pair_debounce.timeout.connect(self._resolve_code)
        self._thread: QThread | None = None
        self._worker: _TestTransferWorker | None = None
        self._pair_payload: dict = {}
        self._pair_code = ""
        self._paired_code = ""
        self._target = ""
        self._step_started: dict[str, float] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(40, 28, 40, 28)
        root.setSpacing(0)
        self.dots = StepDots(5)
        root.addWidget(self.dots)
        root.addSpacing(36)
        self.stack = SlideStack()
        root.addWidget(self.stack, 1)

        self.stack.addWidget(self._build_role_page())       # PAGE_ROLE
        self.stack.addWidget(self._build_pair_page())       # PAGE_PAIR
        self.stack.addWidget(self._build_folder_page())     # PAGE_FOLDER
        self.stack.addWidget(self._build_schedule_page())   # PAGE_SCHEDULE
        self.stack.addWidget(self._build_test_page())       # PAGE_TEST
        self.stack.addWidget(self._build_code_page())       # PAGE_CODE
        self._show_dots()

    # ------------------------------------------------------------ frame

    def _page(self, title: str, sub: str) -> tuple[QWidget, QVBoxLayout, QHBoxLayout]:
        w = page_widget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        t = label(title, "title", wrap=True)
        lay.addWidget(t)
        lay.addSpacing(8)
        s = label(sub, "body2", wrap=True)
        lay.addWidget(s)
        lay.addSpacing(28)
        body = QVBoxLayout()
        body.setSpacing(10)
        lay.addLayout(body)
        lay.addStretch(1)
        row = QHBoxLayout()
        row.setSpacing(8)
        lay.addLayout(row)
        w.title_label = t     # type: ignore[attr-defined]
        w.sub_label = s       # type: ignore[attr-defined]
        return w, body, row

    def _back(self, to: int) -> object:
        b = button("이전", "quiet")
        b.clicked.connect(lambda: self._go(to))
        return b

    def _flow(self) -> list[int]:
        return SENDER_PAGES if self.cfg.role == ROLE_SENDER else RECEIVER_PAGES

    def _go(self, page: int) -> None:
        self.stack.slide_to(page)
        self._show_dots()

    def _show_dots(self) -> None:
        flow = self._flow()
        cur = self.stack.currentIndex()
        self.dots.set_step(flow.index(cur) if cur in flow else 0, len(flow))

    # -------------------------------------------------------- 0 · role

    def _build_role_page(self) -> QWidget:
        w, body, row = self._page("이 컴퓨터는 어느 쪽인가요?",
                                  "컴퓨터마다 한 번씩 정해요. 나중에 설정에서 바꿀 수 있어요.")
        self.role_sender = ChoiceCard("↑", "보내는 컴퓨터",
                                      "작업 폴더를 정해진 때마다 압축해서 건너편으로 보내요.",
                                      "평소 작업하는 PC")
        self.role_receiver = ChoiceCard("↓", "받는 컴퓨터",
                                        "건너온 압축을 날짜별로 풀어서 보관해요. 켜 두기만 하면 돼요.",
                                        "늘 켜 두는 PC")
        self.role_sender.clicked.connect(lambda: self._pick_role(ROLE_SENDER))
        self.role_receiver.clicked.connect(lambda: self._pick_role(ROLE_RECEIVER))
        body.addLayout(hbox(self.role_sender, self.role_receiver, spacing=16))
        self._pick_role(ROLE_SENDER)
        row.addStretch(1)
        nxt = button("다음", "primary", large=True)
        nxt.clicked.connect(self._role_next)
        row.addWidget(nxt)
        return w

    def _pick_role(self, role: str) -> None:
        self.cfg.role = role
        self.role_sender.setSelected(role == ROLE_SENDER)
        self.role_receiver.setSelected(role == ROLE_RECEIVER)
        if hasattr(self, "dots"):
            self._show_dots()

    def _role_next(self) -> None:
        if self.cfg.role == ROLE_SENDER:
            self._go(PAGE_PAIR)
            self.code_entry.setFocus()
        else:
            self._go(PAGE_CODE)
            self._start_receiver_pairing()

    # ------------------------------------------------ sender 1 · code

    def _build_pair_page(self) -> QWidget:
        w, body, row = self._page("받는 컴퓨터에 뜬 코드를 넣어 주세요",
                                  "받는 컴퓨터에서 나루를 먼저 열면 코드가 보여요.")
        self.code_entry = QLineEdit()
        self.code_entry.setProperty("kind", "code")
        self.code_entry.setPlaceholderText("XXXXX-XXXX")
        self.code_entry.setMaxLength(12)
        set_code_font(self.code_entry, 24, entry=True)
        self.code_entry.textEdited.connect(self._code_edited)
        self.code_entry.returnPressed.connect(self._resolve_code)
        body.addWidget(self.code_entry)
        self.pair_result = label("", "caption2", wrap=True)
        body.addWidget(self.pair_result)
        row.addWidget(self._back(PAGE_ROLE))
        row.addStretch(1)
        self.pair_next = button("다음", "primary", large=True)
        self.pair_next.setEnabled(False)
        self.pair_next.clicked.connect(lambda: self._go(PAGE_FOLDER))
        row.addWidget(self.pair_next)
        return w

    def _code_edited(self, text: str) -> None:
        upper = text.upper()
        if upper != text:
            pos = self.code_entry.cursorPosition()
            self.code_entry.setText(upper)
            self.code_entry.setCursorPosition(pos)
        if self._paired_code and upper.strip() != self._paired_code:
            self._paired_code = ""
        self.pair_next.setEnabled(bool(self._paired_code))
        set_prop(self.code_entry, "state", None)
        self.pair_result.setText("")
        if _valid_code(upper):
            self._pair_debounce.start()

    def _resolve_code(self) -> None:
        code = self.code_entry.text().strip().upper()
        if not code or code == self._paired_code:
            return
        if not _valid_code(code):
            self._show_pair_error("코드는 9자리예요. 받는 컴퓨터에 뜬 코드를 그대로 넣어 주세요.")
            return
        self.pair_result.setText("연결해 보고 있어요...")
        set_prop(self.pair_result, "tone", "tl")
        QApplication.processEvents()
        device_id = self.cfg.ensure_device_id()
        try:
            payload = pairing.resolve_and_pair(
                code,
                device_name=socket.gethostname(),
                device_id=device_id,
                interval_minutes=self.cfg.sender.interval_minutes or 1440,
                transport_preference="taildrop",
            )
        except pairing.PairingError as exc:
            # The screen gets the plain sentence; the cause underneath (a
            # socket error, an HTTP status) goes to the log.
            if self._log and exc.__cause__ is not None:
                self._log(f"짝 코드 연결 실패: {exc} ({exc.__cause__})")
            self._show_pair_error(f"연결하지 못했어요. {exc}")
            return

        self._pair_payload = payload
        self._pair_code = code
        self._paired_code = code
        self._target = payload.get("device_name", "")
        s = self.cfg.sender
        s.transport = "taildrop"
        if self._target:
            s.taildrop_targets = [self._target]
        ip, _secret = pairing.unpack_code(code)
        s.host = ip
        fp = payload.get("host_key_fingerprint")
        if fp:
            s.host_key = fp
        set_prop(self.code_entry, "state", "ok")
        self.pair_result.setText(f"✓ {self._target}와 연결할 수 있어요")
        set_prop(self.pair_result, "tone", "tl")
        self.pair_next.setEnabled(True)

    def _show_pair_error(self, text: str) -> None:
        set_prop(self.code_entry, "state", "error")
        self.pair_result.setText(text)
        set_prop(self.pair_result, "tone", "er")
        self.pair_next.setEnabled(False)

    # ---------------------------------------------- sender 2 · folder

    def _build_folder_page(self) -> QWidget:
        w, body, row = self._page("어떤 폴더를 보낼까요?", "폴더 안의 파일을 빠짐없이 그대로 보내요.")
        self.source_edit = QLineEdit(self.cfg.sender.source_dir or self._default_folder())
        self.source_edit.setReadOnly(True)
        self.source_edit.setFocusPolicy(Qt.NoFocus)
        change = button("바꾸기")
        change.clicked.connect(self._browse_source)
        body.addLayout(hbox(self.source_edit, change, spacing=12))
        self.folder_error = label("이 폴더를 찾을 수 없어요. 다시 골라 주세요", "caption2", "er")
        self.folder_error.hide()
        body.addWidget(self.folder_error)
        self.tile_dirs = StatTile("하위 폴더")
        self.tile_size = StatTile("크기")
        self.tile_eta = StatTile("첫 전송")
        for t in (self.tile_dirs, self.tile_size, self.tile_eta):
            t.value.setProperty("role", "numberSm")
            t.detail.hide()
        body.addLayout(hbox(self.tile_dirs, self.tile_size, self.tile_eta, spacing=12))
        row.addWidget(self._back(PAGE_PAIR))
        row.addStretch(1)
        self.folder_next = button("다음", "primary", large=True)
        self.folder_next.clicked.connect(self._folder_next)
        row.addWidget(self.folder_next)

        from .workers import FolderStats
        self._stats = FolderStats()
        self._stats.done.connect(self._show_folder_stats)
        self._folder_changed()
        return w

    @staticmethod
    def _default_folder() -> str:
        guess = Path.home() / "PycharmProjects"
        return str(guess) if guess.is_dir() else ""

    def _browse_source(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "보낼 폴더 고르기", self.source_edit.text() or "")
        if chosen:
            self.source_edit.setText(str(Path(chosen)))
            self._folder_changed()

    def _folder_changed(self) -> None:
        text = self.source_edit.text().strip()
        ok = bool(text) and Path(text).is_dir()
        self.folder_next.setEnabled(ok)
        set_prop(self.source_edit, "state", None if ok or not text else "error")
        self.folder_error.setVisible(bool(text) and not ok)
        if not text:
            self.source_edit.setPlaceholderText("보낼 폴더를 골라 주세요")
        for t in (self.tile_dirs, self.tile_size, self.tile_eta):
            t.value.setText("…" if ok else "-")
        if ok:
            self._stats.start(text)

    def _show_folder_stats(self, folder: str, dirs: int, total: int) -> None:
        if folder != self.source_edit.text().strip():
            return
        self.tile_dirs.value.setText(f"{dirs}개")
        self.tile_size.value.setText(wording.size(total))
        self.tile_eta.value.setText(wording.first_send_estimate(total))

    def _folder_next(self) -> None:
        self.cfg.sender.source_dir = self.source_edit.text().strip()
        if not self.cfg.sender.work_dir:
            self.cfg.sender.work_dir = str(Path(tempfile.gettempdir()) / "NaruWork")
        self._go(PAGE_SCHEDULE)

    # -------------------------------------------- sender 3 · how often

    def _build_schedule_page(self) -> QWidget:
        w, body, row = self._page("얼마나 자주 보낼까요?", "그때 컴퓨터가 꺼져 있으면, 다음에 켤 때 보내요.")
        self.schedule_rows: dict[int, OptionRow] = {}
        at = self.cfg.sender.at_time
        for minutes, badge in SCHEDULE_PRESETS:
            r = OptionRow(wording.schedule_name(minutes), wording.schedule_note(minutes, at), badge)
            r.clicked.connect(lambda m=minutes: self._pick_schedule(m))
            self.schedule_rows[minutes] = r
            body.addWidget(r)
        self._pick_schedule(1440)
        row.addWidget(self._back(PAGE_FOLDER))
        row.addStretch(1)
        go = button("시험 전송하기", "primary", large=True)
        go.clicked.connect(self._schedule_next)
        row.addWidget(go)
        return w

    def _pick_schedule(self, minutes: int) -> None:
        self._schedule = minutes
        for m, r in self.schedule_rows.items():
            r.setSelected(m == minutes)

    def _schedule_minutes(self) -> int:
        return self._schedule

    def _schedule_next(self) -> None:
        self.cfg.sender.interval_minutes = self._schedule
        self._go(PAGE_TEST)
        self._run_test_transfer()

    # ---------------------------------------------- sender 4 · test

    def _build_test_page(self) -> QWidget:
        w, body, row = self._page("건너가는지 확인하고 있어요",
                                  "작은 파일 하나로 처음부터 끝까지 한 번 해 볼게요.")
        self.test_page = w
        self.steps = StepList([name for _key, name in STEPS])
        body.addWidget(self.steps)
        # Hidden while the test runs (the design's wzShowBack): leaving the
        # page would strand the worker thread mid-transfer.
        self.test_back = self._back(PAGE_SCHEDULE)
        row.addWidget(self.test_back)
        row.addStretch(1)
        self.test_finish = button("확인하는 중", "primary", large=True)
        self.test_finish.setEnabled(False)
        self.test_finish.clicked.connect(self._test_primary)
        row.addWidget(self.test_finish)
        self._test_ok = False
        return w

    def _run_test_transfer(self) -> None:
        self.steps.reset()
        self._test_ok = False
        self._step_started.clear()
        self.test_page.title_label.setText("건너가는지 확인하고 있어요")
        self.test_page.sub_label.setText("작은 파일 하나로 처음부터 끝까지 한 번 해 볼게요.")
        self.test_finish.setText("확인하는 중")
        self.test_finish.setEnabled(False)
        self.test_back.hide()

        confirm_token = self._pair_payload.get("confirm_token", "")
        thread = QThread(self)
        worker = _TestTransferWorker(self.cfg, self._pair_code, confirm_token, log=self._log)
        worker.moveToThread(thread)
        worker.step.connect(self._on_test_step)
        worker.finished.connect(self._on_test_finished)
        thread.started.connect(worker.run)
        self._thread, self._worker = thread, worker
        thread.start()

    def _on_test_step(self, key: str, status: str) -> None:
        keys = [k for k, _ in STEPS]
        if key not in keys:
            return
        i = keys.index(key)
        if status == "running":
            self._step_started[key] = time.monotonic()
            self.steps.set_step(i, "run", "보내는 중" if key == "전송" else "하는 중")
            return
        took = time.monotonic() - self._step_started.get(key, time.monotonic())
        if status == "ok":
            note = wording.duration(took)
            if key == "전송" and self._target:
                note += f" · {self._target}"
            if key == "수신·해제 확인":
                note = f"{wording.duration(took)} 만에 확인"
            self.steps.set_step(i, "ok", note)
        else:
            self.steps.set_step(i, "err", "안 됐어요")

    def _on_test_finished(self, ok: bool, detail: str) -> None:
        if self._thread:
            self._thread.quit()
            self._thread.wait()
        self._thread = None
        self._worker = None
        self._test_ok = ok
        if ok:
            from datetime import datetime

            from tsbackup import schedule

            s = self.cfg.sender
            nxt = schedule.next_slot(datetime.now(), s.interval_minutes, s.at_time).timestamp()
            self.test_page.title_label.setText("잘 건너갔어요")
            self.test_page.sub_label.setText(
                f"다음 전송은 {_copula(wording.when(nxt))}. 창을 닫아도 나루는 트레이에서 계속 일해요.")
            self.test_finish.setText("마침")
        else:
            self.test_page.title_label.setText("건너가지 못했어요")
            self.test_page.sub_label.setText(
                f"{detail} 받는 컴퓨터가 켜져 있는지 확인하고 다시 해 주세요.")
            self.test_finish.setText("다시 해 보기")
        self.test_finish.setEnabled(True)
        self.test_back.show()

    def _test_primary(self) -> None:
        if self._test_ok:
            self.accept()
        else:
            self._run_test_transfer()

    # ---------------------------------------------- receiver 1 · code

    def _build_code_page(self) -> QWidget:
        w, body, row = self._page("보내는 컴퓨터에 이 코드를 넣어 주세요",
                                  "같은 Tailscale 네트워크에 있는 컴퓨터에서만 쓸 수 있어요.")
        self.code_panel = CodePanel()
        self.code_panel.copy.connect(self._copy_receiver_code)
        self.code_panel.renew.connect(self._regenerate_receiver_code)
        self.code_label = self.code_panel.code
        body.addWidget(self.code_panel)
        body.addSpacing(4)
        self.pair_status = StatusLine()
        body.addWidget(self.pair_status)
        row.addWidget(self._back(PAGE_ROLE))
        row.addStretch(1)
        self.receiver_finish_anyway = button("시험 없이 마치기", "text")
        self.receiver_finish_anyway.setVisible(False)
        self.receiver_finish_anyway.clicked.connect(self._finish_without_confirm)
        row.addWidget(self.receiver_finish_anyway)
        self.receiver_finish = button("마침", "primary", large=True)
        self.receiver_finish.setEnabled(False)
        self.receiver_finish.clicked.connect(self.accept)
        row.addWidget(self.receiver_finish)
        return w

    def _start_receiver_pairing(self) -> None:
        ip = pairing.local_tailscale_ip()
        if not ip:
            self.code_panel.set_code("—")
            self.pair_status.set("err", "Tailscale이 꺼져 있거나 로그인이 안 돼 있어요. "
                                        "켜고 나서 새 코드를 눌러 주세요.")
            return
        r = self.cfg.receiver
        if not r.incoming_dir:
            r.incoming_dir = str(Path.home() / "Downloads" / "NaruIncoming")
        if not r.unpack_dir:
            r.unpack_dir = str(Path.home() / "NaruUnpacked")
        Path(r.incoming_dir).mkdir(parents=True, exist_ok=True)
        Path(r.unpack_dir).mkdir(parents=True, exist_ok=True)
        if self._listener is None:
            known_path = config_dir() / pairing.KNOWN_SENDERS_FILENAME
            self._listener = pairing.PairingListener(self.cfg, known_path, log=self._log)
            self.code_panel.set_code(self._listener.start(ip))
        else:
            self.code_panel.set_code(self._listener.regenerate())
        self.pair_status.set("wait", "보내는 컴퓨터를 기다리고 있어요")
        self._poll_timer.start(1000)

    def _regenerate_receiver_code(self) -> None:
        if not self._listener:
            self._start_receiver_pairing()
            return
        self.code_panel.set_code(self._listener.regenerate())
        self.pair_status.set("wait", "보내는 컴퓨터를 기다리고 있어요")
        self.receiver_finish.setEnabled(False)
        self.receiver_finish_anyway.setVisible(False)
        if not self._poll_timer.isActive():
            self._poll_timer.start(1000)

    def _copy_receiver_code(self) -> None:
        text = self.code_panel.code.text()
        if text and text != "—":
            QApplication.clipboard().setText(text)
            self.code_panel.copy_btn.setText("복사했어요")
            QTimer.singleShot(1500, lambda: self.code_panel.copy_btn.setText("복사"))

    def _finish_without_confirm(self) -> None:
        """Escape hatch for a sender that never completes - without it the
        receiver would be stuck on a code that only offers 새 코드.
        Deliberately secondary to 마침: it asks first, and finishes
        regardless of is_confirmed()."""
        from PySide6.QtWidgets import QMessageBox

        resp = QMessageBox.question(
            self, "시험 없이 마치기",
            "보내는 컴퓨터의 시험 파일을 아직 받지 못했어요. 그래도 마칠까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp == QMessageBox.StandardButton.Yes:
            self.accept()

    def _poll_paired(self) -> None:
        if not self._listener:
            return
        # is_paired() alone is not "done": the sender's mandatory test
        # transfer still needs this listener alive to answer /confirm.
        # Enabling 마침 (which stops the listener) before that strands the
        # sender. Keep waiting, and say so, until is_confirmed().
        if self._listener.is_confirmed():
            self.pair_status.set("ok", "✓ 시험 파일까지 잘 받았어요. 이제 마쳐도 돼요.")
            self.receiver_finish.setEnabled(True)
            self.receiver_finish_anyway.setVisible(False)
            self._poll_timer.stop()
        elif self._listener.is_expired():
            self.pair_status.set("warn", "코드가 만료됐어요. 새 코드를 만들어 주세요.")
            self.receiver_finish_anyway.setVisible(True)
            self._poll_timer.stop()
        elif self._listener.is_paired():
            self.pair_status.set("linked", "연결됐어요. 시험 파일이 오기를 기다리고 있어요.")

    # ------------------------------------------------------------- close

    def accept(self) -> None:
        self._cleanup()
        self.cfg.onboarded = True
        # Finishing the wizard means "ready to run". Without this the
        # engine's loop never started on its own (autostart_engine defaults
        # to off), so a fresh install never ran a scheduled backup.
        self.cfg.autostart_engine = True
        self.cfg.save()
        super().accept()

    def reject(self) -> None:
        # No cancel button - onboarding is mandatory - but the window can
        # still be closed (Alt+F4 / ✕), which must not leak the listener.
        self._cleanup()
        super().reject()

    def _cleanup(self) -> None:
        self._poll_timer.stop()
        self._pair_debounce.stop()
        if self._listener:
            self._listener.stop()
            self._listener = None
        if self._thread:
            self._thread.quit()
            self._thread.wait()

