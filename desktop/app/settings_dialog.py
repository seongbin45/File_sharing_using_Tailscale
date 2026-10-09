"""고급 설정 - 압축 수준 · 전송 방식 · 연결 정보.

나루 디자인 시스템 §09 설정 folds all of this into one row ("기본값이면 열지
않아도 돼요"), because every field here has a default that is normally
right, and the pairing code fills the connection details in. This is the
only screen where the technical words (taildrop, sftp, LZMA2, 지문) appear
(principle ⑤).

apply_to() writes back into the config; the settings window saves.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFormLayout, QLineEdit, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget,
)

from tsbackup.config import ROLE_SENDER, TRANSPORTS

from .icons import window_icon
from .widgets import Card, ToggleSwitch, button, hbox, label

LEVELS = [(1, "빠르게 (1)"), (5, "보통 (5, 기본)"), (9, "작게 (9)")]


class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("고급 설정")
        self.setWindowIcon(window_icon())
        self.resize(640, 620)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 20)
        outer.setSpacing(12)
        outer.addWidget(label("고급 설정", "title"))
        outer.addWidget(label("기본값이면 열지 않아도 돼요. 짝 코드가 연결 정보를 채워 줘요.", "body2", wrap=True))

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 8, 0, 8)
        lay.setSpacing(16)
        self.sender_box = self._build_sender_box()
        self.receiver_box = self._build_receiver_box()
        lay.addWidget(self.sender_box)
        lay.addWidget(self.receiver_box)
        lay.addStretch(1)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.NoFrame)
        area.setWidget(page)
        outer.addWidget(area, 1)

        self.role = cfg.role
        cancel = button("취소", "quiet")
        cancel.clicked.connect(self.reject)
        save = button("저장하기", "primary")
        save.clicked.connect(self.accept)
        outer.addLayout(hbox(None, cancel, save))
        self._sync_enabled()

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _form(card: Card) -> QFormLayout:
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        card.body.addLayout(form)
        return form

    def _dir_row(self, value: str) -> dict:
        edit = QLineEdit(value)
        pick = button("바꾸기")
        pick.clicked.connect(lambda: self._pick_dir(edit))
        w = QWidget()
        w.setLayout(hbox(edit, pick, spacing=8))
        return {"w": w, "edit": edit}

    def _pick_dir(self, edit: QLineEdit) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "폴더 고르기", edit.text() or "")
        if chosen:
            edit.setText(chosen)

    @staticmethod
    def _spin(lo: int, hi: int, value: int) -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(value)
        s.setMinimumHeight(40)
        return s

    # ------------------------------------------------------------- sender

    def _build_sender_box(self) -> Card:
        s = self.cfg.sender
        box = Card(padding=16, spacing=10)
        box.body.addWidget(label("보내는 컴퓨터", "section"))
        form = self._form(box)

        self.level = QComboBox()
        for v, text in LEVELS:
            self.level.addItem(text, v)
        idx = next((i for i, (v, _t) in enumerate(LEVELS) if v == s.level), None)
        if idx is None:
            self.level.addItem(f"직접 정한 값 ({s.level})", s.level)
            idx = self.level.count() - 1
        self.level.setCurrentIndex(idx)
        form.addRow("압축 수준 (LZMA2)", self.level)

        self.work_dir = self._dir_row(s.work_dir)
        form.addRow("압축을 만들 폴더", self.work_dir["w"])
        self.source_dir = self._dir_row(s.source_dir)
        form.addRow("보내는 폴더", self.source_dir["w"])
        self.keep_local = self._spin(0, 20, s.keep_local)
        form.addRow("남겨 둘 압축", self.keep_local)

        self.transport = QComboBox()
        for t in TRANSPORTS:
            self.transport.addItem(t, t)
        self.transport.setCurrentText(s.transport)
        self.transport.currentTextChanged.connect(self._sync_transport)
        form.addRow("전송 방식", self.transport)
        self.fallbacks = QLineEdit(" ".join(s.fallback_transports))
        self.fallbacks.setPlaceholderText("예: sftp http (공백으로 구분, 위에서부터 차례로)")
        form.addRow("예비 전송", self.fallbacks)

        self.taildrop_targets = QLineEdit(" ".join(s.taildrop_targets))
        self.taildrop_targets.setPlaceholderText("기기 이름, 공백으로 구분 (위에서부터 차례로)")
        form.addRow("taildrop 받는 기기", self.taildrop_targets)
        self.host = QLineEdit(s.host)
        form.addRow("주소 (sftp/http)", self.host)
        self.port = self._spin(0, 65535, s.port)
        form.addRow("포트 (0 = 기본)", self.port)
        self.username = QLineEdit(s.username)
        form.addRow("사용자 (sftp)", self.username)
        self.remote_dir = QLineEdit(s.remote_dir)
        form.addRow("원격 폴더 (sftp)", self.remote_dir)
        self.http_token = QLineEdit(s.http_token)
        form.addRow("토큰 (http)", self.http_token)
        self.host_key = QLineEdit(s.host_key)
        self.host_key.setPlaceholderText("비어 있으면 첫 연결 때 등록해요")
        form.addRow("호스트 키 지문 (sftp)", self.host_key)
        return box

    # ----------------------------------------------------------- receiver

    def _build_receiver_box(self) -> Card:
        r = self.cfg.receiver
        box = Card(padding=16, spacing=10)
        box.body.addWidget(label("받는 컴퓨터", "section"))
        form = self._form(box)
        self.incoming_dir = self._dir_row(r.incoming_dir)
        form.addRow("받는 폴더", self.incoming_dir["w"])
        self.unpack_dir = self._dir_row(r.unpack_dir)
        form.addRow("풀어 둘 폴더", self.unpack_dir["w"])
        self.delete_after = ToggleSwitch()
        self.delete_after.setChecked(r.delete_after_unpack)
        form.addRow("풀고 나서 압축 지우기", self.delete_after)
        self.expects_http = ToggleSwitch()
        self.expects_http.setChecked(r.expects_http)
        form.addRow("HTTP로 받기", self.expects_http)
        self.http_bind = QLineEdit(r.http_bind)
        form.addRow("HTTP 바인딩", self.http_bind)
        self.http_port = self._spin(1, 65535, r.http_port)
        form.addRow("HTTP 포트", self.http_port)
        self.recv_token = QLineEdit(r.http_token)
        form.addRow("HTTP 토큰", self.recv_token)
        return box

    def _sync_enabled(self) -> None:
        is_sender = self.role == ROLE_SENDER
        self.sender_box.setVisible(is_sender)
        self.receiver_box.setVisible(not is_sender)
        self._sync_transport()

    def _sync_transport(self, *_) -> None:
        t = self.transport.currentText()
        self.taildrop_targets.setEnabled(t == "taildrop")
        self.host.setEnabled(t in ("sftp", "http"))
        self.username.setEnabled(t == "sftp")
        self.remote_dir.setEnabled(t == "sftp")
        self.http_token.setEnabled(t == "http")
        self.host_key.setEnabled(t == "sftp")

    # -------------------------------------------------------------- apply

    def apply_to(self, cfg) -> None:
        s = cfg.sender
        s.source_dir = self.source_dir["edit"].text().strip()
        s.work_dir = self.work_dir["edit"].text().strip()
        s.level = int(self.level.currentData())
        s.keep_local = self.keep_local.value()
        s.transport = self.transport.currentText()
        s.fallback_transports = [t for t in self.fallbacks.text().split() if t in TRANSPORTS]
        s.taildrop_targets = self.taildrop_targets.text().split()
        s.host = self.host.text().strip()
        s.port = self.port.value()
        s.username = self.username.text().strip()
        s.remote_dir = self.remote_dir.text().strip()
        s.http_token = self.http_token.text().strip()
        s.host_key = self.host_key.text().strip()

        r = cfg.receiver
        r.incoming_dir = self.incoming_dir["edit"].text().strip()
        r.unpack_dir = self.unpack_dir["edit"].text().strip()
        r.delete_after_unpack = self.delete_after.isChecked()
        r.http_bind = self.http_bind.text().strip() or "127.0.0.1"
        r.http_port = self.http_port.value()
        r.http_token = self.recv_token.text().strip()
        r.expects_http = self.expects_http.isChecked()

