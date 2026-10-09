"""Tray icon, menu and alerts - 나루 디자인 시스템 §03 (트레이 아이콘 상태)
and §09 (트레이 · 알림: "평소에는 이것만 봐요").

Icon: 정상 / 연결 중 / 주의 / 실패 / 멈춤. The 주의·실패 badge goes away
only when the person presses 확인했어요 on the home or the next send
succeeds - a toast can be missed, the icon cannot.

Menu: a state header (dot, verdict, one line), then 나루 열기, 지금 (다시)
보내기, 잠시 멈추기 ›, the folder, 나루 끝내기.

Alerts are bad news only (principle ④), and once per incident: a sender's
failure after its retries are exhausted (Engine.failed_after_retries),
and a receiver's sender going quiet.
"""

from __future__ import annotations

import subprocess
import sys

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget, QWidgetAction

from tsbackup import pairing
from tsbackup.config import ROLE_RECEIVER, config_dir
from tsbackup.engine_core import MAX_RETRIES
from update_manager.paths import UM_EXE_NAME, find_tsbackup_install_dir

from . import wording
from .icons import app_icon
from .widgets import Dot, hbox, label, vbox

SILENCE_CHECK_MS = 30 * 60 * 1000


def _find_update_manager_exe():
    """Module-level, not a Tray method: lets a test exercise this lookup
    without constructing a real QSystemTrayIcon."""
    install_dir = find_tsbackup_install_dir()
    if install_dir is None:
        return None
    exe = install_dir / UM_EXE_NAME
    return exe if exe.is_file() else None


