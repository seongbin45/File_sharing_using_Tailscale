"""
Naru_update_manager — silent loop.

  python -m update_manager
  Naru_update_manager.exe [--once] [--interval 600]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from update_manager import __version__
from update_manager.apply import install_staged_exe
from update_manager.config import INTERVAL_SEC
from update_manager.github_release import fetch_latest_release
from update_manager.lock_win import PendingLock
from update_manager.logutil import setup_logging
from update_manager.paths import find_tsbackup_install_dir
from update_manager.pending import (
    delete_version_dir,
    ensure_exe,
    exe_path,
    pending_version_dir,
    prune_other_versions,
    verify_exe_full,
)
from update_manager.process_win import (
    is_tray_autostart_registered,
    kill_tsbackup_processes,
    main_window_visible,
    restart_tsbackup_tray,
)
from update_manager import status_io
from update_manager.versioning import (
    is_newer,
    read_installed_version,
    version_tuple_to_str,
)


def _acquire_mutex():
    """Single instance via named mutex. Returns handle or None if already running."""
    if sys.platform != "win32":
        return object()
    import ctypes

    kernel32 = ctypes.windll.kernel32
    name = "Global\\NaruUpdateManagerMutex"
    handle = kernel32.CreateMutexW(None, False, name)
    last = kernel32.GetLastError()
    if last == 183:
        if handle:
            kernel32.CloseHandle(handle)
        return None
    return handle


def run_once(log: logging.Logger) -> str:
    """
    One update tick.

    Returns: no_install | no_version | no_release | up_to_date | deferred_ui
             | killed_failed | updated | error | pending_busy
    """
    install_dir = find_tsbackup_install_dir()
    if install_dir is None:
        log.info("Naru install dir not found - skip")
        return "no_install"

    run_id = status_io.start_run(install_dir, pid=os.getpid())
    try:
        local = read_installed_version(install_dir)
        if local is None:
            log.warning("cannot read installed version under %s - skip", install_dir)
            status_io.finish_run(install_dir, run_id, "no_version")
            return "no_version"

        release = fetch_latest_release()
        if release is None:
            log.info("no usable release / network - skip")
            status_io.finish_run(install_dir, run_id, "no_release")
            return "no_release"

        local_s = version_tuple_to_str(local)
        remote_s = version_tuple_to_str(release.version)
        status_io.update_run(install_dir, run_id, local=local_s, remote=remote_s)

        if not is_newer(release.version, local):
            log.info("up to date local=%s remote=%s", local_s, remote_s)
            prune_other_versions(install_dir, remote_s)
            status_io.finish_run(install_dir, run_id, "up_to_date")
            return "up_to_date"

        log.info("update available %s -> %s (%s)", local_s, remote_s, release.asset_name)

        pend = pending_version_dir(install_dir, remote_s)
        lock = PendingLock(pend / "download.lock")
        if not lock.acquire():
            status_io.finish_run(install_dir, run_id, "pending_busy")
            return "pending_busy"

        try:
            prune_other_versions(install_dir, remote_s)
            status_io.update_run(install_dir, run_id, phase="downloading")
            ensure_exe(pend, release)

            if main_window_visible():
                log.info("main window visible - defer apply (exe retained in pending)")
                status_io.finish_run(install_dir, run_id, "deferred_ui")
                return "deferred_ui"

            # Apply gate - always re-hash
            verify_exe_full(pend, release)

            if main_window_visible():
                log.info("main window opened during verify - defer apply")
                status_io.finish_run(install_dir, run_id, "deferred_ui")
                return "deferred_ui"

            if not kill_tsbackup_processes():
                log.error("could not stop Naru.exe - abort update (files intact)")
                status_io.finish_run(install_dir, run_id, "killed_failed")
                return "killed_failed"

            install_staged_exe(exe_path(pend, release.asset_name), install_dir)
            # Release the lock before deleting its own directory - on
            # Windows, shutil.rmtree can't remove download.lock while
            # PendingLock still holds an open handle on it (msvcrt.locking
            # doesn't set FILE_SHARE_DELETE), which raised a real
            # [WinError 32] here (caught and logged, but the pending dir
            # was then never actually cleaned up). release() is safe to
            # call twice - the finally block below still runs.
            lock.release()
            delete_version_dir(pend)

            if is_tray_autostart_registered():
                restart_tsbackup_tray(install_dir)

            log.info("success %s -> %s", local_s, remote_s)
            status_io.finish_run(install_dir, run_id, "updated")
            return "updated"
        finally:
            lock.release()
    except Exception as e:
        log.exception("apply failed: %s", e)
        status_io.finish_run(install_dir, run_id, "error", error=str(e))
        return "error"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument(
        "--once", action="store_true", help="Run a single check then exit (for tests)"
    )
    parser.add_argument(
        "--interval", type=int, default=INTERVAL_SEC,
        help=f"Seconds between checks (default {INTERVAL_SEC})",
    )
    args = parser.parse_args(argv)

    log = setup_logging()
    log.info("Naru Update Manager %s starting", __version__)

    mutex = _acquire_mutex()
    if mutex is None:
        # Daemon already holds the Global mutex. A manual "check now" tick
        # (tray -> --once) still gets to run once here - PendingLock
        # serializes the actual download, per-run status preserves
        # identity. Do not become a second long-runner.
        log.info("daemon mutex held - one-shot tick")
        run_once(log)
        return 0

    try:
        if args.once:
            run_once(log)
            return 0
        time.sleep(min(30, max(5, args.interval // 20)))
        while True:
            try:
                run_once(log)
            except Exception:
                log.exception("tick crashed")
            time.sleep(max(60, int(args.interval)))
    finally:
        if sys.platform == "win32" and mutex is not None:
            try:
                import ctypes

                ctypes.windll.kernel32.CloseHandle(mutex)
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
