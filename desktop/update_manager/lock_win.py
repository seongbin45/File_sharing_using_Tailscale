"""Non-blocking exclusive file lock (OS handle; released on process death).

Ported as-is from CloneUp - generic, no CloneUp-specific assumptions."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger("tsbackup_update_manager")


class PendingLock:
    """Exclusive lock on ``download.lock``. Context manager / manual acquire."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh = None
        self._fd = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            return self._acquire_win()
        return self._acquire_posix()

    def release(self) -> None:
        try:
            if self._fh is not None:
                self._fh.close()
        except OSError:
            pass
        self._fh = None
        self._fd = None

    def __enter__(self) -> PendingLock:
        if not self.acquire():
            raise RuntimeError("pending_busy")
        return self

    def __exit__(self, *exc) -> None:
        self.release()

    def _acquire_win(self) -> bool:
        import msvcrt

        try:
            if not self.path.exists():
                self.path.write_bytes(b"0")
            self._fh = open(self.path, "r+b")  # noqa: SIM115
            self._fd = self._fh.fileno()
            msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError as e:
            log.info("pending lock busy: %s", e)
            self.release()
            return False

    def _acquire_posix(self) -> bool:
        import fcntl

        try:
            self._fh = open(self.path, "a+b")  # noqa: SIM115
            self._fd = self._fh.fileno()
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError as e:
            log.info("pending lock busy: %s", e)
            self.release()
            return False
