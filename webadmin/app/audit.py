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

import hashlib
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

# Each record carries the hash of the one before it, so removing or editing a
# line breaks every hash after it and verify() says where.
#
# This is tamper-EVIDENT, not tamper-proof: anyone who can write the file can
# also recompute the whole chain. It becomes proof only if the head hash is
# recorded somewhere the same attacker does not control - print it, mail it to
# yourself, commit it. head() exists for exactly that.
GENESIS = "0" * 64
_head: str | None = None


def _digest(record: dict[str, Any]) -> str:
    body = json.dumps(record, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def head() -> str:
    """Hash of the newest record. Anchor this off-box to make the chain mean
    something."""
    global _head
    if _head is not None:
        return _head
    events = tail(1)
    _head = events[-1].get("hash", GENESIS) if events else GENESIS
    return _head


def record(event: str, *, who: str | None = None, level: str | None = None,
           client: str | None = None, **detail: Any) -> None:
    global _disabled_reason, _head
    if _disabled_reason:
        return

    try:
        with _lock:
            record = {
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "event": event,
                "who": who or "anonymous",
                "level": level,
                "client": client,
                **detail,
                "prev": head(),
            }
            record["hash"] = _digest(record)
            with AUDIT_FILE.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            _head = record["hash"]
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
        "head": head(),
    }


def verify() -> dict[str, Any]:
    """Walk the chain. Returns where it first breaks, if it does."""
    events = tail(10_000_000)
    expected = GENESIS
    for index, record in enumerate(events, start=1):
        stored = record.get("hash")
        body = {k: v for k, v in record.items() if k != "hash"}
        if record.get("prev") != expected:
            return {"ok": False, "line": index, "problem": "앞 기록과 연결되지 않습니다",
                    "checked": index - 1}
        if stored != _digest(body):
            return {"ok": False, "line": index, "problem": "기록이 수정되었습니다",
                    "checked": index - 1}
        expected = stored
    return {"ok": True, "checked": len(events), "head": expected}


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
