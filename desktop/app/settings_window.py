"""나루 설정 - 나루 디자인 시스템 §09 설정 ("목록형, 고급은 한 줄로 접기").

Left: 백업 / 받는 컴퓨터 / 알림 / 나루 정보 (a receiver sees 받기 / 보내는
컴퓨터 / 알림 / 나루 정보). Right: one card of rows - a name, its current
value, › - and the technical settings folded into a single 고급 설정 row.
"바꾸면 바로 저장돼요": every change is saved the moment it is made; there is
no 저장 button to forget.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLineEdit, QListWidget, QMessageBox,
    QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from tsbackup import pairing
from tsbackup.config import ROLE_RECEIVER, ROLE_SENDER, config_dir

from . import startup, wording
from .icons import window_icon
from .widgets import Card, ElidedLabel, LogoMark, OptionRow, ToggleSwitch, button, hbox, label, page_widget


class SettingRow(QPushButton):
    """A row: name on the left, the current value and › on the right."""

    def __init__(self, name: str, value: str = "", sub: str = "") -> None:
        super().__init__()
        self.setProperty("row", True)
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 10, 16, 10)
        lay.setSpacing(12)
        left = QVBoxLayout()
        left.setSpacing(2)
        left.addWidget(label(name))
        self.sub = label(sub, "caption", wrap=True)
        self.sub.setVisible(bool(sub))
        left.addWidget(self.sub)
        lay.addLayout(left, 1)
        self.value = ElidedLabel(value, "body2")
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.value)
        lay.addWidget(label("›", "caption"))
        self.setMinimumHeight(56 if sub else 52)

    def set_value(self, text: str) -> None:
        self.value.setText(text)


class ToggleRow(QWidget):
    def __init__(self, name: str, on: bool, sub: str = "") -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 8, 16, 8)
        left = QVBoxLayout()
        left.setSpacing(2)
        left.addWidget(label(name))
        if sub:
            left.addWidget(label(sub, "caption", wrap=True))
        lay.addLayout(left, 1)
        self.switch = ToggleSwitch()
        self.switch.setChecked(on)
        lay.addWidget(self.switch)
        self.setMinimumHeight(52)


def rows_card(*rows: QWidget) -> Card:
    card = Card(padding=0, spacing=0)
    card.body.setContentsMargins(0, 6, 0, 6)
    for r in rows:
        card.body.addWidget(r)
    return card


class _ChoiceDialog(QDialog):
    """A small dialog of option rows (+ optional extra widget) and 저장하기."""

    def __init__(self, parent, title: str, sub: str, options: list[tuple[object, str, str, str]],
                 current, extra: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowIcon(window_icon())
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)
        lay.addWidget(label(title, "title"))
        if sub:
            lay.addWidget(label(sub, "body2", wrap=True))
        lay.addSpacing(8)
        self.value = current
        self.rows = {}
        for key, name, note, badge in options:
            r = OptionRow(name, note, badge)
            r.clicked.connect(lambda k=key: self._pick(k))
            self.rows[key] = r
            lay.addWidget(r)
        if extra is not None:
            lay.addWidget(extra)
        self._pick(current if current in self.rows else next(iter(self.rows)))
        cancel = button("취소", "quiet")
        cancel.clicked.connect(self.reject)
        ok = button("저장하기", "primary")
        ok.clicked.connect(self.accept)
        lay.addSpacing(8)
        lay.addLayout(hbox(None, cancel, ok))

    def _pick(self, key) -> None:
        self.value = key
        for k, r in self.rows.items():
            r.setSelected(k == key)


class SettingsWindow(QDialog):
    def __init__(self, cfg, log, engine, parent=None, *, on_role_changed=None, on_changed=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.log = log
        self.engine = engine
        self._on_role_changed = on_role_changed or (lambda: None)
        self._on_changed = on_changed or (lambda: None)
        self.setWindowTitle("나루 설정")
        self.setWindowIcon(window_icon())
        self.setMinimumSize(720, 560)
        self.resize(760, 600)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 16, 0, 0)
        outer.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setProperty("nav", True)
        self.nav.setFixedWidth(200)
        self.nav.setFocusPolicy(Qt.NoFocus)
        outer.addWidget(self.nav)

        right = QVBoxLayout()
        right.setContentsMargins(24, 8, 24, 20)
        self.pages = QStackedWidget()
        right.addWidget(self.pages, 1)
        right.addWidget(label("바꾸면 바로 저장돼요", "caption"))
        outer.addLayout(right, 1)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self._build()

    # ------------------------------------------------------------- frame

    def _build(self) -> None:
        self.nav.clear()
        while self.pages.count():
            w = self.pages.widget(0)
            self.pages.removeWidget(w)
            w.deleteLater()
        if self.cfg.role == ROLE_SENDER:
            pages = [("백업", self._sender_backup), ("받는 컴퓨터", self._sender_targets),
                     ("알림", self._alerts), ("나루 정보", self._about)]
        else:
            pages = [("받기", self._receiver_backup), ("보내는 컴퓨터", self._receiver_senders),
                     ("알림", self._alerts), ("나루 정보", self._about)]
        for name, build in pages:
            self.nav.addItem(name)
            self.pages.addWidget(self._page(name, build()))
        self.nav.setCurrentRow(0)

    def _page(self, title: str, content: QWidget) -> QWidget:
        page = page_widget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        lay.addWidget(label(title, "title"))
        lay.addWidget(content)
        lay.addStretch(1)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.NoFrame)
        area.setWidget(page)
        return area

    def _save(self) -> None:
        self.cfg.save()
        self.engine.reschedule()
        self._on_changed()

    # --------------------------------------------------------- sender pages

    def _sender_backup(self) -> QWidget:
        s = self.cfg.sender
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self.row_folder = SettingRow("보내는 폴더", s.source_dir or "정하지 않았어요")
        self.row_folder.clicked.connect(self._pick_source)
        self.row_schedule = SettingRow("얼마나 자주", wording.schedule_full(s.interval_minutes, s.at_time))
        self.row_schedule.clicked.connect(self._pick_schedule)
        self.row_keep = SettingRow("여기 남겨 둘 압축", self._keep_text())
        self.row_keep.clicked.connect(self._pick_keep)
        lay.addWidget(rows_card(self.row_folder, self.row_schedule, self.row_keep, self._startup_row()))
        lay.addWidget(rows_card(self._advanced_row()))
        return w

    def _keep_text(self) -> str:
        k = self.cfg.sender.keep_local
        return f"최근 {k}개" if k > 0 else "남기지 않아요"

    def _pick_source(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "보낼 폴더 고르기", self.cfg.sender.source_dir or "")
        if chosen:
            self.cfg.sender.source_dir = str(Path(chosen))
            self.row_folder.set_value(self.cfg.sender.source_dir)
            self._save()

    def _pick_schedule(self) -> None:
        s = self.cfg.sender
        time_edit = QLineEdit(s.at_time)
        time_edit.setInputMask("99:99")
        time_edit.setFixedWidth(120)
        extra = QWidget()
        extra.setLayout(hbox(label("보내는 시각"), None, time_edit, margins=(4, 8, 4, 0)))
        options = [(m, wording.schedule_name(m), wording.schedule_note(m, s.at_time),
                    "추천" if m == 1440 else "") for m in (360, 1440, 10080)]
        current = s.interval_minutes if s.interval_minutes in (360, 1440, 10080) else 1440
        dlg = _ChoiceDialog(self, "얼마나 자주 보낼까요?", "그때 컴퓨터가 꺼져 있으면, 다음에 켤 때 보내요.",
                            options, current, extra)
        if dlg.exec():
            from tsbackup.schedule import parse_at

            h, m = parse_at(time_edit.text())
            s.interval_minutes = int(dlg.value)
            s.at_time = f"{h:02d}:{m:02d}"
            self.row_schedule.set_value(wording.schedule_full(s.interval_minutes, s.at_time))
            self._save()

    def _pick_keep(self) -> None:
        options = [(1, "최근 1개", "가장 적게 써요", ""), (3, "최근 3개", "", "추천"),
                   (5, "최근 5개", "", ""), (0, "남기지 않기", "보내고 나면 바로 지워요", "")]
        dlg = _ChoiceDialog(self, "여기 남겨 둘 압축", "오래된 압축은 새로 만들기 전에 지워요. 보내지 못한 압축은 따로 남겨 둬요.",
                            options, self.cfg.sender.keep_local)
        if dlg.exec():
            self.cfg.sender.keep_local = int(dlg.value)
            self.row_keep.set_value(self._keep_text())
            self._save()

    def _sender_targets(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        s = self.cfg.sender
        rows = []
        for t in s.taildrop_targets or ([s.host] if s.host else []):
            r = QWidget()
            remove = button("빼기", "text")
            remove.clicked.connect(lambda _c=False, name=t: self._remove_target(name))
            r.setLayout(hbox(label(t), None, remove, margins=(20, 6, 12, 6)))
            r.setMinimumHeight(52)
            rows.append(r)
        if not rows:
            empty = label("아직 연결한 받는 컴퓨터가 없어요.", "body2")
            empty.setContentsMargins(20, 12, 20, 12)
            rows.append(empty)
        lay.addWidget(rows_card(*rows))
        lay.addWidget(label("위에서부터 차례로 보내 보고, 처음 닿은 곳에 보내요.", "caption", wrap=True))
        add = button("받는 컴퓨터 더하기", "primary")
        add.clicked.connect(self._add_target)
        lay.addLayout(hbox(add, None))
        return w

    def _remove_target(self, name: str) -> None:
        if QMessageBox.question(self, "받는 컴퓨터 빼기", f"{name}에는 더 보내지 않을까요?") != QMessageBox.Yes:
            return
        s = self.cfg.sender
        s.taildrop_targets = [t for t in s.taildrop_targets if t != name]
        self._save()
        self._build_keep(1)

    def _add_target(self) -> None:
        dlg = AddReceiverDialog(self.cfg, self)
        if dlg.exec():
            self._save()
            self._build_keep(1)

    def _build_keep(self, index: int) -> None:
        self._build()
        self.nav.setCurrentRow(index)

    # ------------------------------------------------------- receiver pages

    def _receiver_backup(self) -> QWidget:
        r = self.cfg.receiver
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self.row_incoming = SettingRow("받는 폴더", r.incoming_dir or "정하지 않았어요")
        self.row_incoming.clicked.connect(lambda: self._pick_receiver_dir("incoming_dir", self.row_incoming))
        self.row_unpack = SettingRow("풀어 둘 폴더", r.unpack_dir or "정하지 않았어요")
        self.row_unpack.clicked.connect(lambda: self._pick_receiver_dir("unpack_dir", self.row_unpack))
        delete = ToggleRow("풀고 나서 압축 지우기", r.delete_after_unpack, "풀어 둔 폴더만 남기고 받은 압축은 지워요")
        delete.switch.toggled.connect(self._set_delete_after)
        lay.addWidget(rows_card(self.row_incoming, self.row_unpack, delete, self._startup_row()))
        lay.addWidget(rows_card(self._advanced_row()))
        return w

    def _pick_receiver_dir(self, field: str, row: SettingRow) -> None:
        current = getattr(self.cfg.receiver, field)
        chosen = QFileDialog.getExistingDirectory(self, "폴더 고르기", current or "")
        if chosen:
            setattr(self.cfg.receiver, field, str(Path(chosen)))
            row.set_value(str(Path(chosen)))
            self._save()

    def _set_delete_after(self, on: bool) -> None:
        self.cfg.receiver.delete_after_unpack = on
        self._save()

    def _receiver_senders(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        registry = pairing.load_known_senders(config_dir() / pairing.KNOWN_SENDERS_FILENAME)
        overdue = {o["device_id"] for o in pairing.overdue_senders(registry)}
        rows = []
        for device_id, info in sorted(registry.items(), key=lambda kv: -float(kv[1].get("last_seen", 0))):
            when = (f"마지막 {wording.when_short(info['last_seen'])}" if info.get("last_seen") else "아직 오지 않았어요")
            note = f"{wording.schedule_name(info.get('interval_minutes') or 0)} · {when}" \
                if info.get("interval_minutes") else when
            r = QWidget()
            r.setLayout(hbox(label(info.get("device_name", device_id)), None,
                             label(note, "caption2", "wn" if device_id in overdue else None),
                             margins=(20, 6, 20, 6)))
            r.setMinimumHeight(52)
            rows.append(r)
        if not rows:
            empty = label("아직 연결한 보내는 컴퓨터가 없어요.", "body2")
            empty.setContentsMargins(20, 12, 20, 12)
            rows.append(empty)
        lay.addWidget(rows_card(*rows))
        add = button("짝 코드 만들기", "primary")
        add.clicked.connect(self._make_code)
        lay.addLayout(hbox(add, None))
        return w

    def _make_code(self) -> None:
        from .main_window import PairingCodeDialog

        PairingCodeDialog(self.cfg, self, log=self.log.line).exec()
        self._build_keep(1)

    # ---------------------------------------------------------- shared pages

    def _startup_row(self) -> ToggleRow:
        row = ToggleRow("Windows를 켤 때 나루도 켜기", startup.is_enabled())
        if not startup.available():
            row.switch.setEnabled(False)
            row.setToolTip("설치한 나루에서만 바꿀 수 있어요")
        row.switch.toggled.connect(lambda on: self._set_startup(row, on))
        return row

    def _set_startup(self, row: ToggleRow, on: bool) -> None:
        if not startup.set_enabled(on):
            row.switch.blockSignals(True)
            row.switch.setChecked(startup.is_enabled())
            row.switch.blockSignals(False)
            QMessageBox.warning(self, "바꾸지 못했어요", "시작 프로그램 바로가기를 바꾸지 못했어요.")

    def _advanced_row(self) -> SettingRow:
        row = SettingRow("고급 설정", "", "압축 수준 · 전송 방식 · 연결 정보. 기본값이면 열지 않아도 돼요")
        row.clicked.connect(self._open_advanced)
        return row

    def _open_advanced(self) -> None:
        from .settings_dialog import SettingsDialog

        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec():
            dlg.apply_to(self.cfg)
            self._save()
            self._build_keep(0)

    def _alerts(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        if self.cfg.role == ROLE_SENDER:
            row = ToggleRow("백업이 건너가지 못했을 때 알려 주기", self.cfg.notify_failures,
                            "세 번 다시 해 봐도 안 될 때 한 번만 알려요")
            row.switch.toggled.connect(lambda on: self._set_flag("notify_failures", on))
        else:
            row = ToggleRow("보내는 컴퓨터가 오지 않을 때 알려 주기", self.cfg.notify_silence,
                            "정한 때보다 한참 늦으면 한 번 알려요")
            row.switch.toggled.connect(lambda on: self._set_flag("notify_silence", on))
        lay.addWidget(rows_card(row))
        lay.addWidget(label("잘 되는 동안에는 알리지 않아요. 같은 상태는 트레이 아이콘과 홈에도 계속 남아요.",
                            "caption", wrap=True))
        return w

    def _set_flag(self, name: str, on: bool) -> None:
        setattr(self.cfg, name, on)
        self._save()

    def _about(self) -> QWidget:
        from tsbackup import __version__

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        brand = Card(padding=16)
        title = label("나루")
        title.setStyleSheet("font-size: 17px; font-weight: 600;")
        brand.body.addLayout(hbox(LogoMark(56), 8, title, None, label(f"버전 {__version__}", "caption2")))
        brand.body.addWidget(label("내 작업 폴더를 매일 건너편 컴퓨터로 건네는 Windows 앱이에요.", "body2", wrap=True))
        lay.addWidget(brand)

        records = SettingRow("자세한 기록 보기")
        records.clicked.connect(self._open_records)
        folder = SettingRow("기록 폴더 열기", str(config_dir()))
        folder.clicked.connect(lambda: self._open_path(config_dir()))
        other = "받는 컴퓨터" if self.cfg.role == ROLE_SENDER else "보내는 컴퓨터"
        role = SettingRow("이 컴퓨터의 역할 바꾸기", f"{other}로 바꾸기")
        role.clicked.connect(self._switch_role)
        rows = [records, folder]
        from .tray import _find_update_manager_exe

        um = _find_update_manager_exe()
        if um is not None:
            upd = SettingRow("업데이트 확인", "지금 확인하기")
            upd.clicked.connect(lambda: self._check_update(um))
            rows.append(upd)
        rows.append(role)
        lay.addWidget(rows_card(*rows))
        return w

    def _open_records(self) -> None:
        from .main_window import RecordDialog

        RecordDialog(self.log, self, runs=self.cfg.role == ROLE_SENDER).exec()

    @staticmethod
    def _open_path(path) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    @staticmethod
    def _check_update(um) -> None:
        import subprocess

        try:
            subprocess.Popen([str(um), "--once"], close_fds=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            pass

    def _switch_role(self) -> None:
        to = ROLE_RECEIVER if self.cfg.role == ROLE_SENDER else ROLE_SENDER
        name = "받는 컴퓨터" if to == ROLE_RECEIVER else "보내는 컴퓨터"
        msg = (f"이 컴퓨터를 {name}로 바꿀까요? 지금 하던 일은 멈추고, "
               f"{name}로 새로 시작해요. 설정은 그대로 남아요.")
        if QMessageBox.question(self, "역할 바꾸기", msg) != QMessageBox.Yes:
            return
        was_running = self.engine.running
        self.engine.stop()
        self.cfg.role = to
        self.cfg.save()
        self._on_role_changed()
        if was_running and not self.cfg.problems():
            self.engine.start()
        self._build()


class AddReceiverDialog(QDialog):
    """받는 컴퓨터 더하기: the wizard's code page, for one more receiver."""

    def __init__(self, cfg, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("받는 컴퓨터 더하기")
        self.setWindowIcon(window_icon())
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)
        lay.addWidget(label("받는 컴퓨터에 뜬 코드를 넣어 주세요", "title", wrap=True))
        lay.addWidget(label("받는 컴퓨터의 나루에서 설정 > 보내는 컴퓨터 > 짝 코드 만들기를 누르면 코드가 보여요.",
                            "body2", wrap=True))
        from .widgets import set_code_font

        self.entry = QLineEdit()
        self.entry.setProperty("kind", "code")
        self.entry.setPlaceholderText("XXXXX-XXXX")
        set_code_font(self.entry, 24, entry=True)
        lay.addWidget(self.entry)
        self.result = label("", "caption2", wrap=True)
        lay.addWidget(self.result)
        cancel = button("취소", "quiet")
        cancel.clicked.connect(self.reject)
        self.go = button("연결하기", "primary")
        self.go.clicked.connect(self._connect)
        lay.addLayout(hbox(None, cancel, self.go))

    def _connect(self) -> None:
        import socket

        from .theme import set_prop

        code = self.entry.text().strip().upper()
        try:
            payload = pairing.resolve_and_pair(
                code, device_name=socket.gethostname(), device_id=self.cfg.ensure_device_id(),
                interval_minutes=self.cfg.sender.interval_minutes, transport_preference="taildrop")
        except pairing.PairingError as exc:
            set_prop(self.entry, "state", "error")
            self.result.setText(f"연결하지 못했어요. {exc}")
            set_prop(self.result, "tone", "er")
            return
        name = payload.get("device_name", "")
        if name and name not in self.cfg.sender.taildrop_targets:
            self.cfg.sender.taildrop_targets.append(name)
        self.accept()
