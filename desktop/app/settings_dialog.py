"""Settings popup.

One dialog serves both roles; the sender and receiver fields live in separate
group boxes and only the relevant one is enabled, so the person setting up a
receiver is not asked for a compression level. The transport row shows only the
fields that transport needs - Taildrop wants device names, sftp/http want a
host - because a form that asks for everything at once is how the wrong field
gets filled in.

Each box also splits into an always-visible basic section and a collapsed
"고급" section behind a disclosure toggle - compression level, the http/sftp
token, and the pinned host-key fingerprint are all fields whose default is
normally the right answer (see _make_advanced_toggle), so they stay out of
the way until someone has a reason to open them.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout,
    QWidget,
)

from tsbackup.config import ROLE_RECEIVER, ROLE_SENDER, TRANSPORTS


class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("설정")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)

        # ---- role -----------------------------------------------------
        role_row = QHBoxLayout()
        role_row.addWidget(QLabel("역할"))
        self.role = QComboBox()
        self.role.addItem("보내는 쪽 (압축·전송)", ROLE_SENDER)
        self.role.addItem("받는 쪽 (수신·압축 해제)", ROLE_RECEIVER)
        self.role.setCurrentIndex(0 if cfg.role == ROLE_SENDER else 1)
        self.role.currentIndexChanged.connect(self._sync_enabled)
        role_row.addWidget(self.role, 1)
        root.addLayout(role_row)

        root.addWidget(self._build_sender_box())
        root.addWidget(self._build_receiver_box())

        self.minimize = QCheckBox("창을 닫으면 트레이로 최소화")
        self.minimize.setChecked(cfg.minimize_to_tray)
        self.autostart = QCheckBox("앱을 열면 자동으로 시작")
        self.autostart.setChecked(cfg.autostart_engine)
        root.addWidget(self.minimize)
        root.addWidget(self.autostart)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._sync_enabled()

    # ------------------------------------------------------------- sender

    def _build_sender_box(self) -> QGroupBox:
        s = self.cfg.sender
        box = QGroupBox("보내는 쪽")
        outer = QVBoxLayout(box)

        basic = QWidget()
        form = QFormLayout(basic)
        form.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(basic)

        self.source_dir = self._dir_row(s.source_dir)
        form.addRow("대상 폴더", self.source_dir["w"])
        self.work_dir = self._dir_row(s.work_dir)
        form.addRow("작업 폴더", self.work_dir["w"])

        self.keep_local = QSpinBox()
        self.keep_local.setRange(0, 20)
        self.keep_local.setValue(s.keep_local)
        form.addRow("로컬 보관 개수", self.keep_local)

        self.interval_hours = QSpinBox()
        self.interval_hours.setRange(0, 168)
        self.interval_hours.setValue(s.interval_minutes // 60)
        self.interval_mins = QSpinBox()
        self.interval_mins.setRange(0, 59)
        self.interval_mins.setValue(s.interval_minutes % 60)
        iv = QHBoxLayout()
        iv.addWidget(self.interval_hours)
        iv.addWidget(QLabel("시간"))
        iv.addWidget(self.interval_mins)
        iv.addWidget(QLabel("분마다"))
        iv.addStretch(1)
        iv_w = QWidget()
        iv_w.setLayout(iv)
        form.addRow("트리거 간격", iv_w)

        self.transport = QComboBox()
        for t in TRANSPORTS:
            self.transport.addItem(t, t)
        self.transport.setCurrentText(s.transport)
        self.transport.currentTextChanged.connect(self._sync_transport)
        form.addRow("전송 방식", self.transport)

        self.taildrop_targets = QLineEdit(" ".join(s.taildrop_targets))
        self.taildrop_targets.setPlaceholderText("기기 이름을 공백으로 구분 (fallback 순서)")
        form.addRow("Taildrop 대상", self.taildrop_targets)

        self.host = QLineEdit(s.host)
        form.addRow("host (sftp/http)", self.host)
        self.port = QSpinBox()
        self.port.setRange(0, 65535)
        self.port.setValue(s.port)
        form.addRow("port (0=기본)", self.port)
        self.username = QLineEdit(s.username)
        form.addRow("사용자 (sftp)", self.username)
        self.remote_dir = QLineEdit(s.remote_dir)
        form.addRow("원격 폴더 (sftp)", self.remote_dir)

        # ---- advanced: level, tokens, pinned host key - defaults are the
        # right answer until there's a specific reason to change one.
        adv = QWidget()
        adv_form = QFormLayout(adv)
        adv_form.setContentsMargins(0, 0, 0, 0)

        self.level = QSpinBox()
        self.level.setRange(0, 9)
        self.level.setValue(s.level)
        adv_form.addRow("압축 수준 (LZMA2 0~9)", self.level)

        self.http_token = QLineEdit(s.http_token)
        adv_form.addRow("토큰 (http)", self.http_token)

        self.host_key = QLineEdit(s.host_key)
        self.host_key.setPlaceholderText("비어 있으면 첫 연결 때 자동으로 등록됩니다 (TOFU)")
        adv_form.addRow("호스트 키 지문 (sftp)", self.host_key)

        outer.addWidget(self._make_advanced_toggle(adv, "고급 — 압축 수준, 토큰, 호스트 키"))
        outer.addWidget(adv)

        self.sender_box = box
        return box

    # ----------------------------------------------------------- receiver

    def _build_receiver_box(self) -> QGroupBox:
        r = self.cfg.receiver
        box = QGroupBox("받는 쪽")
        outer = QVBoxLayout(box)

        basic = QWidget()
        form = QFormLayout(basic)
        form.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(basic)

        self.incoming_dir = self._dir_row(r.incoming_dir)
        form.addRow("수신 폴더", self.incoming_dir["w"])
        self.unpack_dir = self._dir_row(r.unpack_dir)
        form.addRow("압축 해제 폴더", self.unpack_dir["w"])

        self.delete_after = QCheckBox("압축 해제 후 원본 삭제")
        self.delete_after.setChecked(r.delete_after_unpack)
        form.addRow("", self.delete_after)

        self.expects_http = QCheckBox("보내는 쪽이 HTTP로 보냅니다 (HTTP 수신 서버를 켭니다)")
        self.expects_http.setChecked(r.expects_http)
        form.addRow("", self.expects_http)

        # ---- advanced: HTTP listener detail - only relevant once
        # expects_http is on, and the defaults are fine until then.
        adv = QWidget()
        adv_form = QFormLayout(adv)
        adv_form.setContentsMargins(0, 0, 0, 0)

        self.http_bind = QLineEdit(r.http_bind)
        adv_form.addRow("HTTP 바인딩", self.http_bind)
        self.http_port = QSpinBox()
        self.http_port.setRange(1, 65535)
        self.http_port.setValue(r.http_port)
        adv_form.addRow("HTTP 포트", self.http_port)
        self.recv_token = QLineEdit(r.http_token)
        adv_form.addRow("HTTP 토큰", self.recv_token)

        outer.addWidget(self._make_advanced_toggle(adv, "고급 — HTTP 바인딩·포트·토큰"))
        outer.addWidget(adv)

        self.receiver_box = box
        return box

    # --------------------------------------------------------------- util

    def _make_advanced_toggle(self, advanced: QWidget, label: str) -> QPushButton:
        """A disclosure button that shows/hides `advanced`, collapsed by
        default. A flat QPushButton rather than QToolButton so the same
        style works without extra icon assets (icons.py's rationale for
        drawing icons in code applies here too - one fewer asset to bundle)."""
        btn = QPushButton(f"▶ {label}")
        btn.setCheckable(True)
        btn.setFlat(True)
        btn.setStyleSheet("text-align: left; border: none; color: #0067c0;")
        advanced.setVisible(False)

        def _toggle(checked: bool) -> None:
            advanced.setVisible(checked)
            btn.setText(f"{'▼' if checked else '▶'} {label}")

        btn.toggled.connect(_toggle)
        return btn

    def _dir_row(self, value: str) -> dict:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit(value)
        btn = QPushButton("...")
        btn.setFixedWidth(32)
        btn.clicked.connect(lambda: self._pick_dir(edit))
        lay.addWidget(edit, 1)
        lay.addWidget(btn)
        return {"w": w, "edit": edit}

    def _pick_dir(self, edit: QLineEdit) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "폴더 선택", edit.text() or "")
        if chosen:
            edit.setText(chosen)

    def _sync_enabled(self) -> None:
        is_sender = self.role.currentData() == ROLE_SENDER
        self.sender_box.setEnabled(is_sender)
        self.receiver_box.setEnabled(not is_sender)
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
        """Write the fields back into the config object."""
        cfg.role = self.role.currentData()
        cfg.minimize_to_tray = self.minimize.isChecked()
        cfg.autostart_engine = self.autostart.isChecked()

        s = cfg.sender
        s.source_dir = self.source_dir["edit"].text().strip()
        s.work_dir = self.work_dir["edit"].text().strip()
        s.level = self.level.value()
        s.keep_local = self.keep_local.value()
        s.interval_minutes = max(1, self.interval_hours.value() * 60 + self.interval_mins.value())
        s.transport = self.transport.currentText()
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
