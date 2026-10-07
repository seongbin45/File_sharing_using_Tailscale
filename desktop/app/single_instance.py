"""One GUI instance per user.

Autostart puts TsBackup in the tray at login, so the next double-click on the
shortcut would otherwise start a second engine: two timers compressing the
same folder, or two receivers unpacking the same archive. A second launch
instead asks the running one to show its window, then exits - the same
handoff rustdesk does for its tray/main process.

The claim is an OS file lock, not the local socket alone: two named-pipe
servers can share a name on Windows, and a lock is atomic and is released by
the OS when the holder dies, so a crash never leaves a stale claim behind.
The socket only carries the "show yourself" request.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Callable

from PySide6.QtNetwork import QLocalServer, QLocalSocket

_SHOW = b"show"


class InstanceGuard:
    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        # Per lock file, so each Windows user (separate %LOCALAPPDATA%) gets
        # its own instance; pipe names are machine-wide.
        digest = hashlib.sha1(str(lock_path).lower().encode("utf-8")).hexdigest()[:16]
        self.server_name = f"TsBackup-{digest}"
        self._fh = None
        self._server: QLocalServer | None = None

    def claim(self, on_show: Callable[[], None]) -> bool:
        """True if this process is now the one instance. False means another
        instance is running and has been asked to show itself."""
        if not self._lock():
            self._ask_running_to_show()
            return False
        QLocalServer.removeServer(self.server_name)
        server = QLocalServer()
        server.newConnection.connect(lambda: self._on_connection(on_show))
        server.listen(self.server_name)
        self._server = server
        return True

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

    def _lock(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fh = open(self.lock_path, "a+b")  # noqa: SIM115 - held for the process lifetime
        except OSError:
            return True  # can't even open it: don't refuse to start over that
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
            return False
        self._fh = fh
        return True

    def _ask_running_to_show(self) -> None:
        sock = QLocalSocket()
        sock.connectToServer(self.server_name)
        if sock.waitForConnected(1000):
            sock.write(_SHOW)
            sock.waitForBytesWritten(1000)
            sock.disconnectFromServer()

    def _on_connection(self, on_show: Callable[[], None]) -> None:
        server = self._server
        if server is None:
            return
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            if not conn.bytesAvailable():
                conn.waitForReadyRead(1000)
            if conn.readAll().data().startswith(_SHOW):
                on_show()
            conn.disconnectFromServer()
            conn.deleteLater()
