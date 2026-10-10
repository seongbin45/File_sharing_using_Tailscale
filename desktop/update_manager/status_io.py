"""Per-run status files + atomic current.json pointer, for the tray's
"check now" polling. No ACL tuning (unlike CloneUp) - a Naru install
is single-user, so default Windows ACLs on install_dir already grant
that one user everything needed."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from update_manager.paths import status_root

log = logging.getLogger("tsbackup_update_manager")

_STATUS_KEEP = 48
_STATUS_MAX_AGE_SEC = 7 * 24 * 3600


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run_id() -> str:
    return str(uuid.uuid4())


def runs_dir(install_dir: Path) -> Path:
    d = status_root(install_dir) / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def current_path(install_dir: Path) -> Path:
    return status_root(install_dir) / "current.json"


def run_path(install_dir: Path, run_id: str) -> Path:
    return runs_dir(install_dir) / f"{run_id}.json"


def read_current_run_id(install_dir: Path) -> str | None:
    p = current_path(install_dir)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        rid = str(data.get("run_id") or "").strip()
        return rid or None
    except (OSError, json.JSONDecodeError):
        return None


def read_run(install_dir: Path, run_id: str) -> dict[str, Any] | None:
    p = run_path(install_dir, run_id)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def write_run(install_dir: Path, run_id: str, payload: dict[str, Any]) -> None:
    """Write only this run's file (never another run's)."""
    path = run_path(install_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=0), encoding="utf-8")
    os.replace(str(tmp), str(path))


def publish_current(install_dir: Path, run_id: str) -> None:
    """Atomic pointer update so readers see a consistent active run_id."""
    cur = current_path(install_dir)
    cur.parent.mkdir(parents=True, exist_ok=True)
    tmp = cur.with_suffix(".tmp")
    tmp.write_text(json.dumps({"run_id": run_id}, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp), str(cur))


def start_run(install_dir: Path, *, pid: int | None = None) -> str:
    run_id = new_run_id()
    payload = {
        "run_id": run_id,
        "started_at": _now_iso(),
        "finished_at": None,
        "phase": "running",
        "local": "",
        "remote": "",
        "error": "",
        "pid": pid or os.getpid(),
    }
    write_run(install_dir, run_id, payload)
    publish_current(install_dir, run_id)
    return run_id


def update_run(install_dir: Path, run_id: str, **fields: Any) -> None:
    data = read_run(install_dir, run_id) or {"run_id": run_id}
    data.update(fields)
    write_run(install_dir, run_id, data)


def finish_run(install_dir: Path, run_id: str, phase: str, *, error: str = "") -> None:
    update_run(
        install_dir, run_id,
        phase=phase,
        finished_at=_now_iso(),
        error=(error or "")[:500],
    )
    publish_current(install_dir, run_id)
    prune_old_runs(install_dir, keep_current=run_id)


def prune_old_runs(install_dir: Path, *, keep_current: str | None = None) -> None:
    """Age then count prune."""
    d = runs_dir(install_dir)
    cur = keep_current or read_current_run_id(install_dir)
    now = time.time()
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files:
        if cur and p.stem == cur:
            continue
        try:
            age = now - p.stat().st_mtime
            if age > _STATUS_MAX_AGE_SEC:
                p.unlink(missing_ok=True)
        except OSError:
            pass
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[_STATUS_KEEP:]:
        if cur and p.stem == cur:
            continue
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
