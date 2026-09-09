"""The tailnet device list behind the sidebar.

`tailscale status --json` is read on the machine the console runs on. That
machine is a tailnet node by definition - if it were not, it could not reach
anything - so its own view of the tailnet is the right one.

Two things this is careful about:

  The tailscale binary may not be on PATH, or may not be installed at all
  (a workstation used only for development). That is not an error: the
  console falls back to the configured hosts and says so, rather than
  showing an empty sidebar and letting the operator guess why.

  A device on the tailnet is not the same thing as a host this console can
  drive. The two lists are merged, never conflated - a peer with no entry in
  hosts.json shows up with no role and no SSH tag, and clicking it leads to
  the connection form rather than to a terminal.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any

from .config import Host, registry

TAILSCALE_TIMEOUT = 6

ROLE_LABEL = {"sender": "보내는 쪽", "receiver": "받는 쪽"}


def _tailscale_binary() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    # Windows installs it here and does not put it on PATH, exactly like 7-Zip.
    for candidate in (
        r"C:\Program Files\Tailscale\tailscale.exe",
        r"C:\Program Files (x86)\Tailscale\tailscale.exe",
    ):
        if shutil.os.path.exists(candidate):  # noqa: PTH110
            return candidate
    return None


async def _tailscale_status() -> tuple[dict[str, Any] | None, str | None]:
    binary = _tailscale_binary()
    if binary is None:
        return None, "tailscale 명령을 찾지 못했습니다"
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "status", "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), TAILSCALE_TIMEOUT)
    except (OSError, asyncio.TimeoutError) as exc:
        return None, f"tailscale status 실행 실패: {exc}"

    if proc.returncode != 0:
        return None, (err.decode("utf-8", "replace").strip() or "tailscale status 실패")[:200]
    try:
        return json.loads(out.decode("utf-8", "replace")), None
    except ValueError:
        return None, "tailscale status --json 을 해석하지 못했습니다"


def _peer_entry(node: dict[str, Any], is_self: bool = False) -> dict[str, Any]:
    ips = node.get("TailscaleIPs") or []
    return {
        "host": (node.get("HostName") or node.get("DNSName", "").split(".")[0] or "?").lower(),
        "ip": next((ip for ip in ips if ":" not in ip), ips[0] if ips else ""),
        "os": node.get("OS") or "",
        # Self has no Online field; if we are asking, we are online.
        "online": True if is_self else bool(node.get("Online")),
        "self": is_self,
    }


def _match(entry: dict[str, Any], hosts: list[Host]) -> Host | None:
    name = entry["host"]
    ip = entry["ip"]
    for host in hosts:
        addr = host.address.lower()
        if addr == name or addr == ip or addr.split(".")[0] == name:
            return host
    return None


def _decorate(entry: dict[str, Any], host: Host | None) -> dict[str, Any]:
    tags: list[dict[str, str]] = []
    if host and host.role in ROLE_LABEL:
        tags.append({"label": ROLE_LABEL[host.role], "kind": host.role})
    if host:
        tags.append({"label": "SSH", "kind": "ssh"})
    if entry.get("self"):
        tags.append({"label": "이 서버", "kind": "self"})

    entry.update({
        "id": host.id if host else None,
        "label": host.label if host else entry["host"],
        "role": host.role if host else "",
        "configured": host is not None,
        "path": host.path if host else "direct",
        "has_password": host.has_password if host else False,
        # Carried on the row itself so the header and the connection form never
        # have to wait for /api/overview to know who we log in as.
        "username": host.username if host else "",
        "port": host.port if host else 22,
        "task": host.task if host else "",
        "scripts_dir": host.scripts_dir if host else r"C:\Scripts",
        "work_dir": host.work_dir if host else "",
        "tags": tags,
    })
    return entry


async def list_devices() -> dict[str, Any]:
    """Tailnet peers merged with configured hosts.

    Configured hosts that the tailnet does not report are still listed, with
    `online: null`. A host that is genuinely unreachable and a tailnet that
    could not be queried look identical otherwise, and the operator needs to
    be able to tell those apart.
    """
    hosts = registry.all()
    status, error = await _tailscale_status()

    devices: list[dict[str, Any]] = []
    seen: set[str] = set()

    if status:
        if status.get("Self"):
            devices.append(_peer_entry(status["Self"], is_self=True))
        for node in (status.get("Peer") or {}).values():
            devices.append(_peer_entry(node))

        for entry in devices:
            host = _match(entry, hosts)
            _decorate(entry, host)
            if host:
                seen.add(host.id)

    for host in hosts:
        if host.id in seen:
            continue
        devices.append(_decorate(
            {"host": host.address.lower(), "ip": "", "os": "", "online": None, "self": False},
            host,
        ))

    # Configured first, then online, then by name: the machines this console
    # actually drives belong at the top.
    devices.sort(key=lambda d: (not d["configured"], not d["online"], d["host"]))

    return {
        "devices": devices,
        "tailnet_ok": status is not None,
        "error": error,
        "online": sum(1 for d in devices if d["online"]),
        "total": len(devices),
    }
