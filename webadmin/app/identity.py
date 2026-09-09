"""Who is on the other end of this request.

The console already runs on a tailnet node, and tailscaled already knows -
cryptographically, from the WireGuard session - which tailnet user every
connection belongs to. Asking it is better than any login this project could
build:

  * nothing to store. No password hashes, no reset flow, no rotation.
  * revocation is instant and happens where it already happens. Remove the
    device or the user in the Tailscale admin console and they are gone from
    here too, with no second list to remember to update.
  * it cannot be phished or replayed. The identity comes from the tunnel, not
    from anything the browser sends.

Two ways to get it, in preference order:

  whois    Ask tailscaled who owns the peer address of this TCP connection.
           Grounded in the connection itself, so nothing the client sends can
           influence it. Works when the console binds to the tailnet address.

  headers  `tailscale serve` terminates the connection and injects
           Tailscale-User-Login. Convenient, and it is what Funnel/serve
           setups use - but it is only as trustworthy as the guarantee that
           nothing except tailscaled can reach the port. See TRUST_HEADERS.

Anything this module returns is an assertion by tailscaled. What that
identity is allowed to DO is a separate question, answered in access.py.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from typing import Any

WHOIS_TIMEOUT = 5
CACHE_SECONDS = 60

# Set by main.py once the bind address is known. Headers are believed ONLY
# when the console listens on loopback, because then the only thing that can
# reach it is something already on this machine - i.e. tailscaled itself.
#
# Believing them on a public bind would be the whole authentication system
# undone by one curl -H "Tailscale-User-Login: ...". That is not a
# hypothetical: it is the standard way identity-header proxies get bypassed.
TRUST_HEADERS = False

HEADER_LOGIN = "tailscale-user-login"
HEADER_NAME = "tailscale-user-name"

_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


def _binary() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    for candidate in (
        r"C:\Program Files\Tailscale\tailscale.exe",
        r"C:\Program Files (x86)\Tailscale\tailscale.exe",
    ):
        if shutil.os.path.exists(candidate):  # noqa: PTH110
            return candidate
    return None


async def whois(address: str, port: int) -> dict[str, Any] | None:
    """The tailnet user owning a peer address, or None."""
    if not address:
        return None

    now = time.time()
    hit = _cache.get(address)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]

    binary = _binary()
    if binary is None:
        _cache[address] = (now, None)
        return None

    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "whois", "--json", f"{address}:{port}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), WHOIS_TIMEOUT)
    except (OSError, asyncio.TimeoutError):
        _cache[address] = (now, None)
        return None

    identity = None
    if proc.returncode == 0:
        try:
            data = json.loads(out.decode("utf-8", "replace"))
            profile = data.get("UserProfile") or {}
            login = profile.get("LoginName")
            if login:
                identity = {
                    "login": login,
                    "name": profile.get("DisplayName") or login,
                    "node": (data.get("Node") or {}).get("Name", ""),
                    "via": "whois",
                }
        except (ValueError, AttributeError):
            identity = None

    _cache[address] = (now, identity)
    return identity


def from_headers(headers) -> dict[str, Any] | None:
    """Identity injected by `tailscale serve`. Refused unless headers are
    trusted for this bind - see TRUST_HEADERS."""
    if not TRUST_HEADERS:
        return None
    login = headers.get(HEADER_LOGIN)
    if not login:
        return None
    return {
        "login": login,
        "name": headers.get(HEADER_NAME) or login,
        "node": "",
        "via": "serve-header",
    }


async def identify(client_host: str | None, client_port: int, headers) -> dict[str, Any] | None:
    """Best available identity for a request, or None if the caller is
    anonymous. whois first: it is derived from the connection rather than
    from anything the client chose to send."""
    if client_host:
        found = await whois(client_host, client_port)
        if found:
            return found
    return from_headers(headers)


def available() -> bool:
    return _binary() is not None
