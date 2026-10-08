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

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from app.main_window import MainWindow  # noqa: E402
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


def test_main_window_both_roles(app: QApplication, root: Path) -> None:
    section("MainWindow construction (both roles)")
    cfg, log, engine = _fresh(root, ROLE_SENDER)
    win = MainWindow(cfg, log, engine, lambda: None)
    check("sender home has folder/target widgets",
          hasattr(win, "sender_folder_label") and hasattr(win, "sender_targets_label"))
    win.close()

    cfg2, log2, engine2 = _fresh(root, ROLE_RECEIVER)
    win2 = MainWindow(cfg2, log2, engine2, lambda: None)
    check("receiver home has stat tiles + silence banner",
          hasattr(win2, "stat_stored") and hasattr(win2, "silence_banner"))
    win2.close()


def test_receiver_home(app: QApplication, root: Path) -> None:
    section("Receiver home (design 5b): who sent what, free space, silence acknowledged")
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
        row = win.received_list.topLevelItem(0)
        check("a received snapshot shows who sent it, from its marker",
              row is not None and row.text(1) == "laptop-a", row and row.text(1))
        check("...and its size", row is not None and row.text(2) == "2.0 KB", row and row.text(2))
        check("free space is shown under 쓴 공간",
              win.stat_space["detail"].text().startswith("남은 공간"), win.stat_space["detail"].text())
        check("a silent sender raises the banner, saying how often it should come",
              win.silence_banner.isVisible() and "laptop-a" in win.silence_title.text()
              and "하루에 한 번" in win.silence_detail.text(), win.silence_detail.text())
        check("the latest unpacked copy is named: when, from whom, how many folders",
              win.latest_copy.text().startswith("마지막으로 확인된 복사본")
              and "laptop-a" in win.latest_copy.text(), win.latest_copy.text())
        win.silence_ack.click()
        win._refresh_receiver_stats()
        check("확인했어요 hides the banner, and the next refresh keeps it hidden",
              not win.silence_banner.isVisible())
        check("...but the verdict still says a computer is not coming",
              "오지 않는 컴퓨터" in win.verdict.text(), win.verdict.text())
        win.close()


def test_sender_home(app: QApplication, root: Path) -> None:
    section("Sender home (design 5b): verdict, folder summary, history")
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
    history.record(hist, _RR(True, "PycharmProjects_2026_10_08_04_00.7z", 2048, 1, "taildrop"), 132)

    with patch("app.main_window.config_dir", return_value=conf):
        win = MainWindow(cfg, log, engine, lambda: None)
        check("the last run's verdict leads the status card - 보냈어요, not a "
              "claim the far side unpacked it, so not the blue success colour",
              win.verdict.text() == "✓ 보냈어요" and "color" not in win.verdict.styleSheet(),
              (win.verdict.text(), win.verdict.styleSheet()))
        check("...with the whole pass's time, in words",
              "2분 12초 걸렸어요" in win.status_detail.text(), win.status_detail.text())
        check("지난 전송 lists runs newest first",
              win.history_list.topLevelItemCount() == 2
              and win.history_list.topLevelItem(1).text(2).startswith("✗"),
              [win.history_list.topLevelItem(i).text(2) for i in range(win.history_list.topLevelItemCount())])
        check("받는 컴퓨터 counts its targets", win.sender_targets_title.text() == "받는 컴퓨터 2대",
              win.sender_targets_title.text())
        check("the home screen does not name the transport",
              "taildrop" not in win.sender_targets_label.text()
              and "taildrop" not in win.history_list.topLevelItem(0).text(2))
        deadline = time.time() + 10
        while "세는 중" in win.sender_folder_summary.text() and time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
        check("the folder summary is filled in off the GUI thread",
              win.sender_folder_summary.text() == "2개 폴더 · 4.0 KB · 하루에 한 번",
              win.sender_folder_summary.text())
        win.close()


def test_role_switch_rebuild(app: QApplication, root: Path) -> None:
    section("MainWindow rebuilds cleanly on a role switch (settings dialog path)")
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
    check("btn_run relabels for the new role", win.btn_run.text() == "지금 확인")
    win.close()


