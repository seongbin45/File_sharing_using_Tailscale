"""Login for the console.

Until now the console had no authentication and did not need any: it bound to
loopback, so the only way to reach it was to already be on the machine.

The moment it is deployed anywhere with a public URL that stops being true,
and what is behind the URL is an interactive shell on someone's home PC. So:

  * a password is REQUIRED whenever the bind address is not loopback. The
    server refuses to start otherwise (see main.check_deployment). Failing to
    boot is a far better outcome than quietly serving an open shell.
  * failed attempts are rate limited per client, because a public endpoint
    gets guessed at within hours of existing.
  * the cookie is HMAC-signed with a secret and carries its own expiry.

Deliberately no user database, no registration, no password reset. One
password, set by the operator, checked in constant time. Anything more is
more attack surface for a tool exactly one person uses.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

COOKIE_NAME = "ts_console"
SESSION_HOURS = 12

# Attempts allowed per client before it has to wait, and how long the window is.
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 300

LOOPBACK = ("127.0.0.1", "::1", "localhost")


def configured_password() -> str | None:
    value = os.environ.get("TSCONSOLE_PASSWORD", "").strip()
    return value or None


def _secret() -> bytes:
    """Cookie signing key.

    An explicit TSCONSOLE_SECRET keeps sessions alive across restarts. Without
    one a random key is generated per process, which is not an error - it just
    means everyone logs in again after a redeploy. On a free tier that spins
    the container down when idle, that is most days, so set it.
    """
    configured = os.environ.get("TSCONSOLE_SECRET", "").strip()
    if configured:
        return configured.encode("utf-8")
    global _EPHEMERAL_SECRET
    if _EPHEMERAL_SECRET is None:
        _EPHEMERAL_SECRET = secrets.token_bytes(32)
    return _EPHEMERAL_SECRET


_EPHEMERAL_SECRET: bytes | None = None


def is_loopback(bind: str) -> bool:
    return bind in LOOPBACK


def auth_required(bind: str) -> bool:
    """Loopback is trusted; anything else must have a password."""
    return not is_loopback(bind)


# ----------------------------------------------------------------- cookies


def _sign(payload: bytes) -> str:
    mac = hmac.new(_secret(), payload, hashlib.sha256).digest()
    return (
        base64.urlsafe_b64encode(payload).decode().rstrip("=")
        + "."
        + base64.urlsafe_b64encode(mac).decode().rstrip("=")
    )


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_cookie() -> str:
    payload = json.dumps(
        {"exp": int(time.time()) + SESSION_HOURS * 3600}, separators=(",", ":")
    ).encode()
    return _sign(payload)


def cookie_valid(value: str | None) -> bool:
    if not value or "." not in value:
        return False
    body, mac = value.rsplit(".", 1)
    try:
        payload = _unb64(body)
        given = _unb64(mac)
    except (ValueError, TypeError):
        return False

    expected = hmac.new(_secret(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, given):
        return False
    try:
        return int(json.loads(payload)["exp"]) > time.time()
    except (ValueError, KeyError, TypeError):
        return False


def password_ok(given: str) -> bool:
    expected = configured_password()
    if expected is None:
        return False
    # Constant time: a length-dependent comparison leaks the password length
    # to anyone who can time the endpoint, and a public endpoint can be timed.
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


# ------------------------------------------------------------ rate limiting

_attempts: dict[str, list[float]] = {}


def _prune(client: str, now: float) -> list[float]:
    recent = [t for t in _attempts.get(client, []) if now - t < LOCKOUT_SECONDS]
    if recent:
        _attempts[client] = recent
    else:
        _attempts.pop(client, None)
    return recent


def throttled(client: str) -> int:
    """Seconds the client must wait, or 0 if it may try now."""
    now = time.time()
    recent = _prune(client, now)
    if len(recent) < MAX_ATTEMPTS:
        return 0
    return max(1, int(LOCKOUT_SECONDS - (now - recent[0])))


def record_failure(client: str) -> None:
    now = time.time()
    _prune(client, now)
    _attempts.setdefault(client, []).append(now)


def clear_failures(client: str) -> None:
    _attempts.pop(client, None)


# ------------------------------------------------------------------ status


def describe() -> dict[str, Any]:
    return {
        "password_set": configured_password() is not None,
        "secret_persistent": bool(os.environ.get("TSCONSOLE_SECRET", "").strip()),
        "session_hours": SESSION_HOURS,
    }
