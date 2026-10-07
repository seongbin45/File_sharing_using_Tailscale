"""First-run wizard: role fork -> pairing -> mandatory test-transfer.

Launched by main.py before MainWindow whenever cfg.onboarded is False.
Accepting means the config is ready to run - main.py writes
onboarded = True and proceeds straight to the normal window. There is no
cancel button by design (the mockup treats this as mandatory), but closing
the window still has to release the pairing listener's socket, so both
accept() and reject() clean it up.

The test-transfer step runs on a QThread (mirroring engine.py's
_SenderWorker) so compressing/sending a real file - however small - never
freezes the dialog.
"""

from __future__ import annotations

import socket
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QRadioButton, QStackedWidget,
    QVBoxLayout, QWidget,
)

from tsbackup import pairing
from tsbackup.config import ROLE_RECEIVER, ROLE_SENDER, config_dir

SCHEDULE_PRESETS = [
    ("6시간마다", 360),
    ("하루 한 번", 1440),
    ("일주일에 한 번", 10080),
]


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

        self.stack = QStackedWidget()
        root = QVBoxLayout(self)
        root.addWidget(self.stack)

        self.stack.addWidget(self._build_role_page())      # 0
        self.stack.addWidget(self._build_receiver_page())  # 1
        self.stack.addWidget(self._build_sender_page())    # 2
        self.stack.addWidget(self._build_test_page())      # 3

    # ---------------------------------------------------------- role page

    def _build_role_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        title = QLabel("이 컴퓨터는 어느 쪽입니까?")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        lay.addWidget(title)
        lay.addWidget(QLabel(
            "두 대 이상에 각각 설치하고, 한 대는 보내는 쪽, 한 대 이상은 받는 쪽으로 두십시오."))

        self.role_sender = QRadioButton(
            "보내는 쪽 - 내 작업 폴더를 정해진 간격마다 압축해 다른 컴퓨터로 보냅니다")
        self.role_receiver = QRadioButton(
            "받는 쪽 - 보내온 압축을 받아 날짜별로 풀어 보관합니다")
        self.role_sender.setChecked(True)
        lay.addWidget(self.role_sender)
        lay.addWidget(self.role_receiver)
        lay.addStretch(1)

        next_btn = QPushButton("다음")
        next_btn.clicked.connect(self._role_next)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(next_btn)
        lay.addLayout(row)
        return w

    def _role_next(self) -> None:
        if self.role_sender.isChecked():
            self.cfg.role = ROLE_SENDER
            self.stack.setCurrentIndex(2)
        else:
            self.cfg.role = ROLE_RECEIVER
            self._start_receiver_pairing()
            self.stack.setCurrentIndex(1)

    # ------------------------------------------------------ receiver page

    def _build_receiver_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("보내는 쪽에 이 코드를 입력하십시오"))
        code_row = QHBoxLayout()
        self.code_label = QLabel("")
        self.code_label.setStyleSheet(
            "font-family: 'Cascadia Mono','D2Coding',Consolas,monospace; "
            "font-size: 26px; font-weight: 700;")
        code_row.addWidget(self.code_label)
        copy_btn = QPushButton("복사")
        copy_btn.clicked.connect(self._copy_receiver_code)
        code_row.addWidget(copy_btn)
        code_row.addStretch(1)
        lay.addLayout(code_row)
        lay.addWidget(QLabel("tailnet 안에서만 유효 · 15분 후 만료 · 한 번 쓰면 소멸"))

        self.pair_status = QLabel("대기 중...")
        lay.addWidget(self.pair_status)

        regen = QPushButton("새 코드")
        regen.clicked.connect(self._regenerate_receiver_code)
        lay.addWidget(regen)
        lay.addStretch(1)

        self.receiver_finish_anyway = QPushButton("시험 없이 마침")
        self.receiver_finish_anyway.setVisible(False)
        self.receiver_finish_anyway.clicked.connect(self._finish_without_confirm)
        self.receiver_finish = QPushButton("마침")
        self.receiver_finish.setEnabled(False)
        self.receiver_finish.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.receiver_finish_anyway)
        row.addWidget(self.receiver_finish)
        lay.addLayout(row)
        return w

    def _start_receiver_pairing(self) -> None:
        ip = pairing.local_tailscale_ip()
        if not ip:
            QMessageBox.warning(
                self, "Tailscale 필요",
                "Tailscale 이 실행 중인지, 이 기기가 로그인되어 있는지 확인하십시오.")
            return

        r = self.cfg.receiver
        if not r.incoming_dir:
            r.incoming_dir = str(Path.home() / "Downloads" / "TsBackupIncoming")
        if not r.unpack_dir:
            r.unpack_dir = str(Path.home() / "TsBackupUnpacked")
        Path(r.incoming_dir).mkdir(parents=True, exist_ok=True)
        Path(r.unpack_dir).mkdir(parents=True, exist_ok=True)

        known_path = config_dir() / pairing.KNOWN_SENDERS_FILENAME
        self._listener = pairing.PairingListener(self.cfg, known_path, log=self._log)
        code = self._listener.start(ip)
        self.code_label.setText(code)
        self.pair_status.setText("대기 중...")
        self._poll_timer.start(1000)

    def _regenerate_receiver_code(self) -> None:
        if not self._listener:
            return
        self.code_label.setText(self._listener.regenerate())
        self.pair_status.setText("대기 중...")
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
        would be stuck on a code that only offers "새 코드", with no way to
        just leave the wizard. Deliberately secondary to "마침": needs an
        explicit warning, and finishes regardless of is_confirmed()."""
        resp = QMessageBox.warning(
            self, "확인 없이 마치기",
            "보내는 쪽의 시험 전송이 확인되지 않았습니다. 그래도 끝내시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp == QMessageBox.StandardButton.Yes:
            self.accept()

    def _poll_paired(self) -> None:
        if not self._listener:
            return
        # is_paired() alone is not "done": the sender's mandatory test-
        # transfer still needs this listener alive to answer /confirm
        # afterward. Enabling "마침" (and letting the receiver close the
        # wizard, which stops the listener) before that round trip lands
        # strands the sender - the exact failure that forced starting
        # over with a brand new code. Keep waiting, and say so, until
        # is_confirmed() is true.
        if self._listener.is_confirmed():
            self.pair_status.setText("시험 전송까지 확인되었습니다. 이제 닫아도 됩니다.")
            self.receiver_finish.setEnabled(True)
            self.receiver_finish_anyway.setVisible(False)
            self._poll_timer.stop()
        elif self._listener.is_expired():
            self.pair_status.setText("코드가 만료되었습니다 - 새 코드를 만드십시오")
            self.receiver_finish_anyway.setVisible(True)
            self._poll_timer.stop()
        elif self._listener.is_paired():
            self.pair_status.setText("연결됨 - 보내는 쪽의 시험 전송을 기다리는 중...")

    # -------------------------------------------------------- sender page

    def _build_sender_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("받는 쪽 코드"))
        code_row = QHBoxLayout()
        self.code_entry = QLineEdit()
        self.code_entry.setPlaceholderText("XXXXX-XXXX")
        confirm_btn = QPushButton("확인")
        confirm_btn.clicked.connect(self._resolve_code)
        code_row.addWidget(self.code_entry, 1)
        code_row.addWidget(confirm_btn)
        lay.addLayout(code_row)
        self.pair_result = QLabel("")
        lay.addWidget(self.pair_result)

        lay.addWidget(QLabel("보낼 폴더"))
        dir_row = QHBoxLayout()
        self.source_edit = QLineEdit(self.cfg.sender.source_dir)
        browse = QPushButton("찾아보기")
        browse.clicked.connect(self._browse_source)
        dir_row.addWidget(self.source_edit, 1)
        dir_row.addWidget(browse)
        lay.addLayout(dir_row)

        lay.addWidget(QLabel("얼마나 자주"))
        self.schedule_combo = QComboBox()
        for label, minutes in SCHEDULE_PRESETS:
            self.schedule_combo.addItem(label, minutes)
        self.schedule_combo.setCurrentIndex(1)  # 하루 한 번
        lay.addWidget(self.schedule_combo)

        lay.addStretch(1)
        self.sender_next = QPushButton("시험 전송하고 끝내기")
        self.sender_next.setEnabled(False)
        self.sender_next.clicked.connect(self._start_test_transfer)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.sender_next)
        lay.addLayout(row)
        return w

    def _browse_source(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "폴더 선택", self.source_edit.text() or "")
        if chosen:
            self.source_edit.setText(chosen)

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
                interval_minutes=self.schedule_combo.currentData(),
                transport_preference="taildrop",
            )
        except pairing.PairingError as exc:
            self.pair_result.setText(f"실패: {exc}")
            self.sender_next.setEnabled(False)
            return

        self._pair_payload = payload
        self._pair_code = code
        target = payload.get("device_name", "")
        self.pair_result.setText(
            f"연결됨: {target} · {payload.get('incoming_dir', '')}")

        s = self.cfg.sender
        s.transport = "taildrop"
        if target:
            s.taildrop_targets = [target]
        ip, _secret = pairing.unpack_code(code)
        s.host = ip
        fp = payload.get("host_key_fingerprint")
        if fp:
            s.host_key = fp
        token = payload.get("issued_token")
        if token:
            s.http_token = token
        self.sender_next.setEnabled(True)

    def _start_test_transfer(self) -> None:
        source = self.source_edit.text().strip()
        if not source or not Path(source).is_dir():
            QMessageBox.warning(self, "폴더 필요", "보낼 폴더를 먼저 선택하십시오.")
            return
        self.cfg.sender.source_dir = source
        if not self.cfg.sender.work_dir:
            self.cfg.sender.work_dir = str(Path(tempfile.gettempdir()) / "TsBackupWork")
        self.cfg.sender.interval_minutes = self.schedule_combo.currentData()

        self.stack.setCurrentIndex(3)
        self._run_test_transfer()

    # ---------------------------------------------------------- test page

    def _build_test_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("작은 파일 하나로 전 과정을 확인합니다"))
        self.step_labels: dict[str, QLabel] = {}
        for name in ("압축", "전송", "수신·해제 확인"):
            lbl = QLabel(f"○ {name}")
            self.step_labels[name] = lbl
            lay.addWidget(lbl)
        self.test_result = QLabel("")
        lay.addWidget(self.test_result)
        lay.addStretch(1)

        self.test_retry = QPushButton("다시 시도")
        self.test_retry.setVisible(False)
        self.test_retry.clicked.connect(self._run_test_transfer)
        self.test_finish = QPushButton("마침")
        self.test_finish.setEnabled(False)
        self.test_finish.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addWidget(self.test_retry)
        row.addStretch(1)
        row.addWidget(self.test_finish)
        lay.addLayout(row)
        return w

    def _run_test_transfer(self) -> None:
        for name, lbl in self.step_labels.items():
            lbl.setText(f"○ {name}")
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
        self.step_labels[label].setText(f"{mark} {label}")

    def _on_test_finished(self, ok: bool, detail: str) -> None:
        if self._thread:
            self._thread.quit()
            self._thread.wait()
        self._thread = None
        self._worker = None
        if ok:
            self.test_result.setText("준비됐습니다. 이제 닫아 두십시오.")
            self.test_finish.setEnabled(True)
        else:
            self.test_result.setText(f"실패: {detail}")
            self.test_retry.setVisible(True)

    # ------------------------------------------------------------- close

    def accept(self) -> None:
        self._cleanup()
        self.cfg.onboarded = True
        self.cfg.save()
        super().accept()

    def reject(self) -> None:
        # No cancel button in the mockup - the wizard is mandatory on first
        # run - but the window can still be closed (Alt+F4 / the X button),
        # which must not leak a bound listener socket.
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
