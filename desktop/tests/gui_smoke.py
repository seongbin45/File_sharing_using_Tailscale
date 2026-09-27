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

import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

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


def section(t):
    print(f"\n{t}")


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


def test_tray_signal_wiring(app: QApplication, root: Path) -> None:
    section("Tray failure-notification wiring (Phase 1)")
    cfg, log, engine = _fresh(root, ROLE_SENDER)
    win = MainWindow(cfg, log, engine, lambda: None)
    tray = Tray(win, engine, lambda: None)

    tray._on_failed_after_retries(RunResult(False, detail="스모크 테스트 실패"))
    check("error flag sets on failed_after_retries", tray._error is True)

    tray._on_run_result(RunResult(True))
    check("error flag clears on the next successful run", tray._error is False)
    win.close()


def test_wizard(app: QApplication, root: Path) -> None:
    section("SetupWizard construction and sender-path wiring (Phase 3)")
    cfg = AppConfig()
    wiz = SetupWizard(cfg)
    check("wizard has 4 pages (role, receiver, sender, test)", wiz.stack.count() == 4)

    wiz.role_sender.setChecked(True)
    wiz._role_next()
    check("choosing sender advances to the sender page", wiz.stack.currentIndex() == 2)

    wiz.code_entry.setText("00000-0001")
    wiz._resolve_code()
    check("an unreachable code reports failure without crashing",
          "실패" in wiz.pair_result.text(), wiz.pair_result.text())
    check("sender_next stays disabled after a failed resolve",
          not wiz.sender_next.isEnabled())

    wiz._cleanup()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        test_main_window_both_roles(app, root)
        test_role_switch_rebuild(app, root)
        test_settings_dialog(app, root)
        test_tray_signal_wiring(app, root)
        test_wizard(app, root)

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)}")
        for name in FAILURES:
            print("  -", name)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
