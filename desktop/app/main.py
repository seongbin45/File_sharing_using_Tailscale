"""Entry point.

    python -m app.main          launch the GUI
    python -m app.main --run    one sender pass, headless, then exit
    python -m app.main --scan   one receiver pass, headless, then exit

The two headless modes exist so a machine can run the same binary from Task
Scheduler without a window - the GUI is for setting it up and watching it, not
a requirement for it to work. They also give the tests a way to drive a real
run without a display.
"""

from __future__ import annotations

import argparse
import sys

# Windows' console defaults to a legacy codepage (cp1252), not UTF-8, so a
# bare print() of the Korean text below - including argparse's own --help
# strings - would crash. Force UTF-8 before argparse or any print() runs.
# hasattr guards both: PyInstaller's console=False (windowed) build gives
# this process no console at all, so sys.stdout/stderr are None here, not
# just non-UTF-8. See docs/VERIFICATION.md section 15.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path

from tsbackup import engine_core
from tsbackup.config import CONFIG_PATH, AppConfig, config_dir
from tsbackup.log import Log
from tsbackup.receiver import Receiver


def _log() -> Log:
    return Log(config_dir() / "tsbackup.log")


def _say(log: Log, msg: str) -> None:
    """Record msg to the log file, and echo it to a console if one exists.

    The windowed (console=False) build - the one Task Scheduler launches -
    has no console at all: sys.stdout is None, and a bare print() would
    crash --run/--scan every time. log.line() is the durable record and is
    left unguarded (a failure there, e.g. disk full, is worth surfacing);
    the console echo is best-effort only and must never take the run down.
    """
    log.line(msg)
    if sys.stdout is not None:
        try:
            print(msg)
        except Exception:
            pass


def _headless_run() -> int:
    cfg = AppConfig.load()
    log = _log()
    problems = cfg.problems()
    if problems:
        for p in problems:
            _say(log, f"설정 필요: {p}")
        return 2
    result = engine_core.run_sender_once(cfg, log.line)
    return 0 if result.ok else 1


def _headless_scan() -> int:
    cfg = AppConfig.load()
    log = _log()
    receiver = Receiver(cfg, log.line)
    if cfg.receiver_uses_http():
        _say(log, "http 수신은 GUI 상주가 필요합니다. --scan 은 폴더 방식만 처리합니다.")
    count = receiver.scan_once()
    _say(log, f"{count}개 처리")
    return 0


def _check_gui_imports() -> int:
    """CI-only diagnostic: prove the GUI path's imports resolve, without
    opening a real window or event loop (so it's safe on a headless runner
    with no display). This exists because --config exits before ever
    reaching _gui(), so it never caught the relative-import bug that broke
    every real launch of the shipped .exe - this exercises the same import
    statements _gui() does, inside this same frozen entry script.
    """
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon  # noqa: F401

    from app.main_window import MainWindow  # noqa: F401
    from app.tray import Tray  # noqa: F401

    return 0


def _gui() -> int:
    # Imported here so the headless modes do not require Qt to be present.
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    # Absolute, not relative: PyInstaller's frozen bootloader runs this
    # file directly as bare __main__ with no parent package, unlike
    # `python -m app.main` (which sets one up) - a relative import here
    # crashes the shipped .exe with "attempted relative import with no
    # known parent package" the moment the GUI path runs. main_window.py
    # and tray.py's OWN relative imports (of .icons etc.) stay fine either
    # way, since those modules load normally once imported from here.
    from app.main_window import MainWindow
    from app.tray import Tray

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)   # closing the window hides to tray

    cfg = AppConfig.load()
    log = Log(config_dir() / "tsbackup.log")

    from tsbackup.engine import Engine
    engine = Engine(cfg, log)

    quit_called = {"done": False}

    def do_quit() -> None:
        if quit_called["done"]:
            return
        quit_called["done"] = True
        engine.stop()
        app.quit()

    window = MainWindow(cfg, log, engine, do_quit)

    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = Tray(window, engine, do_quit)
        tray.show()
    else:
        log.line("시스템 트레이를 쓸 수 없습니다. 창을 닫으면 종료됩니다.")
        cfg.minimize_to_tray = False

    window.show()

    if cfg.autostart_engine and not cfg.problems():
        engine.start()

    return app.exec()


def main() -> int:
    parser = argparse.ArgumentParser(description="TS Backup")
    parser.add_argument("--run", action="store_true", help="한 번 압축·전송 후 종료")
    parser.add_argument("--scan", action="store_true", help="수신 폴더를 한 번 처리 후 종료")
    parser.add_argument("--config", help="설정 파일 경로 표시", action="store_true")
    parser.add_argument(
        "--check-gui", action="store_true",
        help=argparse.SUPPRESS,  # CI diagnostic only, not a user-facing mode
    )
    args = parser.parse_args()

    if args.check_gui:
        return _check_gui_imports()
    if args.config:
        # Manual/interactive diagnostic, not a Task Scheduler path - nothing
        # to log, so just avoid crashing under console=False (see _say).
        if sys.stdout is not None:
            try:
                print(CONFIG_PATH)
            except Exception:
                pass
        return 0
    if args.run:
        return _headless_run()
    if args.scan:
        return _headless_scan()
    return _gui()


if __name__ == "__main__":
    raise SystemExit(main())
