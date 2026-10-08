"""The sender's run history - what the home screen's 지난 전송 list shows.

One JSON object per line, newest last, capped so the file never grows
without bound. Written by whoever ran the pass (the tray engine, or a Task
Scheduler `--run`), never by engine_core itself, so tests that drive
run_sender_once do not write into the person's real config folder.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

FILENAME = "history.jsonl"
KEEP = 100


def record(path: Path, result, elapsed: float) -> None:
    """Append one run. `elapsed` is the whole pass, compress and send -
    RunResult.seconds is the compression alone."""
    entry = {
        "at": time.time(),
        "ok": bool(result.ok),
        "archive": result.archive,
        "size": result.size,
        "elapsed": round(elapsed, 1),
        "transport": result.transport,
        "detail": result.detail,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        lines.append(json.dumps(entry, ensure_ascii=False))
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines[-KEEP:]) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass  # a history line is not worth failing a backup over


def load(path: Path, count: int = 20) -> list[dict]:
    """Newest first."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
        if len(out) >= count:
            break
    return out
