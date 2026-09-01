"""Self-test for the web console.

Plain python, no test runner:

    cd webadmin && python -m tests.selftest

Deliberately dependency-free so it also runs on the Windows machines, where
adding pytest to get a green tick is not worth the install.

What it can and cannot cover: there is no SSH server here, so the transport
itself is exercised with a fake paramiko channel. That still catches the parts
most likely to be wrong - the thread-to-event-loop bridge in SshShell, the
base64/JSON contract with PowerShell, and every failure path - none of which
a live connection would test any better.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import sshbackend as sb              # noqa: E402
from app.backends import MockBackend, MockShell  # noqa: E402
from app.config import Host, Registry          # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}   {detail}")
        FAILURES.append(name)


def section(title: str) -> None:
    print(f"\n{title}")


# --------------------------------------------------------------------------


def test_config() -> None:
    section("config")
    reg = Registry()
    hosts = {h.id: h for h in reg.all()}
    check("example registry loads two hosts", len(hosts) == 2, str(list(hosts)))
    check("sender work_dir defaulted", hosts["sender"].work_dir == r"C:\TempBackup")
    check("receiver work_dir defaulted", hosts["receiver"].work_dir == r"C:\TempReceive")

    reg.set_password("sender", "hunter2")
    pub = hosts["sender"].public()
    check("password never serialised", "password" not in pub and pub["has_password"] is True,
          str(pub))
    check("repr hides password", "hunter2" not in repr(hosts["sender"]))

    reg.set_password("sender", "")
    check("empty password clears", hosts["sender"].password is None)
    check("unknown host is rejected", reg.set_password("nope", "x") is False)


def test_mock_backend() -> None:
    section("mock backend")
    backend = MockBackend()
    reg = Registry()
    sender = reg.get("sender")
    receiver = reg.get("receiver")

    s = asyncio.run(backend.status(sender))
    check("sender status has sender block", "sender" in s and "receiver" not in s)
    check("sender status has task", s["task"]["registered"] is True)

    r = asyncio.run(backend.status(receiver))
    check("receiver status has receiver block", "receiver" in r and "sender" not in r)
    check("status is JSON-serialisable", bool(json.dumps(r)))

    lines = asyncio.run(backend.log(sender, 3))
    check("log honours the line count", len(lines) == 3, str(len(lines)))


def test_mock_shell() -> None:
    section("mock shell")

    async def drain(shell) -> tuple[str, bool]:
        """Everything queued right now. The shell emits one frame per
        keystroke, so a fixed read count silently truncates - drain until it
        goes quiet instead. Returns (text, session_ended)."""
        text, ended = "", False
        while True:
            try:
                chunk = await asyncio.wait_for(shell.read(), 0.2)
            except asyncio.TimeoutError:
                return text, ended
            if chunk is None:
                return text, True
            text += chunk.decode()

    async def drive() -> tuple[str, bool, bool]:
        shell = MockShell(Registry().get("sender"))
        banner, _ = await drain(shell)

        for ch in "dir\r":
            await shell.write(ch.encode())
        body, _ = await drain(shell)

        # backspace must erase, not accumulate
        for ch in "ab\x7f":
            await shell.write(ch.encode())
        buf, _ = await drain(shell)
        erased = buf.endswith("\b \b")

        # Ctrl-C first: the backspace above left an "a" on the line, and
        # "aexit" is not "exit".
        await shell.write(b"\x03")
        await drain(shell)

        for ch in "exit\r":
            await shell.write(ch.encode())
        _, ended = await drain(shell)
        return banner + body, erased, ended

    out, erased, ended = asyncio.run(drive())
    check("banner is emitted", "Microsoft Windows" in out)
    check("mock is labelled as mock", "MOCK" in out)
    check("keystrokes echo", "dir" in out)
    check("command produces output", "ts_backup.bat" in out)
    check("backspace erases", erased)
    check("exit ends the session", ended)


def test_probe_templates() -> None:
    section("probe templates")
    filled = {
        "sender": sb._fill(sb._SENDER_PROBE, SCRIPTS=r"C:\Scripts", WORK=r"C:\TempBackup",
                           TASK="TailscaleProjectBackup", LOGLINES="12"),
        "receiver": sb._fill(sb._RECEIVER_PROBE, SCRIPTS=r"C:\Scripts", WORK=r"C:\TempReceive",
                             TASK="TailscaleProjectReceive", LOGLINES="12"),
        "log": sb._fill(sb._LOG_ONLY, LOGPATH=r"C:\TempBackup\backup.log", LOGLINES="40"),
        "action": sb._fill(sb._ACTION, ARGS="/run /tn 'T'"),
    }
    for name, script in filled.items():
        left = re.findall(r"__[A-Z]+__", script)
        check(f"{name}: no unfilled placeholder", not left, str(left))
        check(f"{name}: emits base64", "ToBase64String" in script)
        # 'Stop' would turn git's and schtasks' stderr into terminating errors
        check(f"{name}: preference is not Stop", "'Stop'" not in script)

    original = filled["sender"]
    decoded = base64.b64decode(sb._encode_command(original)).decode("utf-16-le")
    check("EncodedCommand round-trips exactly", decoded == original)


# ------------------------------- fake paramiko ----------------------------


class FakeStd:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeClient:
    """Stands in for paramiko.SSHClient for the exec path."""

    def __init__(self, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.stdout, self.stderr = stdout, stderr
        self.last_command = ""

    def exec_command(self, command, timeout=None):  # noqa: ANN001
        self.last_command = command
        return None, FakeStd(self.stdout), FakeStd(self.stderr)


def b64json(obj: object) -> bytes:
    return base64.b64encode(json.dumps(obj).encode("utf-8"))


def test_probe_parsing() -> None:
    section("probe parsing")
    backend = sb.SshBackend()

    payload = {"ok": True, "task": {"registered": True}, "log": ["a", "b"]}
    client = FakeClient(stdout=b64json(payload))
    got = backend._run_blocking(client, "whatever")
    check("base64 JSON is parsed", got == payload, str(got))
    check("command uses -EncodedCommand", "-EncodedCommand" in client.last_command)
    check("command uses -NoProfile", "-NoProfile" in client.last_command)

    # Korean survives the whole round trip
    korean = {"log": ["[2026-09-01] 압축 해제 완료 · 음원+악보병합 프로젝트"]}
    got = backend._run_blocking(FakeClient(stdout=b64json(korean)), "x")
    check("non-ASCII survives base64/JSON", got == korean, str(got))

    # a PowerShell parse error lands on stdout as plain text
    try:
        backend._run_blocking(FakeClient(stdout=b"At line:1 char:5 + oops"), "x")
        check("garbage stdout raises", False, "no exception")
    except sb.SshError as exc:
        check("garbage stdout raises SshError", "oops" in str(exc), str(exc))

    # nothing on stdout: report what stderr said
    try:
        backend._run_blocking(FakeClient(stdout=b"", stderr="접근이 거부되었습니다".encode()), "x")
        check("empty stdout raises", False, "no exception")
    except sb.SshError as exc:
        check("empty stdout surfaces stderr", "거부" in str(exc), str(exc))


def test_status_shaping() -> None:
    section("status shaping")
    backend = sb.SshBackend()
    host = Registry().get("sender")

    async def with_result(payload: object):
        async def fake_run(h, script):
            if isinstance(payload, Exception):
                raise payload
            return payload
        backend._run = fake_run  # type: ignore[method-assign]
        return await backend.status(host)

    # PowerShell's ConvertTo-Json collapses a one-element array to a scalar
    got = asyncio.run(with_result({"ok": True, "script_present": True, "log": "only line"}))
    check("single log line is re-wrapped", got["log"] == ["only line"], str(got["log"]))
    check("reachable is set", got["reachable"] is True)

    got = asyncio.run(with_result({"ok": True, "script_present": True}))
    check("missing log becomes empty list", got["log"] == [])

    got = asyncio.run(with_result({"ok": True, "script_present": False}))
    check("missing script is reported", "배포" in (got["error"] or ""), str(got.get("error")))

    got = asyncio.run(with_result(sb.SshError("인증 실패")))
    check("failure marks unreachable", got["reachable"] is False)
    check("failure carries the reason", "인증 실패" in got["error"], got["error"])
    check("failure is still JSON-serialisable", bool(json.dumps(got)))


def test_no_password() -> None:
    section("credentials")
    backend = sb.SshBackend()
    host = Host(id="x", label="x", role="sender", address="h", username="u", password=None)
    try:
        backend._connect_blocking(host)
        check("connecting without a password is refused", False, "no exception")
    except sb.SshError as exc:
        check("connecting without a password is refused", "비밀번호" in str(exc), str(exc))


# ------------------------------ fake channel ------------------------------


class FakeChannel:
    """paramiko.Channel stand-in. recv() blocks like the real one, which is
    the whole point: SshShell reads it on a thread and hands chunks to the
    event loop, and that bridge is what this exercises."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []
        self._event = threading.Event()
        self.sent = bytearray()
        self.size: tuple[int, int] | None = None
        self.closed = False

    def feed(self, data: bytes | None) -> None:
        self._chunks.append(data if data is not None else b"")
        self._event.set()

    def recv(self, n: int) -> bytes:
        while True:
            if self._chunks:
                return self._chunks.pop(0)
            if self.closed:
                return b""
            self._event.wait(0.05)
            self._event.clear()

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def resize_pty(self, cols: int, rows: int) -> None:
        self.size = (cols, rows)

    def close(self) -> None:
        self.closed = True
        self._event.set()


