"""What the console talks to.

Two implementations sit behind one interface:

  MockBackend  - canned data shaped like the real thing. Lets the screen be
                 built and judged without touching anyone's machine, and
                 keeps the UI testable in CI where no tailnet exists.
  SshBackend   - the real one (app/sshbackend.py), wired in step 2.

Everything the console needs from a host goes through this interface, so
swapping them is a one-line change in main.py and nothing in the browser
knows the difference.
"""

from __future__ import annotations

import abc
import asyncio
import time
from typing import Any

from .config import Host


class Shell(abc.ABC):
    """One interactive terminal session."""

    @abc.abstractmethod
    async def read(self) -> bytes | None:
        """Next chunk of output, or None once the session has ended."""

    @abc.abstractmethod
    async def write(self, data: bytes) -> None: ...

    @abc.abstractmethod
    async def resize(self, cols: int, rows: int) -> None: ...

    @abc.abstractmethod
    async def close(self) -> None: ...


class Backend(abc.ABC):
    name: str

    @abc.abstractmethod
    async def status(self, host: Host) -> dict[str, Any]: ...

    @abc.abstractmethod
    async def log(self, host: Host, lines: int) -> list[str]: ...

    @abc.abstractmethod
    async def action(self, host: Host, action: str) -> dict[str, Any]: ...

    @abc.abstractmethod
    async def shell(self, host: Host, cols: int, rows: int) -> Shell: ...

    @abc.abstractmethod
    async def devices(self) -> dict[str, Any]:
        """The tailnet device list behind the sidebar."""


# --------------------------------------------------------------------------
# Mock
# --------------------------------------------------------------------------

_SENDER_LOG = [
    "[2026-08-31 17:52:34.16] === run start ===",
    "[2026-08-31 17:52:35.12] free space on work volume: 363975 MB",
    "[2026-08-31 17:52:35.36] creating archive: C:\\TempBackup\\PycharmProjects_2026_08_31_17_52.7z (LZMA2 -mx=5)",
    "[2026-08-31 17:56:37.35] archive ready, 4741140138 bytes",
    "[2026-08-31 18:07:37.16] sent PycharmProjects_2026_08_31_17_52.7z -> wisenesco-23031302",
    "[2026-08-31 18:07:37.74] sent and removed local archive",
    "[2026-08-31 18:07:37.74] === run end (exit 0) ===",
]

_RECEIVER_LOG = [
    "[2026-09-01 18:47:54] === run start ===",
    "[2026-09-01 18:47:55] running as WORKGROUP\\emergency (profile C:\\Users\\Emergency)",
    "[2026-09-01 18:47:55] watching C:\\Users\\WISENESCO\\Downloads -> C:\\Users\\WISENESCO\\Downloads\\PycharmProjects",
    "[2026-09-01 18:47:55] ingesting PycharmProjects_2026_08_31_17_52.7z",
    "[2026-09-01 18:49:27]     renamed 13 nested .git -> .git_archived",
    "[2026-09-01 18:49:58]     staging 22 projects, this may take a while",
    "[2026-09-01 18:50:49]     committed: snapshot 2026_08_31_17_52",
    "[2026-09-01 18:50:49]     tagged 2026_08_31_17_52",
    "[2026-09-01 18:50:49]     removed zip",
    "[2026-09-01 18:50:49] === run end (exit 0) ===",
]


class MockShell(Shell):
    """A fake cmd.exe. Enough to prove the WebSocket plumbing and xterm.js
    wiring before a real channel is on the other end."""

    BANNER = (
        "Microsoft Windows [Version 10.0.26200.9168]\r\n"
        "(c) Microsoft Corporation. All rights reserved.\r\n"
        "\r\n"
        "\x1b[33m[MOCK] 이 터미널은 흉내입니다. 실제 SSH 세션이 아닙니다.\x1b[0m\r\n"
        "\x1b[33m[MOCK] hosts.json 을 채우고 --ssh 로 실행하면 진짜에 붙습니다.\x1b[0m\r\n"
        "\r\n"
    )

    def __init__(self, host: Host) -> None:
        self.host = host
        self.prompt = f"{host.username}@{host.address.upper()} C:\\Users\\{host.username}>"
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._line = bytearray()
        self._closed = False
        self._emit(self.BANNER + self.prompt)

    def _emit(self, text: str) -> None:
        self._queue.put_nowait(text.encode("utf-8"))

    async def read(self) -> bytes | None:
        return await self._queue.get()

    async def write(self, data: bytes) -> None:
        if self._closed:
            return
        for byte in data:
            ch = bytes([byte])
            if ch in (b"\r", b"\n"):
                self._emit("\r\n")
                self._run(self._line.decode("utf-8", "replace").strip())
                self._line.clear()
            elif ch in (b"\x7f", b"\b"):
                if self._line:
                    self._line.pop()
                    self._emit("\b \b")
            elif ch == b"\x03":  # Ctrl-C
                self._line.clear()
                self._emit("^C\r\n" + self.prompt)
            elif byte >= 0x20:
                self._line += ch
                self._emit(ch.decode("utf-8", "replace"))

    def _run(self, command: str) -> None:
        cmd = command.lower()
        if not cmd:
            pass
        elif cmd in ("exit", "logout"):
            self._emit("\r\n")
            self._closed = True
            self._queue.put_nowait(None)
            return
        elif cmd.startswith("dir"):
            self._emit(
                " C 드라이브의 볼륨에는 이름이 없습니다.\r\n\r\n"
                f" {self.host.scripts_dir} 디렉터리\r\n\r\n"
                "2026-08-31  오후 05:50            12,345 ts_backup.bat\r\n"
                "2026-08-31  오후 05:50             1,024 ts_backup_hidden.vbs\r\n"
                "2026-08-31  오후 05:50             2,048 ts_backup_task.xml\r\n"
                "2026-08-31  오후 05:50            18,000 ts_console.ps1\r\n"
                "               4개 파일              33,417 바이트\r\n\r\n"
            )
        elif "backup.log" in cmd or "receive.log" in cmd:
            log = _SENDER_LOG if self.host.role == "sender" else _RECEIVER_LOG
            self._emit("\r\n".join(log) + "\r\n")
        elif cmd.startswith("schtasks"):
            self._emit("성공: 예약된 작업 실행을 요청했습니다.\r\n")
        else:
            self._emit(
                f"'{command.split()[0]}'은(는) 내부 또는 외부 명령이 아닙니다.  "
                "[MOCK 셸이 아는 명령: dir, type, schtasks, exit]\r\n"
            )
        self._emit(self.prompt)

    async def resize(self, cols: int, rows: int) -> None:
        return None

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._queue.put_nowait(None)


