"""First-run wizard, one question per page.

Laid out by design/FastAPI 관리화면 설계/나루 디자인 참고 조사: a page asks
one thing and has one main button (bottom right); secondary actions are
text buttons; no transport names or fingerprints; 해요체; the last page is
a real test transfer, not "saved".

    0  role       이 컴퓨터는 어떤 일을 하나요?
    receiver      1 ready (Tailscale on, why the firewall prompt matters)
                  2 code  (show it, wait for the sender's test file)
    sender        3 folder  4 how often  5 code  6 test transfer

The sender enters the code last: the pairing request carries the chosen
interval to the receiver (its silence detection uses it), and the code's
10-minute life then only has to cover typing it in.

Launched by main.py before MainWindow whenever cfg.onboarded is False.
There is no cancel button (onboarding is mandatory), but closing the window
still has to release the pairing listener's socket, so both accept() and
reject() clean up. The test transfer runs on a QThread so compressing and
sending a real file never freezes the dialog.
"""

from __future__ import annotations

import socket
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QRadioButton, QStackedWidget,
    QVBoxLayout, QWidget,
)

from tsbackup import pairing
from tsbackup.config import ROLE_RECEIVER, ROLE_SENDER, config_dir

from .theme import c

PAGE_ROLE, PAGE_READY, PAGE_CODE, PAGE_FOLDER, PAGE_SCHEDULE, PAGE_PAIR, PAGE_TEST = range(7)

SCHEDULE_PRESETS = [
    ("하루에 한 번", 1440),
    ("6시간마다", 360),
    ("일주일에 한 번", 10080),
]

# pairing.run_test_transfer()'s step keys, as the page says them.
STEP_TEXT = {
    "압축": "작은 파일을 압축해요",
    "전송": "받는 컴퓨터로 보내요",
    "수신·해제 확인": "받는 컴퓨터가 받아서 풀었는지 확인해요",
}


class _TestTransferWorker(QObject):
    """Qt wrapper around pairing.run_test_transfer() - the actual logic
    lives there, Qt-free, so it can be covered by the headless selftest
    suite too. This class only runs it on a worker thread and translates
    its plain step()/return-value callback into Qt signals."""

    step = Signal(str, str)        # label, "running" | "ok" | "fail"
    finished = Signal(bool, str)   # ok, detail

    def __init__(self, cfg, code: str, confirm_token: str) -> None:
        super().__init__()
        self._cfg = cfg
        self._code = code
        self._confirm_token = confirm_token

    def run(self) -> None:
        ok, detail = pairing.run_test_transfer(
            self._cfg.sender, self._code, self._confirm_token,
            step=self.step.emit,
        )
        self.finished.emit(ok, detail)


