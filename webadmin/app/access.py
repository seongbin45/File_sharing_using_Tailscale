"""What an identity is allowed to do.

Tailscale answers "who is this". It cannot answer "may this person open a
shell on the backup machine", because that question is about this console,
not about the network. So the roles live here, in a file you own, next to
the code that enforces them.

Four levels, and the boundaries are where they are for a reason:

    none      nothing. The default for an identity nobody listed.
    viewer    read. Dashboards, status, logs.
    operator  + run the task now, pause and resume the schedule.
    admin     + the terminal, and editing connection settings.

**The terminal is admin, alone at the top, and that is the important line.**
Every other permission here is a specific, bounded action. A shell is not: it
can rewrite the backup script, disable the schedule, read the .env files that
this whole project exists to protect, and delete the snapshots. Anyone with
the terminal has every other permission whether the table says so or not, so
handing it out is not a smaller decision than handing out admin - it is the
same decision.

Editing connection settings is admin for the same reason one step removed: it
can repoint a host at a machine of your choosing, and then the terminal goes
there.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
ACCESS_FILE = Path(os.environ.get("TSCONSOLE_ACCESS", BASE_DIR / "access.json"))
ACCESS_JSON_ENV = "TSCONSOLE_ACCESS_JSON"

NONE, VIEWER, OPERATOR, ADMIN = "none", "viewer", "operator", "admin"
ORDER = {NONE: 0, VIEWER: 1, OPERATOR: 2, ADMIN: 3}
LEVELS = tuple(ORDER)

LABEL = {NONE: "권한 없음", VIEWER: "읽기", OPERATOR: "운영", ADMIN: "관리자"}


def at_least(level: str, required: str) -> bool:
    return ORDER.get(level, 0) >= ORDER.get(required, 0)


class Policy:
    """Loaded once, reloadable. Deliberately tiny and deliberately a file:
    a table you can read, diff and review in a pull request beats a database
    nobody can see into."""

    def __init__(self) -> None:
        self._users: dict[str, str] = {}
        self._default: str = NONE
        self._lock = threading.RLock()
        self.source = ""
        self.load()

    def load(self) -> None:
        inline = os.environ.get(ACCESS_JSON_ENV, "").strip()
        if inline:
            raw = json.loads(inline)
            source = f"${ACCESS_JSON_ENV}"
        elif ACCESS_FILE.exists():
            raw = json.loads(ACCESS_FILE.read_text(encoding="utf-8"))
            source = str(ACCESS_FILE)
        else:
            raw, source = {}, "(없음)"

        users = {}
        for login, level in (raw.get("users") or {}).items():
            if level not in ORDER:
                # A typo like "adminn" must not silently become "no access"
                # in a way nobody notices, nor be treated as admin. Loud.
                raise ValueError(
                    f"{source}: '{login}' 의 권한 '{level}' 을 알 수 없습니다. "
                    f"가능한 값: {', '.join(LEVELS)}"
                )
            users[login.lower()] = level

        default = raw.get("default", NONE)
        if default not in ORDER:
            raise ValueError(f"{source}: default '{default}' 을 알 수 없습니다.")

        with self._lock:
            self._users = users
            self._default = default
            self.source = source

    def level_for(self, login: str | None) -> str:
        if not login:
            return NONE
        with self._lock:
            return self._users.get(login.lower(), self._default)

    @property
    def configured(self) -> bool:
        with self._lock:
            return bool(self._users) or self._default != NONE

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "source": self.source,
                "default": self._default,
                "users": dict(self._users),
                "configured": bool(self._users) or self._default != NONE,
            }


policy = Policy()


# --------------------------------------------------------------------------
# What each route needs
# --------------------------------------------------------------------------

# Checked longest-prefix-first, so a specific rule beats a general one.
RULES: tuple[tuple[str, str, str], ...] = (
    # (method or "*", path prefix, required level)
    ("*",    "/api/hosts/{id}/terminal", ADMIN),      # a shell is everything
    ("POST", "/api/servers",             ADMIN),      # repointing a host leads to a shell
    ("POST", "/api/hosts/{id}/credentials", ADMIN),
    ("POST", "/api/hosts/{id}/action",   OPERATOR),
    ("GET",  "/api/",                    VIEWER),
    ("GET",  "/static/",                 VIEWER),
    ("GET",  "/",                        VIEWER),
)


def required_for(method: str, path: str) -> str:
    """The level a request needs. Unknown paths default to admin: a route
    added later without a rule should be locked down, not open."""
    if path.startswith("/api/hosts/") and path.endswith("/terminal"):
        return ADMIN
    if method == "POST":
        if path == "/api/servers" or path.endswith("/credentials"):
            return ADMIN
        if path.endswith("/test"):
            return ADMIN
        if path.endswith("/action"):
            return OPERATOR
        return ADMIN
    if method in ("GET", "HEAD"):
        return VIEWER
    return ADMIN
