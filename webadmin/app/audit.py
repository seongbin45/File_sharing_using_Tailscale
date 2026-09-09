"""Who did what, when.

Access control without a record is half a system. Knowing that only three
people can open a shell is worth much less than being able to answer "who
opened one at 03:00 on the 14th", which is the question you actually get
asked afterwards.

JSONL, appended, one event per line. No rotation by size on purpose - these
lines are short and rare (a login, an action, a terminal session), so a year
of them is a small file, and losing the oldest half of an audit log to make
room is exactly backwards.

Reads are not logged. Logging every dashboard poll at 10-second intervals
would bury the six lines a month that matter.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
AUDIT_FILE = Path(os.environ.get("TSCONSOLE_AUDIT", BASE_DIR / "audit.log"))

_lock = threading.Lock()
_disabled_reason: str | None = None


def record(event: str, *, who: str | None = None, level: str | None = None,
           client: str | None = None, **detail: Any) -> None:
    global _disabled_reason
    if _disabled_reason:
        return

    line = json.dumps(
        {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "event": event,
            "who": who or "anonymous",
            "level": level,
            "client": client,
            **detail,
        },
        ensure_ascii=False,
    )
    try:
        with _lock:
            with AUDIT_FILE.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except OSError as exc:
        # A read-only filesystem must not take the console down, but it must
        # not be silent either - an audit log that stopped working without
        # anyone noticing is worse than none at all.
        _disabled_reason = str(exc)
        print(f"WARNING: 감사 로그를 쓸 수 없어 비활성화합니다: {exc}", flush=True)


def status() -> dict[str, Any]:
    return {
        "path": str(AUDIT_FILE),
        "enabled": _disabled_reason is None,
        "reason": _disabled_reason,
    }


def tail(count: int = 100) -> list[dict[str, Any]]:
    if not AUDIT_FILE.exists():
        return []
    try:
        lines = AUDIT_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-count:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
