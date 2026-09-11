"""Logging: a rotating file plus an in-memory ring the UI can show.

The file is the record that survives a restart; the ring is what the log
screen renders without re-reading the file every refresh. Both get the same
lines, so what the operator sees on screen is exactly what was written.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

_LOG_MAX_BYTES = 5 * 1024 * 1024
_RING = 500


class Log:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._ring: deque[str] = deque(maxlen=_RING)
        # UI subscribes here; called with each new line, on whatever thread
        # logged it, so the slot must marshal to the GUI thread itself.
        self._subscribers: list[Callable[[str], None]] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def subscribe(self, fn: Callable[[str], None]) -> None:
        self._subscribers.append(fn)

    def line(self, message: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        text = f"[{stamp}] {message}"
        with self._lock:
            self._rotate()
            try:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(text + "\n")
            except OSError:
                pass  # a full or read-only disk must not crash the app
            self._ring.append(text)
        for fn in list(self._subscribers):
            try:
                fn(text)
            except Exception:
                pass

    def tail(self, count: int = _RING) -> list[str]:
        with self._lock:
            return list(self._ring)[-count:]

    def _rotate(self) -> None:
        try:
            if self.path.exists() and self.path.stat().st_size > _LOG_MAX_BYTES:
                backup = self.path.with_suffix(self.path.suffix + ".1")
                backup.unlink(missing_ok=True)
                self.path.rename(backup)
        except OSError:
            pass