def test_ssh_shell_bridge() -> None:
    section("SshShell thread bridge")

    async def drive() -> dict:
        chan = FakeChannel()
        shell = sb.SshShell(chan, asyncio.get_running_loop())

        chan.feed("한글 출력\r\n".encode())
        first = await asyncio.wait_for(shell.read(), 2)

        await shell.write(b"dir\r")
        await shell.resize(120, 40)

        # an empty recv() means the far end hung up
        chan.feed(None)
        end = await asyncio.wait_for(shell.read(), 2)

        await shell.close()
        return {"first": first, "sent": bytes(chan.sent), "size": chan.size,
                "end": end, "closed": chan.closed}

    r = asyncio.run(drive())
    check("output crosses thread to loop", r["first"] == "한글 출력\r\n".encode(), str(r["first"]))
    check("keystrokes reach the channel", r["sent"] == b"dir\r", str(r["sent"]))
    check("resize reaches the channel", r["size"] == (120, 40), str(r["size"]))
    check("hangup yields None", r["end"] is None, str(r["end"]))
    check("close closes the channel", r["closed"] is True)

    # no thread must outlive the session
    time.sleep(0.2)
    leaked = [t for t in threading.enumerate() if t.name.startswith("Thread-") and t.is_alive()]
    check("reader thread does not leak", len(leaked) == 0, f"{len(leaked)} alive")


def test_action_shaping() -> None:
    section("actions")
    backend = sb.SshBackend()
    host = Registry().get("sender")

    async def run(payload):
        async def fake_run(h, script):
            fake_run.script = script
            return payload
        backend._run = fake_run  # type: ignore[method-assign]
        result = await backend.action(host, "run")
        return result, fake_run.script

    result, script = asyncio.run(run({"ok": True, "code": 0, "output": "성공"}))
    check("scalar output is re-wrapped", result["output"] == ["성공"], str(result["output"]))
    check("task name reaches the command", "TailscaleProjectBackup" in script)
    check("ok is passed through", result["ok"] is True)

    async def bad():
        return await backend.action(host, "rm -rf")
    check("unknown action is refused", asyncio.run(bad())["ok"] is False)


# --------------------------------------------------------------------------

if __name__ == "__main__":
    test_config()
    test_mock_backend()
    test_mock_shell()
    test_probe_templates()
    test_probe_parsing()
    test_status_shaping()
    test_no_password()
    test_ssh_shell_bridge()
    test_action_shaping()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)}")
        for name in FAILURES:
            print(f"  - {name}")
        sys.exit(1)
    print("all checks passed")
