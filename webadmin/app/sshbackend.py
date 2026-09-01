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
import threading
import time
from pathlib import Path
from typing import Any

import paramiko

from .backends import Backend, Shell
from .config import Host
from .devices import list_devices

CONNECT_TIMEOUT = 12
PROBE_TIMEOUT = 45
KNOWN_HOSTS = Path(__file__).resolve().parent.parent / "known_hosts"


class SshError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Remote probes
# --------------------------------------------------------------------------

_PREAMBLE = r"""
$ErrorActionPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'

function Emit($obj) {
    $json = $obj | ConvertTo-Json -Depth 6 -Compress
    [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
}

function TaskInfo($name) {
    $t = $null; $i = $null
    try {
        $t = Get-ScheduledTask -TaskName $name -ErrorAction Stop
        $i = Get-ScheduledTaskInfo -TaskName $name -ErrorAction Stop
    } catch { return @{ registered = $false } }
    return [ordered]@{
        registered  = $true
        enabled     = ($t.State -ne 'Disabled')
        last_run    = $(if ($i.LastRunTime) { '{0:yyyy-MM-dd HH:mm}' -f $i.LastRunTime })
        last_result = $i.LastTaskResult
        next_run    = $(if ($i.NextRunTime) { '{0:yyyy-MM-dd HH:mm}' -f $i.NextRunTime })
        missed      = $i.NumberOfMissedRuns
    }
}

function LogTail($path, $count) {
    if (-not (Test-Path -LiteralPath $path)) { return @() }
    return @(Get-Content -LiteralPath $path -Tail $count)
}

function FreeBytes($path) {
    try { return [int64](Get-Item -LiteralPath $path).PSDrive.Free } catch { return $null }
}
"""

_SENDER_PROBE = _PREAMBLE + r"""
function BatVal($path, $name) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $pattern = '^\s*set\s+"' + [regex]::Escape($name) + '=(.*)"\s*$'
    $m = Select-String -LiteralPath $path -Pattern $pattern | Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value }
    return $null
}

$bat  = Join-Path '__SCRIPTS__' 'ts_backup.bat'
$work = BatVal $bat 'WORK_DIR'
if (-not $work) { $work = '__WORK__' }
$base = BatVal $bat 'BASE_DIR'

$projects = $null
if ($base -and (Test-Path -LiteralPath $base)) {
    $projects = @(Get-ChildItem -LiteralPath $base -Directory -Force |
                  Where-Object { $_.Name -notlike '.*' }).Count
}

$pendingDir = Join-Path $work 'pending'
$pending = @()
if (Test-Path -LiteralPath $pendingDir) {
    $pending = @(Get-ChildItem -LiteralPath $pendingDir -File -Filter '*.7z')
}

$targets = BatVal $bat 'TARGETS'

Emit ([ordered]@{
    ok     = $true
    script_present = (Test-Path -LiteralPath $bat)
    task   = (TaskInfo '__TASK__')
    sender = [ordered]@{
        base_dir      = $base
        projects      = $projects
        level         = (BatVal $bat 'SEVENZIP_LEVEL')
        dry_run       = ((BatVal $bat 'DRY_RUN') -eq '1')
        targets       = @($(if ($targets) { $targets -split '\s+' } else { @() }))
        free_bytes    = (FreeBytes $work)
        pending_count = $pending.Count
        pending_bytes = [int64](($pending | Measure-Object Length -Sum).Sum)
    }
    log = (LogTail (Join-Path $work 'backup.log') __LOGLINES__)
})
"""

_RECEIVER_PROBE = _PREAMBLE + r"""
function Ps1Val($path, $name) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $pattern = '^\s*\$' + [regex]::Escape($name) + "\s*=\s*'([^']*)'"
    $m = Select-String -LiteralPath $path -Pattern $pattern | Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value }
    return $null
}

function Ps1Num($path, $name) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $pattern = '^\s*\$' + [regex]::Escape($name) + '\s*=\s*(\d+)'
    $m = Select-String -LiteralPath $path -Pattern $pattern | Select-Object -First 1
    if ($m) { return [int]$m.Matches[0].Groups[1].Value }
    return $null
}

$ps1  = Join-Path '__SCRIPTS__' 'ts_receive.ps1'
$work = Ps1Val $ps1 'WorkDir'
if (-not $work) { $work = '__WORK__' }

$watch   = Ps1Val $ps1 'WatchDir'
$archive = Ps1Val $ps1 'ArchiveRoot'
$seven   = Ps1Val $ps1 'SevenZip'
$resetAfter = Ps1Num $ps1 'ResetAfterDays'
$repo = $(if ($watch) { Join-Path $watch 'PycharmProjects' })

$waiting = @()
if ($watch -and (Test-Path -LiteralPath $watch)) {
    $waiting = @(Get-ChildItem -LiteralPath $watch -File -Filter '*.7z')
}

$tags = @(); $gitSize = $null; $last = $null
if ($repo -and (Test-Path -LiteralPath (Join-Path $repo '.git'))) {
    $tags = @(& git -C $repo tag 2>$null | Where-Object { $_.Trim() })
    if ($tags.Count) { $last = @($tags | Sort-Object)[-1] }
    $counts = @(& git -C $repo count-objects -vH 2>$null)
    $line = @($counts | Where-Object { $_ -like 'size-pack:*' })
    if ($line.Count) { $gitSize = ($line[0] -replace '^size-pack:\s*', '') }
}

$snapCount = $null; $resetCount = $null; $dueDays = $null; $dueDate = $null
$stateFile = $(if ($repo) { Join-Path $repo '.ts_state.json' })
if ($stateFile -and (Test-Path -LiteralPath $stateFile)) {
    try {
        $st = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $snapCount  = [int]$st.snapshot_count
        $resetCount = [int]$st.reset_count
        if ($st.last_reset -and $resetAfter) {
            $due = ([datetime]$st.last_reset).AddDays($resetAfter)
            $dueDays = [int][Math]::Ceiling(($due - (Get-Date)).TotalDays)
            $dueDate = '{0:yyyy-MM-dd}' -f $due
        }
    } catch { }
}

$generations = 0
if ($archive -and (Test-Path -LiteralPath $archive)) {
    $generations = @(Get-ChildItem -LiteralPath $archive -Directory).Count
}

Emit ([ordered]@{
    ok = $true
    script_present = (Test-Path -LiteralPath $ps1)
    task = (TaskInfo '__TASK__')
    receiver = [ordered]@{
        watch_dir      = $watch
        repo_dir       = $repo
        waiting_count  = $waiting.Count
        waiting_bytes  = [int64](($waiting | Measure-Object Length -Sum).Sum)
        snapshots      = $tags.Count
        last_snapshot  = $last
        git_size       = $gitSize
        snapshot_count = $snapCount
        reset_count    = $resetCount
        reset_due_days = $dueDays
        reset_due_date = $dueDate
        seven_zip      = $(if ($seven) { Test-Path -LiteralPath $seven } else { $false })
        generations    = $generations
        free_bytes     = (FreeBytes $work)
    }
    log = (LogTail (Join-Path $work 'receive.log') __LOGLINES__)
})
"""

