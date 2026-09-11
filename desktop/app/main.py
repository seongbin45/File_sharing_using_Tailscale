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
from pathlib import Path

from tsbackup import engine_core
from tsbackup.config import CONFIG_PATH, AppConfig, config_dir
from tsbackup.log import Log
from tsbackup.receiver import Receiver


def _log() -> Log:
    return Log(config_dir() / "tsbackup.log")


def _headless_run() -> int:
    cfg = AppConfig.load()
    log = _log()
    problems = cfg.problems()
    if problems:
        for p in problems:
            print("설정 필요:", p)
        return 2
    result = engine_core.run_sender_once(cfg, log.line)
    return 0 if result.ok else 1


def _headless_scan() -> int:
    cfg = AppConfig.load()
    log = _log()
    receiver = Receiver(cfg, log.line)
    if cfg.receiver_uses_http():
        print("http 수신은 GUI 상주가 필요합니다. --scan 은 폴더 방식만 처리합니다.")
    count = receiver.scan_once()
    print(f"{count}개 처리")
    return 0


def _gui() -> int:
    # Imported here so the headless modes do not require Qt to be present.
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from .main_window import MainWindow
    from .tray import Tray

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
    args = parser.parse_args()

    if args.config:
        print(CONFIG_PATH)
        return 0
    if args.run:
        return _headless_run()
    if args.scan:
        return _headless_scan()
    return _gui()


if __name__ == "__main__":
    raise SystemExit(main())