def _primary(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setDefault(True)
    b.setStyleSheet(
        f"QPushButton {{ background: {c('primary')}; color: {c('on_primary')}; border: none; "
        "border-radius: 4px; padding: 7px 18px; font-weight: 600; } "
        "QPushButton:disabled { background: #9e9e9e; color: #eeeeee; }")
    return b


def _text_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setFlat(True)
    b.setStyleSheet(f"QPushButton {{ border: none; color: {c('primary')}; padding: 7px 8px; }}")
    return b


class SetupWizard(QDialog):
    def __init__(self, cfg, parent=None, log=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self._log = log
        self.setWindowTitle("TS Backup 설정")
        self.setMinimumSize(560, 440)

        self._listener: pairing.PairingListener | None = None
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_paired)
        self._thread: QThread | None = None
        self._worker: _TestTransferWorker | None = None
        self._pair_payload: dict = {}
        self._pair_code = ""
        self._ready_ip: str | None = None

        self.stack = QStackedWidget()
        root = QVBoxLayout(self)
        root.addWidget(self.stack)

        self.stack.addWidget(self._build_role_page())       # PAGE_ROLE
        self.stack.addWidget(self._build_ready_page())      # PAGE_READY
        self.stack.addWidget(self._build_code_page())       # PAGE_CODE
        self.stack.addWidget(self._build_folder_page())     # PAGE_FOLDER
        self.stack.addWidget(self._build_schedule_page())   # PAGE_SCHEDULE
        self.stack.addWidget(self._build_pair_page())       # PAGE_PAIR
        self.stack.addWidget(self._build_test_page())       # PAGE_TEST

    # -------------------------------------------------------- page frame

    def _page(self, title: str, hint: str = "") -> tuple[QWidget, QVBoxLayout, QHBoxLayout]:
        """Title, optional one-line hint, a body, and a button row whose
        right end is reserved for the page's one main button."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 24, 24, 16)
        lay.setSpacing(12)
        t = QLabel(title)
        t.setWordWrap(True)
        t.setStyleSheet("font-size: 20px; font-weight: 600;")
        lay.addWidget(t)
        if hint:
            h = QLabel(hint)
            h.setWordWrap(True)
            h.setStyleSheet(f"color: {c('muted')};")
            lay.addWidget(h)
        body = QVBoxLayout()
        body.setSpacing(8)
        lay.addLayout(body)
        lay.addStretch(1)
        row = QHBoxLayout()
        lay.addLayout(row)
        return w, body, row

    # --------------------------------------------------------- role page

    def _build_role_page(self) -> QWidget:
        w, body, row = self._page(
            "이 컴퓨터는 어떤 일을 하나요?",
            "두 대 이상에 각각 설치하고, 한 대는 보내고 나머지는 받게 해 주세요.")
        self.role_sender = QRadioButton("보내는 컴퓨터 - 작업 폴더를 정해진 간격마다 압축해서 보내요")
        self.role_receiver = QRadioButton("받는 컴퓨터 - 보내온 압축을 받아서 날짜별로 풀어 둬요")
        self.role_sender.setChecked(True)
        for radio, note in ((self.role_sender, "보통 평소에 작업하는 PC"),
                            (self.role_receiver, "보통 집에 두는 PC나 서버. 켜 두기만 하면 돼요")):
            body.addWidget(radio)
            n = QLabel(note)
            n.setStyleSheet(f"color: {c('faint')}; margin-left: 24px;")
            body.addWidget(n)
        row.addStretch(1)
        nxt = _primary("다음")
        nxt.clicked.connect(self._role_next)
        row.addWidget(nxt)
        return w

    def _role_next(self) -> None:
        if self.role_sender.isChecked():
            self.cfg.role = ROLE_SENDER
            self.stack.setCurrentIndex(PAGE_FOLDER)
        else:
            self.cfg.role = ROLE_RECEIVER
            self.stack.setCurrentIndex(PAGE_READY)
            self._check_ready()

    # ------------------------------------------------- receiver: ready

    def _build_ready_page(self) -> QWidget:
        w, body, row = self._page(
            "먼저 연결을 확인할게요",
            "보내는 컴퓨터는 Tailscale로 이 컴퓨터에 닿아요.")
        self.ready_status = QLabel("")
        self.ready_status.setWordWrap(True)
        body.addWidget(self.ready_status)
        fw = QLabel("코드를 처음 띄울 때 Windows가 방화벽 허용을 물어볼 수 있어요. "
                    "허용해 주세요. 보내는 컴퓨터가 이 컴퓨터에 닿으려면 필요해요.")
        fw.setWordWrap(True)
        fw.setStyleSheet(f"color: {c('muted')};")
        body.addWidget(fw)
        back = _text_button("이전")
        back.clicked.connect(lambda: self.stack.setCurrentIndex(PAGE_ROLE))
        again = _text_button("다시 확인")
        again.clicked.connect(self._check_ready)
        row.addWidget(back)
        row.addWidget(again)
        row.addStretch(1)
        self.ready_next = _primary("다음")
        self.ready_next.clicked.connect(self._ready_next)
        row.addWidget(self.ready_next)
        return w

    def _check_ready(self) -> None:
        self._ready_ip = pairing.local_tailscale_ip()
        if self._ready_ip:
            self.ready_status.setText(f"✓ Tailscale이 켜져 있어요 ({self._ready_ip})")
            self.ready_status.setStyleSheet(f"color: {c('primary')};")
        else:
            self.ready_status.setText(
                "Tailscale이 꺼져 있거나 로그인이 안 돼 있어요. 켜고 로그인한 뒤 다시 확인해 주세요.")
            self.ready_status.setStyleSheet(f"color: {c('danger')};")
        self.ready_next.setEnabled(bool(self._ready_ip))

    def _ready_next(self) -> None:
        if not self._ready_ip:
            return
        self.stack.setCurrentIndex(PAGE_CODE)
        self._start_receiver_pairing()

    # -------------------------------------------------- receiver: code

    def _build_code_page(self) -> QWidget:
        w, body, row = self._page(
            "보내는 컴퓨터에 이 코드를 넣어 주세요",
            "10분 동안, 한 번만 쓸 수 있어요.")
        code_row = QHBoxLayout()
        self.code_label = QLabel("")
        self.code_label.setStyleSheet(
            "font-family: 'Cascadia Mono','D2Coding',Consolas,monospace; "
            f"font-size: 30px; font-weight: 700; color: {c('teal')};")
        code_row.addWidget(self.code_label)
        copy_btn = _text_button("복사")
        copy_btn.clicked.connect(self._copy_receiver_code)
        code_row.addWidget(copy_btn)
        code_row.addStretch(1)
        body.addLayout(code_row)
        carried = QLabel(f"이 코드로 이 컴퓨터 이름({socket.gethostname()})과 받을 폴더가 함께 "
                         "건너가요. 보내는 컴퓨터에서 따로 적을 필요 없어요.")
        carried.setWordWrap(True)
        carried.setStyleSheet(f"color: {c('faint')};")
        body.addWidget(carried)
        self.pair_status = QLabel("")
        self.pair_status.setWordWrap(True)
        body.addWidget(self.pair_status)

        regen = _text_button("새 코드")
        regen.clicked.connect(self._regenerate_receiver_code)
        row.addWidget(regen)
        self.receiver_finish_anyway = _text_button("시험 없이 마치기")
        self.receiver_finish_anyway.setVisible(False)
        self.receiver_finish_anyway.clicked.connect(self._finish_without_confirm)
        row.addWidget(self.receiver_finish_anyway)
        row.addStretch(1)
        self.receiver_finish = _primary("마침")
        self.receiver_finish.setEnabled(False)
        self.receiver_finish.clicked.connect(self.accept)
        row.addWidget(self.receiver_finish)
        return w

    def _start_receiver_pairing(self) -> None:
        r = self.cfg.receiver
        if not r.incoming_dir:
            r.incoming_dir = str(Path.home() / "Downloads" / "TsBackupIncoming")
        if not r.unpack_dir:
            r.unpack_dir = str(Path.home() / "TsBackupUnpacked")
        Path(r.incoming_dir).mkdir(parents=True, exist_ok=True)
        Path(r.unpack_dir).mkdir(parents=True, exist_ok=True)

        known_path = config_dir() / pairing.KNOWN_SENDERS_FILENAME
        self._listener = pairing.PairingListener(self.cfg, known_path, log=self._log)
        self.code_label.setText(self._listener.start(self._ready_ip))
        self.pair_status.setText("보내는 컴퓨터를 기다리고 있어요")
        self._poll_timer.start(1000)

    def _regenerate_receiver_code(self) -> None:
        if not self._listener:
            return
        self.code_label.setText(self._listener.regenerate())
        self.pair_status.setText("보내는 컴퓨터를 기다리고 있어요")
        self.receiver_finish.setEnabled(False)
        self.receiver_finish_anyway.setVisible(False)
        if not self._poll_timer.isActive():
            self._poll_timer.start(1000)

    def _copy_receiver_code(self) -> None:
        text = self.code_label.text()
        if text:
            QApplication.clipboard().setText(text)

    def _finish_without_confirm(self) -> None:
        """Escape hatch for a sender that never completes (walked away,
        uninstalled, network dead for good) - without this the receiver
        would be stuck on a code that only offers 새 코드, with no way to
        just leave the wizard. Deliberately secondary to 마침: needs an
        explicit warning, and finishes regardless of is_confirmed()."""
        resp = QMessageBox.warning(
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
        # transfer still needs this listener alive to answer /confirm
        # afterward. Enabling 마침 (and letting the receiver close the
        # wizard, which stops the listener) before that round trip lands
        # strands the sender. Keep waiting, and say so, until is_confirmed().
        if self._listener.is_confirmed():
            self.pair_status.setText("✓ 시험 파일까지 잘 받았어요. 이제 마쳐도 돼요.")
            self.receiver_finish.setEnabled(True)
            self.receiver_finish_anyway.setVisible(False)
            self._poll_timer.stop()
        elif self._listener.is_expired():
            self.pair_status.setText("코드가 만료됐어요. 새 코드를 만들어 주세요.")
            self.receiver_finish_anyway.setVisible(True)
            self._poll_timer.stop()
        elif self._listener.is_paired():
            self.pair_status.setText("연결됐어요. 시험 파일이 오기를 기다리고 있어요.")

    # ---------------------------------------------------- sender: folder

    def _build_folder_page(self) -> QWidget:
        w, body, row = self._page(
            "어떤 폴더를 보낼까요?",
            ".env와 .git까지, 그 안의 전부를 보내요.")
        dir_row = QHBoxLayout()
        self.source_edit = QLineEdit(self.cfg.sender.source_dir)
        self.source_edit.textChanged.connect(self._folder_changed)
        browse = _text_button("찾아보기")
        browse.clicked.connect(self._browse_source)
        dir_row.addWidget(self.source_edit, 1)
        dir_row.addWidget(browse)
        body.addLayout(dir_row)
        self.folder_summary = QLabel("")
        self.folder_summary.setStyleSheet(f"color: {c('faint')};")
        body.addWidget(self.folder_summary)

        from .main_window import _FolderSummary
        self._summary = _FolderSummary()
        self._summary.done.connect(self.folder_summary.setText)

        back = _text_button("이전")
        back.clicked.connect(lambda: self.stack.setCurrentIndex(PAGE_ROLE))
        row.addWidget(back)
        row.addStretch(1)
        self.folder_next = _primary("다음")
        self.folder_next.clicked.connect(self._folder_next)
        row.addWidget(self.folder_next)
        self._folder_changed(self.source_edit.text())
        return w

    def _browse_source(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "보낼 폴더 고르기", self.source_edit.text() or "")
        if chosen:
            self.source_edit.setText(chosen)

    def _folder_changed(self, text: str) -> None:
        ok = bool(text.strip()) and Path(text.strip()).is_dir()
        self.folder_next.setEnabled(ok)
        if ok:
            self.folder_summary.setText("세는 중이에요...")
            self._summary.start(text.strip(), ".env와 .git 포함")
        else:
            self.folder_summary.setText("" if not text.strip() else "그런 폴더가 없어요.")

    def _folder_next(self) -> None:
        self.cfg.sender.source_dir = self.source_edit.text().strip()
        if not self.cfg.sender.work_dir:
            self.cfg.sender.work_dir = str(Path(tempfile.gettempdir()) / "TsBackupWork")
        self.stack.setCurrentIndex(PAGE_SCHEDULE)

    # -------------------------------------------------- sender: schedule

    def _build_schedule_page(self) -> QWidget:
        w, body, row = self._page(
            "얼마나 자주 보낼까요?",
            "앱이 켜지면 한 번 보내고, 그 뒤로 이 간격마다 보내요.")
        self.schedule_group = QButtonGroup(self)
        for i, (label, minutes) in enumerate(SCHEDULE_PRESETS):
            radio = QRadioButton(label)
            radio.setChecked(i == 0)
            self.schedule_group.addButton(radio, minutes)
            body.addWidget(radio)
        back = _text_button("이전")
        back.clicked.connect(lambda: self.stack.setCurrentIndex(PAGE_FOLDER))
        row.addWidget(back)
        row.addStretch(1)
        nxt = _primary("다음")
        nxt.clicked.connect(self._schedule_next)
        row.addWidget(nxt)
        return w

    def _schedule_minutes(self) -> int:
        return self.schedule_group.checkedId()

    def _schedule_next(self) -> None:
        self.cfg.sender.interval_minutes = self._schedule_minutes()
        self.stack.setCurrentIndex(PAGE_PAIR)
        self.code_entry.setFocus()

    # ------------------------------------------------------ sender: code

    def _build_pair_page(self) -> QWidget:
        w, body, row = self._page(
            "받는 컴퓨터에 뜬 코드를 넣어 주세요",
            "받는 컴퓨터에서 코드를 띄우면 9자리 코드가 보여요. "
            "연결되면 바로 작은 파일로 시험해 볼게요.")
        self.code_entry = QLineEdit()
        self.code_entry.setPlaceholderText("XXXXX-XXXX")
        self.code_entry.setStyleSheet(
            "font-family: 'Cascadia Mono','D2Coding',Consolas,monospace; font-size: 20px;")
        self.code_entry.returnPressed.connect(self._resolve_code)
        body.addWidget(self.code_entry)
        self.pair_result = QLabel("")
        self.pair_result.setWordWrap(True)
        body.addWidget(self.pair_result)
        back = _text_button("이전")
        back.clicked.connect(lambda: self.stack.setCurrentIndex(PAGE_SCHEDULE))
        row.addWidget(back)
        row.addStretch(1)
        self.pair_button = _primary("연결하고 시험하기")
        self.pair_button.clicked.connect(self._resolve_code)
        row.addWidget(self.pair_button)
        return w

    def _resolve_code(self) -> None:
        code = self.code_entry.text().strip()
        if not code:
            return
        device_id = self.cfg.ensure_device_id()
        try:
            payload = pairing.resolve_and_pair(
                code,
                device_name=socket.gethostname(),
                device_id=device_id,
                interval_minutes=self._schedule_minutes(),
                transport_preference="taildrop",
            )
        except pairing.PairingError as exc:
            self.pair_result.setText(f"연결하지 못했어요. {exc}")
            self.pair_result.setStyleSheet(f"color: {c('danger')};")
            return

        self._pair_payload = payload
        self._pair_code = code
        target = payload.get("device_name", "")
        s = self.cfg.sender
        s.transport = "taildrop"
        if target:
            s.taildrop_targets = [target]
        ip, _secret = pairing.unpack_code(code)
        s.host = ip
        fp = payload.get("host_key_fingerprint")
        if fp:
            s.host_key = fp
        self.pair_result.setText(f"✓ {target}에 연결됐어요")
        self.pair_result.setStyleSheet(f"color: {c('teal')};")
        self.stack.setCurrentIndex(PAGE_TEST)
        self._run_test_transfer()

    # ---------------------------------------------------------- test page

    def _build_test_page(self) -> QWidget:
        w, body, row = self._page("작은 파일 하나로 끝까지 해 볼게요")
        self.step_labels: dict[str, QLabel] = {}
        for key, text in STEP_TEXT.items():
            lbl = QLabel(f"○ {text}")
            self.step_labels[key] = lbl
            body.addWidget(lbl)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        body.addWidget(self.test_result)
        self.test_retry = _text_button("다시 해 보기")
        self.test_retry.setVisible(False)
        self.test_retry.clicked.connect(self._run_test_transfer)
        row.addWidget(self.test_retry)
        row.addStretch(1)
        self.test_finish = _primary("마침")
        self.test_finish.setEnabled(False)
        self.test_finish.clicked.connect(self.accept)
        row.addWidget(self.test_finish)
        return w

    def _run_test_transfer(self) -> None:
        for key, lbl in self.step_labels.items():
            lbl.setText(f"○ {STEP_TEXT[key]}")
        self.test_result.setText("")
        self.test_finish.setEnabled(False)
        self.test_retry.setVisible(False)

        confirm_token = self._pair_payload.get("confirm_token", "")
        thread = QThread(self)
        worker = _TestTransferWorker(self.cfg, self._pair_code, confirm_token)
        worker.moveToThread(thread)
        worker.step.connect(self._on_test_step)
        worker.finished.connect(self._on_test_finished)
        thread.started.connect(worker.run)
        self._thread, self._worker = thread, worker
        thread.start()

    def _on_test_step(self, label: str, status: str) -> None:
        mark = {"running": "…", "ok": "✓", "fail": "✕"}.get(status, "○")
        if label in self.step_labels:
            self.step_labels[label].setText(f"{mark} {STEP_TEXT.get(label, label)}")

    def _on_test_finished(self, ok: bool, detail: str) -> None:
        if self._thread:
            self._thread.quit()
            self._thread.wait()
        self._thread = None
        self._worker = None
        if ok:
            from .main_window import _every

            # True by construction: accept() turns on autostart, and the
            # engine's start() sends once immediately, then every interval.
            self.test_result.setText(
                "준비됐어요. 마침을 누르면 첫 백업을 바로 시작하고, 그 뒤로는 "
                f"{_every(self.cfg.sender.interval_minutes)} 알아서 보내요. 창을 닫아도 "
                "트레이에서 계속 돌고, 문제가 생길 때만 알려 드릴게요.")
            self.test_result.setStyleSheet("")
            self.test_finish.setEnabled(True)
        else:
            self.test_result.setText(
                f"끝까지 가지 못했어요. {detail}\n받는 컴퓨터가 켜져 있는지 확인하고 다시 해 주세요.")
            self.test_result.setStyleSheet(f"color: {c('danger')};")
            self.test_retry.setVisible(True)

    # ------------------------------------------------------------- close

    def accept(self) -> None:
        self._cleanup()
        self.cfg.onboarded = True
        # Finishing the wizard means "ready to run". Without this the
        # engine's loop never started on its own (autostart_engine defaults
        # to off and only the settings dialog turned it on), so a fresh
        # install never ran a scheduled backup until someone pressed 켜기.
        self.cfg.autostart_engine = True
        self.cfg.save()
        super().accept()

    def reject(self) -> None:
        # No cancel button - the wizard is mandatory on first run - but the
        # window can still be closed (Alt+F4 / the X button), which must not
        # leak a bound listener socket.
        self._cleanup()
        super().reject()

    def _cleanup(self) -> None:
        self._poll_timer.stop()
        if self._listener:
            self._listener.stop()
            self._listener = None
        if self._thread:
            self._thread.quit()
            self._thread.wait()
