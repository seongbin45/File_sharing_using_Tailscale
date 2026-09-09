"""The real backend: SSH to each machine over the tailnet.

Two decisions here are worth reading before changing anything.

**Probes are one round trip, not twenty.** Each status refresh sends a single
PowerShell script and gets one JSON document back. Issuing a command per field
would mean dozens of round trips every ten seconds against a machine that is
often busy shipping several gigabytes.

**Everything comes back base64-encoded.** The script builds UTF-8 JSON and
hands over its base64. Nothing then depends on the remote console code page,
on what PowerShell picks for stdout in a redirected session, or on the log
file's own encoding. This project has been bitten by every one of those
(see docs/VERIFICATION.md), and base64 ends the entire category.

The script itself goes out with -EncodedCommand for the same reason in the
other direction: UTF-16LE base64 sidesteps every layer of cmd, SSH and
PowerShell quoting, so a script containing quotes, braces and backslashes
arrives exactly as written.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import paramiko

from . import hostkeys
from .backends import Backend, Shell
from .config import Host, keypass_env_name
from .devices import list_devices

CONNECT_TIMEOUT = 12
PROBE_TIMEOUT = 45
KNOWN_HOSTS = Path(__file__).resolve().parent.parent / "known_hosts"

# Set when tailscaled runs in userspace mode, which is how Tailscale works
# inside a container that cannot create a TUN device (every managed PaaS).
# In that mode there is no network interface to route through: tailscaled
# exposes the tailnet as a SOCKS5 proxy instead, and every connection has to
# be dialled through it. Empty on a normal host, where the tailnet is just
# the network and nothing special is needed.
SOCKS5 = os.environ.get("TSCONSOLE_SOCKS5", "").strip()


def _socks_socket(address: str, port: int):
    """A socket to (address, port) dialled through the SOCKS5 proxy.

    The name is resolved by the proxy, not here: MagicDNS names only mean
    something inside the tailnet, so resolving locally first would fail on
    every one of them.
    """
    try:
        import socks  # PySocks
    except ImportError as exc:
        raise SshError(
            "TSCONSOLE_SOCKS5 가 설정됐지만 PySocks 가 없습니다. "
            "pip install -r requirements.txt 로 설치하십시오."
        ) from exc

    host, _, raw_port = SOCKS5.rpartition(":")
    if not host:
        raise SshError(f"TSCONSOLE_SOCKS5 형식이 잘못됐습니다: {SOCKS5!r} (host:port)")
    sock = socks.socksocket()
    sock.set_proxy(socks.SOCKS5, host, int(raw_port), rdns=True)
    sock.settimeout(CONNECT_TIMEOUT)
    sock.connect((address, port))
    return sock


class SshError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# The guard, and the verbs it accepts
# --------------------------------------------------------------------------
#
# All remote work goes through scripts/ts_guard.ps1, in one of two ways:
#
#   restricted   The key on the far end is pinned with command="...ts_guard.ps1"
#                so sshd runs it whatever we ask for. We send only the verb.
#                A stolen console cannot open a shell on that machine.
#
#   inline       Nothing is installed there. We send the guard's own body
#                followed by a call into it, as -EncodedCommand.
#
# The point of sending the same file inline is that there is exactly ONE copy
# of this PowerShell. Keeping a second, subtly different set of probes in this
# module is how the restricted and unrestricted paths would quietly drift into
# behaving differently, which is the worst possible outcome for a security
# boundary.

# Same grammar the guard enforces. Checked here too so a bug in the console
# cannot even attempt to send something outside it.
VERB = re.compile(r"^(?:whoami|status|log \d{1,4}|task (?:run|enable|disable))$")

_GUARD_NAMES = ("ts_guard.ps1",)
_GUARD_DIRS = (
    Path(__file__).resolve().parent,                       # shipped beside the app (Docker)
    Path(__file__).resolve().parent.parent.parent / "scripts",   # the repo
)
_guard_body: str | None = None


def guard_source() -> str:
    """The guard script's text, read once."""
    global _guard_body
    if _guard_body is not None:
        return _guard_body
    for directory in _GUARD_DIRS:
        for name in _GUARD_NAMES:
            candidate = directory / name
            if candidate.exists():
                # utf-8-sig: the file carries a BOM so Windows PowerShell
                # reads its Korean correctly, and the BOM must not survive
                # into the middle of a script we concatenate.
                _guard_body = candidate.read_text(encoding="utf-8-sig")
                return _guard_body
    raise SshError(
        "ts_guard.ps1 을 찾지 못했습니다. "
        f"찾은 위치: {', '.join(str(d) for d in _GUARD_DIRS)}"
    )