class _MenuHeader(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.dot = Dot("pr")
        self.title = label("", "section")
        self.sub = label("", "caption")
        self.setLayout(hbox(self.dot, vbox(self.title, self.sub, spacing=0), None,
                            spacing=4, margins=(6, 8, 16, 8)))

    def set(self, tone: str, title: str, sub: str) -> None:
        self.dot.set_tone(tone)
        self.title.setText(title)
        self.sub.setText(sub)


class Tray(QSystemTrayIcon):
    def __init__(self, window, engine, on_quit) -> None:
        super().__init__(app_icon("ok"))
        self.window = window
        self.engine = engine
        self._on_quit = on_quit
        self._error = False
        self._paused = False
        self._last_fail = None
        self._toast = None
        self._resume_timer = QTimer(self, singleShot=True)
        self._resume_timer.timeout.connect(self.engine.resume)
        self.setToolTip("나루")
        receiver = window.cfg.role == ROLE_RECEIVER

        menu = QMenu()
        self.header = _MenuHeader()
        head = QWidgetAction(menu)
        head.setDefaultWidget(self.header)
        menu.addAction(head)
        menu.addSeparator()
        self.act_open = QAction("나루 열기", menu)
        self.act_run = QAction("지금 확인하기" if receiver else "지금 보내기", menu)
        self.pause_menu = QMenu("잠시 멈추기", menu)
        for text, minutes in (("1시간 동안", 60), ("오늘 하루", 24 * 60), ("다시 켤 때까지", 0)):
            a = QAction(text, self.pause_menu)
            a.triggered.connect(lambda _c=False, m=minutes: self._pause_for(m))
            self.pause_menu.addAction(a)
        self.act_resume = QAction("다시 시작하기", menu)
        self.act_resume.triggered.connect(self._resume)
        self.act_folder = QAction("받는 폴더 열기" if receiver else "보내는 폴더 열기", menu)
        self.act_quit = QAction("나루 끝내기", menu)
        menu.addAction(self.act_open)
        menu.addAction(self.act_run)
        self.act_pause = menu.addMenu(self.pause_menu)
        menu.addAction(self.act_resume)
        menu.addAction(self.act_folder)

        # Only when the installer placed an update manager (a source run
        # has none). Fire-and-forget: it logs its own result.
        um_exe = _find_update_manager_exe()
        if um_exe is not None:
            self.act_check_update = QAction("업데이트 확인하기", menu)
            self.act_check_update.triggered.connect(lambda: self._check_for_update(um_exe))
            menu.addAction(self.act_check_update)
        menu.addSeparator()
        menu.addAction(self.act_quit)
        self.setContextMenu(menu)
        self._menu = menu

        self.act_open.triggered.connect(self._open)
        self.act_run.triggered.connect(self._run_now)
        self.act_folder.triggered.connect(self._open_folder)
        self.act_quit.triggered.connect(self._on_quit)
        self.activated.connect(self._on_activated)
        menu.aboutToShow.connect(self._refresh)

        engine.status.connect(self._on_status)
        engine.run_finished.connect(self._on_run_result)
        engine.failed_after_retries.connect(self._on_failed_after_retries)

        self._silence_seen: set[tuple[str, float]] = set()
        self._silence_timer = QTimer(self, interval=SILENCE_CHECK_MS)
        self._silence_timer.timeout.connect(self._check_silence)
        if receiver:
            self._silence_timer.start()
            QTimer.singleShot(5000, self._check_silence)
        self._refresh()

    # -------------------------------------------------------------- actions

    def _check_for_update(self, um_exe) -> None:
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        try:
            subprocess.Popen([str(um_exe), "--once"], close_fds=True, **kwargs)
        except OSError:
            pass

    def _open(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self.window.isVisible():
                self.window.hide()
            else:
                self._open()

    def _run_now(self) -> None:
        self.window._run_now()

    def _open_folder(self) -> None:
        cfg = self.window.cfg
        path = cfg.receiver.unpack_dir if cfg.role == ROLE_RECEIVER else cfg.sender.source_dir
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _pause_for(self, minutes: int) -> None:
        if not self.engine.running:
            return
        self.engine.pause()
        if minutes:
            self._resume_timer.start(minutes * 60 * 1000)

    def _resume(self) -> None:
        self._resume_timer.stop()
        if self.engine.running:
            self.engine.resume()
        else:
            self.engine.start()

    def _toggle_pause(self) -> None:
        if self.engine.busy or not self._paused:
            self._pause_for(0)
        else:
            self._resume()

    # ---------------------------------------------------------------- state

    def _state(self) -> tuple[str, str, str, str]:
        """(icon state, dot tone, title, line) for the icon and menu header."""
        cfg = self.window.cfg
        due = self.engine.due
        nxt = f"다음 전송 {wording.when(due)}" if due else ""
        if self._paused:
            return "paused", "bd2", "잠시 멈췄어요", "다시 시작하기를 누르면 이어서 해요"
        if self.engine.busy:
            return "link", "tl", "보내고 있어요", "건너편으로 보내는 중이에요"
        if self._error:
            return "err", "er", "건너가지 못했어요", "다음 전송 때 다시 보내요"
        if cfg.role == ROLE_RECEIVER and self._unacked_silence():
            o = self._unacked_silence()[0]
            return ("warn", "wn", f"{o.get('device_name', o['device_id'])}에서 소식이 없어요",
                    f"{wording.days_since(o['last_seen'])}일째 오지 않아요")
        if not self.engine.running:
            return "paused", "bd2", "꺼져 있어요", "지금 보내기를 누르면 다시 켜져요"
        if cfg.role == ROLE_RECEIVER:
            return "ok", "pr", "받을 준비가 됐어요", "5초마다 확인하고 있어요"
        return "ok", "pr", "잘 되고 있어요", nxt

    def _unacked_silence(self) -> list[dict]:
        registry = pairing.load_known_senders(config_dir() / pairing.KNOWN_SENDERS_FILENAME)
        acked = getattr(self.engine, "acked_silence", set())
        return [o for o in pairing.overdue_senders(registry)
                if (o["device_id"], o.get("last_seen")) not in acked]

    def _refresh(self) -> None:
        icon, tone, title, sub = self._state()
        self.setIcon(app_icon(icon))
        self.setToolTip(f"나루 — {title}")
        self.header.set(tone, title, sub)
        self.act_run.setText("지금 다시 보내기" if self._error else
                             ("지금 확인하기" if self.window.cfg.role == ROLE_RECEIVER else "지금 보내기"))
        self.act_pause.setVisible(not self._paused and self.engine.running)
        self.act_resume.setVisible(self._paused or not self.engine.running)

    def _on_status(self, text: str) -> None:
        self._paused = text == "일시중지"
        if not self._paused:
            self._resume_timer.stop()
        self._refresh()

    def _on_run_result(self, result) -> None:
        if result.ok and self._error:
            self._error = False
        self._refresh()

    def clear_alert(self) -> None:
        """확인했어요 on the home clears the badge."""
        self._error = False
        self._refresh()

    # --------------------------------------------------------------- alerts

    def _on_failed_after_retries(self, result) -> None:
        self._error = True
        self._last_fail = result
        self._refresh()
        if not self.window.cfg.notify_failures:
            return
        s = self.window.cfg.sender
        target = (s.taildrop_targets or [s.host or ""])[0]
        where = f"받는 컴퓨터 {target}에" if target else "받는 컴퓨터에"
        self._show_toast(
            "백업이 건너가지 못했어요",
            # The archive is parked in pending/ and the next run resends it
            # (tsbackup/pending.py), so this promise is true.
            f"{where} 닿지 못했어요. {MAX_RETRIES}번 다시 해 봤어요. "
            "압축은 만들어 뒀고, 다음 전송 때 같이 보낼게요.",
            [("지금 다시 보내기", self._run_now), ("열어 보기", self._open)],
        )

    def _check_silence(self) -> None:
        self._refresh()
        if self.window.cfg.role != ROLE_RECEIVER or not self.window.cfg.notify_silence:
            return
        for o in self._unacked_silence():
            key = (o["device_id"], o.get("last_seen"))
            if key in self._silence_seen:
                continue
            self._silence_seen.add(key)
            name = o.get("device_name", o["device_id"])
            self._show_toast(
                f"{name}에서 {wording.days_since(o['last_seen'])}일째 오지 않아요",
                f"{wording.schedule_name(o.get('interval_minutes'))} 오기로 했어요. "
                "그 컴퓨터가 꺼져 있는지 확인해 주세요.",
                [("열어 보기", self._open)],
            )
            break

    def _show_toast(self, title: str, body: str, actions) -> None:
        from .toast import Toast

        if self._toast is not None:
            try:
                self._toast.close()
            except RuntimeError:
                pass
        self._toast = Toast(title, body, actions)
        self._toast.popup()