def test_settings_dialog(app: QApplication, root: Path) -> None:
    section("SettingsDialog construction, disclosure toggle, apply_to round-trip")
    cfg = AppConfig()
    dlg = SettingsDialog(cfg)
    check("host_key field exists (Phase 1)", hasattr(dlg, "host_key"))
    check("expects_http field exists (Phase 1)", hasattr(dlg, "expects_http"))

    from PySide6.QtWidgets import QPushButton
    toggles = [b for b in dlg.findChildren(QPushButton) if b.isCheckable()]
    check("two advanced-section disclosure toggles exist (Phase 2)", len(toggles) == 2, toggles)

    # A child widget's isVisible() only reflects the whole ancestor chain
    # once the top-level window is actually shown - not just its own
    # setVisible() flag - so show() first or this assertion is meaningless.
    dlg.show()
    check("host_key starts collapsed (advanced section closed by default)",
          not dlg.host_key.isVisible())
    for b in toggles:
        b.setChecked(True)
    check("host_key becomes visible once its disclosure is opened",
          dlg.host_key.isVisible())

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
    section("Tray failure-notification wiring (Phase 1)")
    cfg, log, engine = _fresh(root, ROLE_SENDER)
    win = MainWindow(cfg, log, engine, lambda: None)
    tray = Tray(win, engine, lambda: None)

    tray._on_failed_after_retries(RunResult(False, detail="스모크 테스트 실패"))
    check("error flag sets on failed_after_retries", tray._error is True)

    tray._on_run_result(RunResult(True))
    check("error flag clears on the next successful run", tray._error is False)
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
    section("SetupWizard construction and sender-path wiring (Phase 3)")
    from app import wizard as wiz_module

    cfg = AppConfig()
    wiz = SetupWizard(cfg)
    check("one question per page: role, ready, code | folder, how often, code, test",
          wiz.stack.count() == 7)

    wiz.role_sender.setChecked(True)
    wiz._role_next()
    check("choosing sender starts at the folder page",
          wiz.stack.currentIndex() == wiz_module.PAGE_FOLDER)

    wiz.source_edit.setText(str(root / "no-such-folder"))
    check("다음 stays off for a folder that does not exist", not wiz.folder_next.isEnabled())
    folder = root / "wiz_src"
    folder.mkdir(exist_ok=True)
    wiz.source_edit.setText(str(folder))
    check("...and turns on for one that does", wiz.folder_next.isEnabled())
    wiz._folder_next()
    check("then how often", wiz.stack.currentIndex() == wiz_module.PAGE_SCHEDULE)
    check("하루에 한 번 is the default", wiz._schedule_minutes() == 1440)
    wiz._schedule_next()
    check("the code comes last, after the interval is known",
          wiz.stack.currentIndex() == wiz_module.PAGE_PAIR and cfg.sender.interval_minutes == 1440)

    wiz.code_entry.setText("00000-0001")
    wiz._resolve_code()
    check("an unreachable code reports failure without crashing",
          "연결하지 못했어요" in wiz.pair_result.text(), wiz.pair_result.text())
    check("...and stays on the code page", wiz.stack.currentIndex() == wiz_module.PAGE_PAIR)

    wiz._cleanup()


def test_wizard_ready_page(app: QApplication, root: Path) -> None:
    section("SetupWizard receiver: the readiness page checks Tailscale first")
    with patch("tsbackup.pairing.local_tailscale_ip", lambda: None):
        wiz = SetupWizard(AppConfig())
        wiz.role_receiver.setChecked(True)
        wiz._role_next()
        check("Tailscale off: says so and 다음 stays off",
              not wiz.ready_next.isEnabled() and "Tailscale" in wiz.ready_status.text(),
              wiz.ready_status.text())
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
        wiz.role_receiver.setChecked(True)
        wiz._role_next()
        wiz._ready_next()
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
              "기다리고" in wiz.pair_status.text(), wiz.pair_status.text())

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
        wiz.role_receiver.setChecked(True)
        wiz._role_next()
        wiz._ready_next()

        check("escape hatch is hidden while a code is still fresh",
              not wiz.receiver_finish_anyway.isVisible())

        wiz._listener._session.created_at = (
            time.time() - pairing_module.CODE_TTL_SECONDS - 1)
        wiz._poll_paired()
        check("status shows expiry once the code's TTL passes unconfirmed",
              "만료" in wiz.pair_status.text(), wiz.pair_status.text())
        check("시험 없이 마침 escape hatch becomes visible on expiry",
              wiz.receiver_finish_anyway.isVisible())

        with patch("app.wizard.QMessageBox.warning",
                   return_value=QMessageBox.StandardButton.No):
            wiz._finish_without_confirm()
        check("declining the warning does not close the wizard",
              wiz.result() != QDialog.DialogCode.Accepted)

        with patch("app.wizard.QMessageBox.warning",
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
        wiz.role_receiver.setChecked(True)
        wiz._role_next()
        wiz._ready_next()
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
        test_settings_dialog(app, root)
        test_tray_signal_wiring(app, root)
        test_tray_update_check_action(app, root)
        test_wizard(app, root)
        test_wizard_ready_page(app, root)
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