def inline_script(verb: str) -> str:
    """The guard body plus a call into it, for hosts with nothing installed.

    The guard only self-invokes when run as a file ($PSCommandPath is set),
    so sending the body is inert until this last line calls it.
    """
    return f"{guard_source()}\nInvoke-Guard -Command '{verb}'\n"


def _encode_command(script: str) -> str:
    """PowerShell -EncodedCommand takes UTF-16LE base64."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


# --------------------------------------------------------------------------
# Interactive shell
# --------------------------------------------------------------------------


class SshShell(Shell):
    def __init__(self, channel: paramiko.Channel, loop: asyncio.AbstractEventLoop) -> None:
        self._chan = channel
        self._loop = loop
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._closed = False
        # paramiko is blocking, so the read side lives in its own thread and
        # hands chunks to the event loop.
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        try:
            while True:
                data = self._chan.recv(32768)
                if not data:
                    break
                self._loop.call_soon_threadsafe(self._queue.put_nowait, data)
        except Exception:
            pass
        finally:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)

    async def read(self) -> bytes | None:
        return await self._queue.get()

    async def write(self, data: bytes) -> None:
        if not self._closed:
            await asyncio.to_thread(self._chan.sendall, data)

    async def resize(self, cols: int, rows: int) -> None:
        if not self._closed:
            await asyncio.to_thread(self._chan.resize_pty, cols, rows)

    async def close(self) -> None:
        self._closed = True
        await asyncio.to_thread(self._chan.close)


# --------------------------------------------------------------------------
# Backend
# --------------------------------------------------------------------------


class SshBackend(Backend):
    name = "ssh"

    def __init__(self) -> None:
        self._clients: dict[str, paramiko.SSHClient] = {}
        self._gateways: dict[str, paramiko.SSHClient] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # ---------------------------------------------------------- connection

    def _lock(self, host_id: str) -> asyncio.Lock:
        return self._locks.setdefault(host_id, asyncio.Lock())

    def _jump_socket(self, host: Host):
        """Open a channel through the jump host and hand it back as the socket
        for the real connection.

        This is what "relay" means in practice: an ordinary SSH ProxyJump.
        No third-party service is involved, so the credentials only ever pass
        between this console and machines the operator owns - which is the
        whole reason Tailscale was chosen for the transfer side too.
        """
        jump = host.jump
        if jump is None or not jump.address:
            raise SshError("점프 호스트가 설정되지 않았습니다.")
        if not jump.password:
            raise SshError(f"점프 호스트({jump.address})의 비밀번호가 없습니다.")

        gateway = paramiko.SSHClient()
        if KNOWN_HOSTS.exists():
            gateway.load_host_keys(str(KNOWN_HOSTS))
        # The jump host sees the target's credentials pass through it, so it
        # is verified on exactly the same terms as the target.
        gateway.set_missing_host_key_policy(
            hostkeys.Pinned(getattr(jump, "host_key", ""), f"점프 호스트 {jump.address}")
        )
        gateway.connect(
            hostname=jump.address,
            port=jump.port,
            username=jump.username,
            password=jump.password,
            timeout=CONNECT_TIMEOUT,
            look_for_keys=False,
            allow_agent=False,
        )
        transport = gateway.get_transport()
        if transport is None:
            raise SshError("점프 호스트 연결이 성립되지 않았습니다.")
        self._gateways[host.id] = gateway   # keep it alive for the session
        return transport.open_channel(
            "direct-tcpip", (host.address, host.port), ("127.0.0.1", 0)
        )

    def _connect_blocking(self, host: Host) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        # known_hosts is a cache, not the authority: on a wiped filesystem it
        # is empty and every host looks new. host.host_key is the authority,
        # because it is deployed with the configuration.
        if KNOWN_HOSTS.exists():
            client.load_host_keys(str(KNOWN_HOSTS))
        policy = hostkeys.Pinned(host.host_key, f"{host.label} ({host.address})")
        client.set_missing_host_key_policy(policy)

        kwargs: dict[str, Any] = {
            "hostname": host.address,
            "port": host.port,
            "username": host.username,
            "timeout": CONNECT_TIMEOUT,
            "banner_timeout": CONNECT_TIMEOUT,
            "auth_timeout": CONNECT_TIMEOUT,
            "look_for_keys": False,   # password auth only; never silently use a key
            "allow_agent": False,
        }

        if host.key_file:
            # A key is not replayable and is revoked by deleting one line
            # from authorized_keys on the machine itself - no password to
            # steal from an environment variable, and nothing to ask anyone
            # else for in order to cut access off.
            kwargs["pkey"] = hostkeys.load_private_key(
                host.key_file, os.environ.get(keypass_env_name(host.id))
            )
            kwargs["password"] = None
        elif host.path == "tsssh":
            # Tailscale SSH: tailscaled terminates the connection and
            # authorises by tailnet identity, so there is no password to
            # offer. paramiko still has to try something, and "none" auth is
            # what the tailscale client itself ends up doing.
            kwargs["password"] = None
        else:
            if not host.password:
                raise SshError("비밀번호가 설정되지 않았습니다. 연결 설정에서 입력하십시오.")
            kwargs["password"] = host.password

        if host.path == "jump":
            kwargs["sock"] = self._jump_socket(host)
        elif SOCKS5:
            kwargs["sock"] = _socks_socket(host.address, host.port)

        try:
            client.connect(**kwargs)
        except paramiko.BadHostKeyException as exc:
            # Already known and now different. paramiko raises before the
            # policy is consulted, so translate it into the same message.
            raise hostkeys.HostKeyError(
                f"{host.label} 의 호스트 키가 이전에 본 값과 다릅니다.\n"
                f"  이전: {hostkeys.fingerprint(exc.expected_key)}\n"
                f"  지금: {hostkeys.fingerprint(exc.key)}\n"
                "연결을 중단했습니다."
            ) from exc

        try:
            client.save_host_keys(str(KNOWN_HOSTS))
        except OSError:
            pass  # read-only checkout: the cache is a convenience
        return client

    async def _client(self, host: Host) -> paramiko.SSHClient:
        existing = self._clients.get(host.id)
        if existing is not None:
            transport = existing.get_transport()
            if transport is not None and transport.is_active():
                return existing
            self._clients.pop(host.id, None)
            self._drop_gateway(host.id)

        client = await asyncio.to_thread(self._connect_blocking, host)
        self._clients[host.id] = client
        return client

    # ------------------------------------------------------------- execute

    def _run_blocking(self, client: paramiko.SSHClient, command: str) -> dict[str, Any]:
        _, stdout, stderr = client.exec_command(command, timeout=PROBE_TIMEOUT)
        out = stdout.read().strip()
        err = stderr.read()

        if not out:
            detail = err.decode("utf-8", "replace").strip() or "원격에서 아무 출력도 없었습니다"
            raise SshError(detail[:400])
        try:
            return json.loads(base64.b64decode(out).decode("utf-8"))
        except (binascii.Error, ValueError, UnicodeDecodeError):
            # Not our base64 - almost always a PowerShell parse error printed
            # to stdout, so show it rather than a decoding complaint.
            raise SshError(out.decode("utf-8", "replace")[:400]) from None

    def _command_for(self, host: Host, verb: str) -> str:
        """What actually goes over the wire for this verb."""
        if not VERB.match(verb):
            # Unreachable unless this module has a bug; a security boundary
            # should still refuse rather than trust its own callers.
            raise SshError(f"허용되지 않은 동작입니다: {verb!r}")
        if host.restricted:
            # sshd replaces this with the forced command from authorized_keys
            # and hands the text to the guard in SSH_ORIGINAL_COMMAND.
            return verb
        return ("powershell -NoProfile -NonInteractive -EncodedCommand "
                + _encode_command(inline_script(verb)))

    async def _run(self, host: Host, verb: str) -> dict[str, Any]:
        command = self._command_for(host, verb)
        async with self._lock(host.id):
            client = await self._client(host)
            try:
                result = await asyncio.to_thread(self._run_blocking, client, command)
            except (paramiko.SSHException, OSError):
                # A dropped transport looks like this. Drop it and let the
                # next refresh reconnect rather than staying broken.
                self._clients.pop(host.id, None)
                self._drop_gateway(host.id)
                raise

        if result.get("denied"):
            # The guard refused. Surface its reason rather than a decoding
            # complaint about the shape of what came back.
            raise SshError(result.get("reason") or "원격에서 거부했습니다.")
        return result

    # -------------------------------------------------------------- public

    async def status(self, host: Host, log_lines: int = 12) -> dict[str, Any]:
        try:
            result = await self._run(host, "status")
        except Exception as exc:
            return {
                "id": host.id,
                "reachable": False,
                "error": f"{type(exc).__name__}: {exc}",
                "task": None,
                "log": [],
            }

        result["id"] = host.id
        result["reachable"] = True
        result["error"] = None
        if not result.get("script_present", True):
            result["error"] = (
                f"{host.scripts_dir} 에 스크립트가 없습니다. 아직 배포되지 않았습니다."
            )
        # ConvertTo-Json collapses a one-element array to a bare value.
        for key in ("log",):
            value = result.get(key)
            if isinstance(value, str):
                result[key] = [value]
            elif value is None:
                result[key] = []
        return result

    async def log(self, host: Host, lines: int) -> list[str]:
        # The guard picks the file from which side it is installed on. The
        # console never names a path, so there is no path to point elsewhere.
        result = await self._run(host, f"log {max(1, min(lines, 500))}")
        value = result.get("log") or []
        return [value] if isinstance(value, str) else value

    async def action(self, host: Host, action: str) -> dict[str, Any]:
        if not host.task:
            return {"ok": False, "action": action, "output": ["이 호스트에 작업 이름이 없습니다."]}

        if action not in ("run", "enable", "disable"):
            return {"ok": False, "action": action, "output": [f"알 수 없는 동작: {action}"]}

        # No task name crosses the wire: the guard holds it. A caller that
        # could name the task could start any scheduled task on that machine.
        result = await self._run(host, f"task {action}")
        output = result.get("output") or []
        return {
            "ok": bool(result.get("ok")),
            "action": action,
            "code": result.get("code"),
            "output": [output] if isinstance(output, str) else output,
        }

    def _drop_gateway(self, host_id: str) -> None:
        gateway = self._gateways.pop(host_id, None)
        if gateway is not None:
            try:
                gateway.close()
            except Exception:
                pass

    async def test(self, host: Host) -> dict[str, Any]:
        """Connect, ask the far end who it thinks it is, and disconnect.

        Deliberately does not reuse the pooled client: the point is to prove
        that a *fresh* connection works with the settings just entered, which
        an already-open session would hide.
        """
        started = time.monotonic()
        self._clients.pop(host.id, None)
        self._drop_gateway(host.id)
        try:
            client = await asyncio.to_thread(self._connect_blocking, host)
        except Exception as exc:
            self._drop_gateway(host.id)
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

        try:
            info = await asyncio.to_thread(
                self._run_blocking, client, self._command_for(host, "whoami")
            )
            elapsed = int((time.monotonic() - started) * 1000)
            return {
                "ok": True,
                "elapsed_ms": elapsed,
                "whoami": info.get("whoami"),
                "computer": info.get("computer"),
                "powershell": info.get("powershell"),
                "scripts_present": info.get("scripts_present"),
                "detail": f"{info.get('computer')}\\{info.get('whoami')} · {elapsed} ms",
            }
        except Exception as exc:
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        finally:
            try:
                client.close()
            except Exception:
                pass
            self._drop_gateway(host.id)

    async def devices(self) -> dict[str, Any]:
        return await list_devices()

    async def shell(self, host: Host, cols: int, rows: int) -> Shell:
        if host.restricted:
            # sshd would refuse anyway - the forced command runs instead of a
            # shell, and no-pty blocks the pty - but failing here says why,
            # instead of leaving a terminal open on a guard denial.
            raise SshError(
                f"{host.label} 은(는) 제한된 키로 연결되어 셸을 열 수 없습니다. "
                "이것이 이 키를 쓰는 이유입니다. 터미널이 필요하면 별도의 "
                "무제한 키를 등록하십시오 (docs/RESTRICTED_KEY.md)."
            )
        async with self._lock(host.id):
            client = await self._client(host)
        channel = await asyncio.to_thread(
            client.invoke_shell, "xterm-256color", cols, rows
        )
        return SshShell(channel, asyncio.get_running_loop())
