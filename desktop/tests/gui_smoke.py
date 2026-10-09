"""Headless-capable Qt smoke test - the GUI half selftest.py's own
docstring says is "checked separately by importing it under an offscreen
Qt." Until now that separate check (app/main.py's --check-gui) only
proved imports resolve; this actually constructs the widgets and exercises
their wiring, the same way this project's own development sessions have
verified them by hand.

    cd desktop && python -m tests.gui_smoke

Set QT_QPA_PLATFORM=offscreen first on a machine with no real display (a
Linux CI runner, this repo's own dev sandbox); windows-latest's GitHub
Actions runner has a real desktop session and needs no such override, but
setting it there too is harmless.

No real network, no real Tailscale interface, no real pairing round trip -
that needs actual hardware (see docs/VERIFICATION.md). This proves the
widgets construct, wire their signals correctly, and handle an unreachable
target cleanly, not that pairing works end to end on a real tailnet.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Every window here can save the config (the wizard's 마침, settings that save
# as they change) and the homes read history/known senders from config_dir().
# Point both at a throwaway folder before tsbackup.config computes its paths
# at import - otherwise a test run overwrites the developer's real config.
_SANDBOX = tempfile.mkdtemp(prefix="tsbackup_gui_smoke_")
os.environ["LOCALAPPDATA"] = _SANDBOX
os.environ["TSBACKUP_CONFIG"] = str(Path(_SANDBOX) / "TsBackup" / "config.json")

from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox  # noqa: E402

from app.main_window import MainWindow, ReceiverHome, SenderHome  # noqa: E402
from app.settings_dialog import SettingsDialog  # noqa: E402
from app.tray import Tray  # noqa: E402
from app.wizard import SetupWizard  # noqa: E402
from tsbackup.config import AppConfig, ROLE_RECEIVER, ROLE_SENDER  # noqa: E402
from tsbackup.engine import Engine  # noqa: E402
from tsbackup.engine_core import RunResult  # noqa: E402
from tsbackup.log import Log  # noqa: E402

FAILURES: list[str] = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if not cond else ""))
    if not cond:
        FAILURES.append(name)
    # CI's piped stdout is fully buffered, not line-buffered - a hard
    # native crash (not a normal Python exception) loses everything still
    # sitting in that buffer, showing zero output for tests that actually
    # ran and passed. Flush after every check so a real crash's log still
    # shows exactly how far execution got.
    sys.stdout.flush()


def section(t):
    print(f"\n{t}")
    sys.stdout.flush()


def _fresh(root: Path, role: str = ROLE_SENDER):
    cfg = AppConfig()
    cfg.role = role
    if role == ROLE_RECEIVER:
        cfg.receiver.unpack_dir = str(root / "unpack")
        Path(cfg.receiver.unpack_dir).mkdir(parents=True, exist_ok=True)
    log = Log(root / f"log_{role}.txt")
    engine = Engine(cfg, log)
    return cfg, log, engine


def _rows(list_card) -> list[list[str]]:
    """The text of each 목록 행 in a ListCard, as the person reads it."""
    out = []
    for i in range(list_card.rows.count()):
        w = list_card.rows.itemAt(i).widget()
        if w is not None:
            out.append([lb.text() for lb in w.findChildren(QLabel) if lb.text()])
    return out


def _wait(app, cond, seconds=10) -> None:
    deadline = time.time() + seconds
    while not cond() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)


def test_main_window_both_roles(app: QApplication, root: Path) -> None:
    section("MainWindow construction (both roles)")
    cfg, log, engine = _fresh(root, ROLE_SENDER)
    win = MainWindow(cfg, log, engine, lambda: None)
    check("a sender gets the sender home", isinstance(win.home, SenderHome))
    check("...and the header says which side this is", win.role_pill.text() == "보내는 컴퓨터")
    win.close()

    cfg2, log2, engine2 = _fresh(root, ROLE_RECEIVER)
    win2 = MainWindow(cfg2, log2, engine2, lambda: None)
    check("a receiver gets the receiver home", isinstance(win2.home, ReceiverHome))
    check("an empty receiver shows the empty state, not empty tiles (§09 빈 상태)",
          win2.home.views.currentIndex() == 1)
    win2.close()


def test_receiver_home(app: QApplication, root: Path) -> None:
    section("Receiver home (§09): who sent what, free space, silence acknowledged")
    import json as _json

    from tsbackup import pairing as pairing_module

    cfg, log, engine = _fresh(root, ROLE_RECEIVER)
    conf = root / "recv_conf"
    conf.mkdir(exist_ok=True)
    snap = Path(cfg.receiver.unpack_dir) / "PycharmProjects_2026_10_08_04_00"
    snap.mkdir(parents=True, exist_ok=True)
    (snap / "main.py").write_bytes(b"x" * 2048)
    (snap / ".ts_sender.json").write_text(_json.dumps({"device_id": "dev-a"}), encoding="utf-8")
    long_ago = time.time() - 3 * 86400
    (conf / pairing_module.KNOWN_SENDERS_FILENAME).write_text(_json.dumps({
        "dev-a": {"device_name": "laptop-a", "interval_minutes": 1440,
                  "first_seen": long_ago, "last_seen": long_ago},
    }), encoding="utf-8")

    with patch("app.main_window.config_dir", return_value=conf):
        win = MainWindow(cfg, log, engine, lambda: None)
        win.show()
        home = win.home
        rows = _rows(home.received)
        check("a received snapshot shows who sent it, from its marker, and its size",
              len(rows) == 1 and "laptop-a" in rows[0] and "2.0 KB" in rows[0], rows)
        check("free space is shown under 쓴 공간",
              home.stat_space.detail.text().startswith("남은 공간"), home.stat_space.detail.text())
        check("a silent sender leads the verdict, saying how often it should come",
              "laptop-a" in home.verdict.title.text() and "3일째" in home.verdict.title.text()
              and "하루에 한 번" in home.verdict.sub.text(),
              (home.verdict.title.text(), home.verdict.sub.text()))
        check("...with 확인했어요 beside it", home.ack_btn.isVisible())
        check("보내는 컴퓨터 tile counts the quiet one", "1대 소식 없음" in home.stat_senders.detail.text(),
              home.stat_senders.detail.text())
        home.ack_btn.click()
        win._refresh_receiver_stats()
        check("확인했어요 clears the warning, and the next refresh keeps it cleared",
              not home.ack_btn.isVisible() and home.verdict.title.text() == "잘 되고 있어요",
              home.verdict.title.text())
        check("...but the tile still says a computer is not coming",
              "1대 소식 없음" in home.stat_senders.detail.text())
        win.close()


def test_sender_home(app: QApplication, root: Path) -> None:
    section("Sender home (§09): verdict, folder summary, history, error view")
    from tsbackup import history
    from tsbackup.engine_core import RunResult as _RR

    cfg, log, engine = _fresh(root, ROLE_SENDER)
    conf = root / "send_conf"
    conf.mkdir(exist_ok=True)
    src = root / "send_src"
    (src / "proj_a").mkdir(parents=True, exist_ok=True)
    (src / "proj_b").mkdir(exist_ok=True)
    (src / "proj_a" / "f.bin").write_bytes(b"x" * 4096)
    cfg.sender.source_dir = str(src)
    cfg.sender.interval_minutes = 1440
    cfg.sender.taildrop_targets = ["recv-1", "recv-2"]
    hist = conf / history.FILENAME
    history.record(hist, _RR(False, "a.7z", detail="연결 실패"), 3)
    history.record(hist, _RR(True, "PycharmProjects_2026_10_08_04_00.7z", 2048, 1, "taildrop", "recv-2"), 132)

    with patch("app.main_window.config_dir", return_value=conf):
        win = MainWindow(cfg, log, engine, lambda: None)
        home = win.home
        check("the last good run leads: 잘 되고 있어요, with size and time in words",
              home.verdict.title.text() == "잘 되고 있어요" and "2.0 KB" in home.verdict.sub.text()
              and "2분 12초" in home.verdict.sub.text(), home.verdict.sub.text())
        rows = _rows(home.history_card)
        check("지난 전송 lists runs newest first",
              len(rows) == 2 and "1곳으로 건너갔어요" in rows[0] and "건너가지 못했어요" in rows[1], rows)
        check("받는 컴퓨터 counts its targets", home.targets_title.text() == "받는 컴퓨터 2대",
              home.targets_title.text())
        shown = " ".join(" ".join(r) for r in rows)
        check("the home screen does not name the transport", "taildrop" not in shown, shown)
        _wait(app, lambda: "세는 중" not in home.folder_summary.text())
        check("the folder summary is filled in off the GUI thread",
              home.folder_summary.text() == "2개 폴더 · 4.0 KB · 하루에 한 번", home.folder_summary.text())

        history.record(hist, _RR(False, "b.7z", detail="taildrop: 닿지 못했습니다"), 5)
        home.refresh()
        check("a failed last run switches to the error view (§09 오류)",
              home.views.currentIndex() == 1 and home.err_verdict.title.text() == "건너가지 못했어요")
        check("...naming the receiver it could not reach, not the transport",
              "recv-1" in home.err_verdict.sub.text() and "taildrop" not in home.err_verdict.sub.text(),
              home.err_verdict.sub.text())
        win.close()


def test_role_switch_rebuild(app: QApplication, root: Path) -> None:
    section("MainWindow rebuilds cleanly on a role switch (settings path)")
    cfg, log, engine = _fresh(root, ROLE_SENDER)
    win = MainWindow(cfg, log, engine, lambda: None)
    cfg.role = ROLE_RECEIVER
    cfg.receiver.unpack_dir = str(root / "unpack2")
    Path(cfg.receiver.unpack_dir).mkdir(parents=True, exist_ok=True)
    try:
        win._build_role_body()
        win._refresh_role_ui()
        ok = True
    except Exception as exc:  # noqa: BLE001
        ok = False
        detail = str(exc)
    check("rebuild to receiver body raises nothing", ok, detail if not ok else "")
    check("the header relabels for the new role", win.role_pill.text() == "받는 컴퓨터")
    check("...and the receiver home replaced the sender one", isinstance(win.home, ReceiverHome))
    win.close()


def test_settings_window(app: QApplication, root: Path) -> None:
    section("SettingsWindow (§09 설정): saves as it changes")
    from app.settings_window import SettingsWindow

    cfg, log, engine = _fresh(root, ROLE_SENDER)
    cfg.sender.source_dir = str(root)
    cfg.sender.interval_minutes = 1440
    win = SettingsWindow(cfg, log, engine, None)
    check("sender settings: 백업 / 받는 컴퓨터 / 알림 / 나루 정보",
          [win.nav.item(i).text() for i in range(win.nav.count())]
          == ["백업", "받는 컴퓨터", "알림", "나루 정보"])
    check("얼마나 자주 reads in words, with the clock time",
          win.row_schedule.value.fullText() == "하루에 한 번, 새벽 4시", win.row_schedule.value.fullText())
    win._set_flag("notify_failures", False)
    check("a toggle is saved the moment it changes, no 저장 button",
          AppConfig.load().notify_failures is False)
    win.close()

    cfg2, log2, engine2 = _fresh(root, ROLE_RECEIVER)
    win2 = SettingsWindow(cfg2, log2, engine2, None)
    check("receiver settings: 받기 / 보내는 컴퓨터 / 알림 / 나루 정보",
          [win2.nav.item(i).text() for i in range(win2.nav.count())]
          == ["받기", "보내는 컴퓨터", "알림", "나루 정보"])
    win2.close()


def test_settings_dialog(app: QApplication, root: Path) -> None:
    section("고급 설정 (SettingsDialog): apply_to round-trip")
    cfg = AppConfig()
    dlg = SettingsDialog(cfg)
    check("host_key field exists", hasattr(dlg, "host_key"))
    check("expects_http field exists", hasattr(dlg, "expects_http"))
    dlg.host_key.setText("SHA256:abc123")
    dlg.expects_http.setChecked(True)
    dlg.apply_to(cfg)
    check("host_key round-trips through apply_to", cfg.sender.host_key == "SHA256:abc123")
    check("expects_http round-trips through apply_to", cfg.receiver.expects_http is True)
    dlg.close()


def _dispose_tray(tray) -> None:
    # A Tray has no Qt parent and the engine's signal connections keep its
    # Python wrapper alive, so without this the real notification-area icon
    # outlives the test and is torn down during process exit - where it
    # crashed with an access violation after every check had passed (4 of
    # 10 runs before; 0 of 20 with the two tray tests removed; 0 of 50 once
    # disposed here).
    import shiboken6

    tray.hide()
    shiboken6.delete(tray)


def test_tray_signal_wiring(app: QApplication, root: Path) -> None:
    section("Tray failure state (§09 트레이)")
    cfg, log, engine = _fresh(root, ROLE_SENDER)
    win = MainWindow(cfg, log, engine, lambda: None)
    tray = Tray(win, engine, lambda: None)

    cfg.notify_failures = False   # no toast window in this test
    tray._on_failed_after_retries(RunResult(False, detail="스모크 테스트 실패"))
    check("a failure after retries turns the menu header to 건너가지 못했어요",
          tray.header.title.text() == "건너가지 못했어요", tray.header.title.text())
    check("...and offers 지금 다시 보내기", tray.act_run.text() == "지금 다시 보내기")

    tray._on_run_result(RunResult(True))
    check("the next successful run clears it",
          tray.header.title.text() != "건너가지 못했어요" and tray.act_run.text() == "지금 보내기")
    _dispose_tray(tray)
    win.close()


def test_tray_update_check_action(app: QApplication, root: Path) -> None:
    section("Tray '지금 업데이트 확인' action - present only when an "
            "installer-placed update manager exe actually exists")
    # _find_update_manager_exe() is a plain module-level function (not a
    # Tray method) specifically so both "absent" and "present" cases can
    # be checked without constructing a real QSystemTrayIcon for each one -
    # each is a real OS resource, and this file already constructs one in
    # test_tray_signal_wiring above, so only one more is made below.
    from app.tray import _find_update_manager_exe

    with patch("app.tray.find_tsbackup_install_dir", return_value=None):
        check("no install dir found (dev/source run) -> no update manager exe",
              _find_update_manager_exe() is None)

    fake_install = root / "fake_install"
    fake_install.mkdir(exist_ok=True)
    fake_um_exe = fake_install / "TsBackup_update_manager.exe"
    fake_um_exe.write_bytes(b"stub")
    with patch("app.tray.find_tsbackup_install_dir", return_value=fake_install):
        check("install dir found with the exe present -> resolved",
              _find_update_manager_exe() == fake_um_exe)

        cfg, log, engine = _fresh(root, ROLE_SENDER)
        win = MainWindow(cfg, log, engine, lambda: None)
        tray = Tray(win, engine, lambda: None)
        check("Tray construction picks it up -> menu action is added",
              hasattr(tray, "act_check_update"))

        with patch("app.tray.subprocess.Popen") as popen:
            tray._check_for_update(fake_um_exe)
        check("clicking it launches the exe with --once",
              popen.call_args[0][0] == [str(fake_um_exe), "--once"], popen.call_args)

        _dispose_tray(tray)
        win.close()


def test_wizard(app: QApplication, root: Path) -> None:
    section("SetupWizard sender path (§09: role, code, folder, how often, test)")
    from app import wizard as wiz_module

    cfg = AppConfig()
    wiz = SetupWizard(cfg)
    check("sender starts selected", wiz.role_sender.isSelected() and not wiz.role_receiver.isSelected())
    wiz._role_next()
    check("the code comes right after the role", wiz.stack.currentIndex() == wiz_module.PAGE_PAIR)
    check("다음 stays off until a code connects", not wiz.pair_next.isEnabled())

    wiz.code_entry.setText("00000-0001")
    wiz._resolve_code()
    check("an unreachable code reports failure without crashing",
          "연결하지 못했어요" in wiz.pair_result.text(), wiz.pair_result.text())
    check("...and stays on the code page with 다음 off",
          wiz.stack.currentIndex() == wiz_module.PAGE_PAIR and not wiz.pair_next.isEnabled())

    wiz._go(wiz_module.PAGE_FOLDER)
    wiz.source_edit.setText(str(root / "no-such-folder"))
    wiz._folder_changed()
    check("다음 stays off for a folder that does not exist", not wiz.folder_next.isEnabled())
    folder = root / "wiz_src"
    (folder / "a").mkdir(parents=True, exist_ok=True)
    wiz.source_edit.setText(str(folder))
    wiz._folder_changed()
    check("...and turns on for one that does", wiz.folder_next.isEnabled())
    _wait(app, lambda: wiz.tile_dirs.value.text() not in ("…", "-"))
    check("the folder tiles count it", wiz.tile_dirs.value.text() == "1개", wiz.tile_dirs.value.text())
    wiz._folder_next()
    check("then how often", wiz.stack.currentIndex() == wiz_module.PAGE_SCHEDULE)
    check("하루에 한 번 is the default", wiz._schedule_minutes() == 1440)

    wiz._cleanup()


def test_wizard_test_page(app: QApplication, root: Path) -> None:
    section("SetupWizard test page: the verdict and the next send time")
    from app import wizard as wiz_module

    wiz = SetupWizard(AppConfig())
    wiz._go(wiz_module.PAGE_TEST)
    wiz._on_test_finished(True, "")
    check("success says 잘 건너갔어요 and when the next send is",
          wiz.test_page.title_label.text() == "잘 건너갔어요"
          and wiz.test_page.sub_label.text().startswith("다음 전송은"), wiz.test_page.sub_label.text())
    check("...and the button becomes 마침", wiz.test_finish.text() == "마침" and wiz.test_finish.isEnabled())
    wiz._on_test_finished(False, "받는 컴퓨터에 닿지 못했어요.")
    check("failure offers 다시 해 보기", wiz.test_finish.text() == "다시 해 보기")
    wiz._cleanup()


def test_wizard_tailscale_off(app: QApplication, root: Path) -> None:
    section("SetupWizard receiver: Tailscale off is said plainly")
    with patch("tsbackup.pairing.local_tailscale_ip", lambda: None):
        wiz = SetupWizard(AppConfig())
        wiz._pick_role(ROLE_RECEIVER)
        wiz._role_next()
        check("no code, a message naming Tailscale, and 마침 off",
              "Tailscale" in wiz.pair_status.text.text() and not wiz.receiver_finish.isEnabled(),
              wiz.pair_status.text.text())
        wiz._cleanup()


@contextmanager
def _no_real_bind(fake_ip: str):
    """No real Tailscale interface in this sandbox to bind to - patch out the
    socket bind while still exercising the code-generation and gating logic
    real hardware would drive identically. Shared by every test that
    constructs a PairingListener, so the bypass can't be forgotten on one
    of them (as `test_pairing_code_dialog` once was)."""
    import tsbackup.pairing as pairing_module

    def _fake_start(self, ip):
        self._tailscale_ip = ip
        return self.regenerate()

    with patch.object(pairing_module.PairingListener, "start", _fake_start), \
         patch.object(pairing_module, "local_tailscale_ip", lambda: fake_ip):
        yield


def test_wizard_receiver_page(app: QApplication, root: Path) -> None:
    section("SetupWizard receiver page: copy button, paired-vs-confirmed gating")
    with _no_real_bind("100.90.1.2"):
        cfg = AppConfig()
        wiz = SetupWizard(cfg)
        wiz._pick_role(ROLE_RECEIVER)
        wiz._role_next()
        check("a code is shown on the receiver page", bool(wiz.code_label.text()))
        check("마침 starts disabled - pairing hasn't happened yet",
              not wiz.receiver_finish.isEnabled())

        wiz._copy_receiver_code()
        check("복사 puts the shown code on the clipboard",
              app.clipboard().text() == wiz.code_label.text())

        # Regression guard for the real bug: /pair succeeding alone must
        # NOT enable 마침 - only after the sender's mandatory test-transfer
        # has actually confirmed. Enabling it on is_paired() alone let the
        # receiver close the wizard (tearing down the listener) while the
        # sender's test-transfer was still in flight, stranding it and
        # forcing the whole pairing to be redone from a fresh code.
        wiz._listener._session.paired = True
        wiz._poll_paired()
        check("마침 stays disabled when paired but not yet confirmed",
              not wiz.receiver_finish.isEnabled())
        check("status reflects waiting for the sender's test-transfer",
              "기다리고" in wiz.pair_status.text.text(), wiz.pair_status.text.text())

        wiz._listener._session.confirmed = True
        wiz._poll_paired()
        check("마침 enables only once the test-transfer is actually confirmed",
              wiz.receiver_finish.isEnabled())

        wiz._cleanup()


def test_wizard_receiver_expiry_escape_hatch(app: QApplication, root: Path) -> None:
    section("SetupWizard receiver page: expiry messaging + 시험 없이 마침 escape hatch")
    with _no_real_bind("100.90.1.4"):
        import tsbackup.pairing as pairing_module

        cfg = AppConfig()
        wiz = SetupWizard(cfg)
        # A child widget's isVisible() only reflects the whole ancestor
        # chain once the top-level window is actually shown, so show()
        # first or the visibility checks below are meaningless (see the
        # same note on SettingsDialog's test above).
        wiz.show()
        wiz._pick_role(ROLE_RECEIVER)
        wiz._role_next()

        check("escape hatch is hidden while a code is still fresh",
              not wiz.receiver_finish_anyway.isVisible())

        wiz._listener._session.created_at = (
            time.time() - pairing_module.CODE_TTL_SECONDS - 1)
        wiz._poll_paired()
        check("status shows expiry once the code's TTL passes unconfirmed",
              "만료" in wiz.pair_status.text.text(), wiz.pair_status.text.text())
        check("시험 없이 마침 escape hatch becomes visible on expiry",
              wiz.receiver_finish_anyway.isVisible())

        with patch("PySide6.QtWidgets.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.No):
            wiz._finish_without_confirm()
        check("declining the warning does not close the wizard",
              wiz.result() != QDialog.DialogCode.Accepted)

        with patch("PySide6.QtWidgets.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes):
            wiz._finish_without_confirm()
        check("accepting the warning finishes the wizard despite no confirm",
              wiz.result() == QDialog.DialogCode.Accepted)
        check("finishing the wizard turns on the engine's autostart - otherwise "
              "no scheduled run ever starts on its own", cfg.autostart_engine is True)


def test_wizard_close_routes_through_reject(app: QApplication, root: Path) -> None:
    section("SetupWizard: closing the window routes through reject() -> _cleanup()")
    with _no_real_bind("100.90.1.6"):
        cfg = AppConfig()
        wiz = SetupWizard(cfg)
        # QDialog.closeEvent()'s own default implementation only calls
        # reject() when isVisible() is true at close time - a dialog that
        # was never shown reports Rejected anyway (that's just the
        # un-set default result()), which would make this test pass
        # vacuously without actually exercising the close path. show()
        # first so the check below proves something real.
        wiz.show()
        wiz._pick_role(ROLE_RECEIVER)
        wiz._role_next()
        check("listener is bound while the wizard is open",
              wiz._listener is not None)

        # SetupWizard doesn't override closeEvent, so this proves Qt's own
        # default (QDialog.closeEvent calls reject() while visible) is
        # really what's wired up - the same path Esc and the title bar's
        # X button take - rather than assuming it without a test.
        wiz.close()
        check("closing the window rejects the dialog rather than silently "
              "hiding it (this is also the X-button/Esc path, since "
              "SetupWizard adds no closeEvent override of its own)",
              wiz.result() == QDialog.DialogCode.Rejected)
        check("...and tears down the pairing listener, not just the widget",
              wiz._listener is None)


def test_pairing_code_dialog(app: QApplication, root: Path) -> None:
    section("PairingCodeDialog: copy button (main_window.py)")
    from app.main_window import PairingCodeDialog

    with _no_real_bind("100.90.1.3"):
        cfg = AppConfig()
        dlg = PairingCodeDialog(cfg)
        check("a code is shown", bool(dlg.code_label.text()) and dlg.code_label.text() != "...")
        dlg._copy_code()
        check("복사 puts the shown code on the clipboard",
              app.clipboard().text() == dlg.code_label.text())
        if dlg._listener:
            dlg._listener.stop()


def test_single_instance(app: QApplication, root: Path) -> None:
    section("single instance (app/single_instance.py)")
    from app.single_instance import InstanceGuard

    lock = root / "si" / "tsbackup.lock"
    shown: list[int] = []
    first = InstanceGuard(lock)
    check("the first launch becomes the instance", first.claim(lambda: shown.append(1)))

    # A real second process, as a second double-click would be: the guard's
    # client side blocks on the pipe, so in-process it would wait on the very
    # event loop that has to answer it.
    import subprocess

    child = subprocess.Popen(
        [sys.executable, "-c",
         "import sys; from PySide6.QtCore import QCoreApplication; "
         "from app.single_instance import InstanceGuard; from pathlib import Path; "
         "a = QCoreApplication([]); "
         "sys.exit(0 if InstanceGuard(Path(sys.argv[1])).claim(lambda: None) else 3)",
         str(lock)],
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    deadline = time.time() + 10
    while (not shown or child.poll() is None) and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    check("a second launch is refused", child.wait(timeout=5) == 3, child.returncode)
    check("...and asks the running one to show its window", shown == [1], shown)

    first.release()
    third = InstanceGuard(lock)
    check("once the instance exits, a new launch is allowed", third.claim(lambda: None))
    third.release()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    # ignore_cleanup_errors (3.10+): every individual check already passed
    # by the time this dir is deleted - if some file inside it is still
    # transiently locked (an AV scan, a not-yet-released OS handle from one
    # of the many Qt widgets these tests construct), that's not a test
    # failure worth crashing over. Seen for real: a first CI run here died
    # with zero output and exit code 1 right at this cleanup boundary, no
    # traceback captured - the print()/FAILURES summary below now also
    # runs *before* cleanup, not after, so the actual pass/fail verdict
    # survives even if directory deletion has trouble on the way out.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        test_main_window_both_roles(app, root)
        test_receiver_home(app, root)
        test_sender_home(app, root)
        test_role_switch_rebuild(app, root)
        test_settings_window(app, root)
        test_settings_dialog(app, root)
        test_tray_signal_wiring(app, root)
        test_tray_update_check_action(app, root)
        test_wizard(app, root)
        test_wizard_test_page(app, root)
        test_wizard_tailscale_off(app, root)
        test_wizard_receiver_page(app, root)
        test_wizard_receiver_expiry_escape_hatch(app, root)
        test_wizard_close_routes_through_reject(app, root)
        test_pairing_code_dialog(app, root)
        test_single_instance(app, root)

        print()
        if FAILURES:
            print(f"FAILED: {len(FAILURES)}")
            for name in FAILURES:
                print("  -", name)
            result = 1
        else:
            print("all checks passed")
            result = 0
        sys.stdout.flush()

    return result


if __name__ == "__main__":
    _code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # Real evidence, not a guess: CI printed "all checks passed" cleanly,
    # then died with exit code 1 ~130-190ms later with zero further
    # output - no traceback, nothing - on FOUR consecutive runs. Switching
    # `raise SystemExit(main())` to `os._exit(_code)` (skips Python's own
    # cleanup/GC/atexit entirely) changed *nothing* - same crash, same gap.
    # That rules out a Python-level teardown problem. What os._exit()
    # does NOT skip on Windows is the OS's own process-exit path: the C
    # runtime's underlying _exit()/ExitProcess() still sends
    # DLL_PROCESS_DETACH to every loaded DLL (Qt's, PySide6's native
    # modules, shiboken6) as the process unloads - outside Python's
    # control for any exit path except one. TerminateProcess() is
    # documented to skip DLL_PROCESS_DETACH notification for the
    # terminating process's own DLLs - the standard PyInstaller/PySide/
    # PyQt escape hatch for exactly this "crashes only on exit" symptom.
    # Every real result is already printed and flushed above, so there is
    # nothing a graceful (or even os._exit-graceful) shutdown still buys.
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.TerminateProcess(kernel32.GetCurrentProcess(), _code)
    os._exit(_code)