_LOG_ONLY = _PREAMBLE + r"""
Emit ([ordered]@{ ok = $true; log = (LogTail '__LOGPATH__' __LOGLINES__) })
"""

_WHOAMI = _PREAMBLE + r"""
Emit ([ordered]@{
    ok         = $true
    whoami     = $env:USERNAME
    computer   = $env:COMPUTERNAME
    powershell = $PSVersionTable.PSVersion.ToString()
    scripts_present = (Test-Path -LiteralPath '__SCRIPTS__')
})
"""

_ACTION = _PREAMBLE + r"""
$out = & schtasks __ARGS__ 2>&1 | ForEach-Object { "$_" }
Emit ([ordered]@{ ok = ($LASTEXITCODE -eq 0); code = $LASTEXITCODE; output = @($out) })
"""


def _fill(template: str, **values: str) -> str:
    out = template
    for key, value in values.items():
        out = out.replace(f"__{key}__", value)
    return out


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
        gateway.set_missing_host_key_policy(paramiko.AutoAddPolicy())
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
        # Trust on first use, then pinned - the same thing the ssh command
        # does interactively. The file is ours, not ~/.ssh/known_hosts, so
        # this never disturbs the operator's own SSH setup.
        if KNOWN_HOSTS.exists():
            client.load_host_keys(str(KNOWN_HOSTS))
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

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

        if host.path == "tsssh":
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

        client.connect(**kwargs)
        try:
            client.save_host_keys(str(KNOWN_HOSTS))
        except OSError:
            pass  # read-only checkout: pinning is a bonus, not a requirement
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

    def _run_blocking(self, client: paramiko.SSHClient, script: str) -> dict[str, Any]:
        command = f"powershell -NoProfile -NonInteractive -EncodedCommand {_encode_command(script)}"
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

    async def _run(self, host: Host, script: str) -> dict[str, Any]:
        async with self._lock(host.id):
            client = await self._client(host)
            try:
                return await asyncio.to_thread(self._run_blocking, client, script)
            except (paramiko.SSHException, OSError):
                # A dropped transport looks like this. Drop it and let the
                # next refresh reconnect rather than staying broken.
                self._clients.pop(host.id, None)
                self._drop_gateway(host.id)
                raise

    # -------------------------------------------------------------- public

    async def status(self, host: Host, log_lines: int = 12) -> dict[str, Any]:
        template = _SENDER_PROBE if host.role == "sender" else _RECEIVER_PROBE
        script = _fill(
            template,
            SCRIPTS=host.scripts_dir,
            WORK=host.work_dir,
            TASK=host.task,
            LOGLINES=str(log_lines),
        )
        try:
            result = await self._run(host, script)
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
        name = "backup.log" if host.role == "sender" else "receive.log"
        path = f"{host.work_dir}\\{name}"
        script = _fill(_LOG_ONLY, LOGPATH=path, LOGLINES=str(lines))
        result = await self._run(host, script)
        value = result.get("log") or []
        return [value] if isinstance(value, str) else value

    async def action(self, host: Host, action: str) -> dict[str, Any]:
        if not host.task:
            return {"ok": False, "action": action, "output": ["이 호스트에 작업 이름이 없습니다."]}

        if action == "run":
            args = f"/run /tn '{host.task}'"
        elif action in ("enable", "disable"):
            args = f"/change /tn '{host.task}' /{action}"
        else:
            return {"ok": False, "action": action, "output": [f"알 수 없는 동작: {action}"]}

        result = await self._run(host, _fill(_ACTION, ARGS=args))
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
            probe = _fill(_WHOAMI, SCRIPTS=host.scripts_dir)
            info = await asyncio.to_thread(self._run_blocking, client, probe)
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
        async with self._lock(host.id):
            client = await self._client(host)
        channel = await asyncio.to_thread(
            client.invoke_shell, "xterm-256color", cols, rows
        )
        return SshShell(channel, asyncio.get_running_loop())