class MockBackend(Backend):
    name = "mock"

    async def status(self, host: Host) -> dict[str, Any]:
        await asyncio.sleep(0.15)  # so the loading state is actually visible
        base = {
            "id": host.id,
            "reachable": True,
            "error": None,
            "checked_at": time.time(),
            "task": {
                "registered": True,
                "enabled": True,
                "last_run": "2026-08-31 18:00",
                "last_result": 0,
                "next_run": "2026-09-01 04:00",
                "missed": 0,
            },
        }
        if host.role == "sender":
            base["sender"] = {
                "base_dir": "C:\\Users\\DiCiA\\PycharmProjects",
                "projects": 22,
                "level": "5",
                "targets": [
                    "wisenesco-23031302",
                    "laptop-7gmpubqc",
                    "desktop-dvj3pqk",
                    "desktop-0g92n63",
                ],
                "free_bytes": 381_891_186_688,
                "pending_count": 0,
                "pending_bytes": 0,
                "dry_run": False,
            }
            base["log"] = _SENDER_LOG
        else:
            base["receiver"] = {
                "watch_dir": "C:\\Users\\WISENESCO\\Downloads",
                "repo_dir": "C:\\Users\\WISENESCO\\Downloads\\PycharmProjects",
                "waiting_count": 0,
                "waiting_bytes": 0,
                "snapshots": 2,
                "last_snapshot": "2026_08_31_17_52",
                "git_size": "4.06 GiB",
                "snapshot_count": 2,
                "reset_count": 0,
                "reset_due_days": 90,
                "reset_due_date": "2026-11-30",
                "seven_zip": True,
                "generations": 0,
                "free_bytes": 162_315_902_976,
            }
            base["log"] = _RECEIVER_LOG
        return base

    async def log(self, host: Host, lines: int) -> list[str]:
        await asyncio.sleep(0.1)
        log = _SENDER_LOG if host.role == "sender" else _RECEIVER_LOG
        return log[-lines:]

    async def action(self, host: Host, action: str) -> dict[str, Any]:
        await asyncio.sleep(0.2)
        return {
            "ok": True,
            "action": action,
            "output": [f"[MOCK] {action} 요청을 보낸 척했습니다: {host.task}"],
        }

    async def shell(self, host: Host, cols: int, rows: int) -> Shell:
        return MockShell(host)

    async def devices(self) -> dict[str, Any]:
        """Shaped exactly like devices.list_devices(), including peers with no
        entry in hosts.json - the sidebar has to look right for those too."""
        await asyncio.sleep(0.1)
        rows = [
            ("desktop-nb8bfur",    "100.101.7.4",  True,  "sender",   "dicia"),
            ("wisenesco-23031302", "100.84.12.31", True,  "receiver", "emergency"),
            ("laptop-7gmpubqc",    "100.72.55.18", True,  None,       ""),
            ("desktop-dvj3pqk",    "100.115.9.62", False, None,       ""),
            ("desktop-0g92n63",    "100.66.240.7", False, None,       ""),
        ]
        label = {"sender": "보내는 쪽", "receiver": "받는 쪽"}
        task = {"sender": "TailscaleProjectBackup", "receiver": "TailscaleProjectReceive"}
        work = {"sender": r"C:\TempBackup", "receiver": r"C:\TempReceive"}
        devices = []
        for host, ip, online, role, user in rows:
            tags = []
            if role:
                tags.append({"label": label[role], "kind": role})
                tags.append({"label": "SSH", "kind": "ssh"})
            devices.append({
                "id": role, "host": host, "ip": ip, "os": "windows", "online": online,
                "self": False, "label": label.get(role, host), "role": role or "",
                "configured": role is not None, "path": "direct",
                "has_password": False, "username": user, "port": 22,
                "task": task.get(role, ""), "scripts_dir": r"C:\Scripts",
                "work_dir": work.get(role, ""), "tags": tags,
            })
        return {
            "devices": devices, "tailnet_ok": True, "error": None,
            "online": sum(1 for d in devices if d["online"]), "total": len(devices),
        }
