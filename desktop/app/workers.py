"""Small background jobs the screens need without freezing the window."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal


def dir_size(path: Path) -> int:
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


class FolderStats(QObject):
    """Sub-folder count and total size of a folder, counted on a thread -
    a tree of tens of thousands of files takes seconds to walk."""

    done = Signal(str, int, int)   # folder, sub-folders, bytes

    def start(self, folder: str) -> None:
        threading.Thread(target=self._run, args=(folder,), daemon=True).start()

    def _run(self, folder: str) -> None:
        path = Path(folder)
        if not folder or not path.is_dir():
            self.done.emit(folder, 0, 0)
            return
        try:
            dirs = sum(1 for p in path.iterdir() if p.is_dir())
        except OSError:
            dirs = 0
        self.done.emit(folder, dirs, dir_size(path))
