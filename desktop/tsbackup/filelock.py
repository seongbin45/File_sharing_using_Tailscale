"""Non-blocking exclusive lock on a file, released by the OS if the holder
dies. Kept free of Qt so the headless --run path can use it too; the GUI's
single-instance guard (app/single_instance.py) has its own copy."""

from __future__ import annotations

import sys
from pathlib import Path


def try_lock(path: Path):
    """Return an open handle holding the lock (close it to release), or
    None if another process holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+b")  # noqa: SIM115 - the caller owns its lifetime
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh
