"""Unsent archives, kept for the next run to resend before compressing anew.

Policy: when every transport fails, the archive is parked here and the next
run resends it instead of building a fresh snapshot. The snapshot it carries
is the one taken when it was first compressed - a retry 20 minutes later
sends that older snapshot, not a newer one. This is the batch version's
pending/ directory (scripts/ts_backup.bat), and it is what makes a resumed
transfer possible at all: a resume needs the same file on both attempts.

Layout, inside the sender's work_dir:

    pending/<name>.7z      the archive, under its original name
    pending/<name>.json    job_id, snapshot, size, sha256, created_at

The .json is the claim that the archive is a complete, verified, unsent
snapshot. It is written last when parking and removed first when completing,
so an archive without its .json is never trusted - it is an interrupted
park or completion, and is cleaned up.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .archiver import STAMP_RE

DIRNAME = "pending"
KEEP_DAYS = 3        # same as the batch version's PENDING_KEEP_DAYS
KEEP_COUNT = 1       # same as PENDING_KEEP_COUNT: each one is a full snapshot

LogFn = Callable[[str], None]


@dataclass
class Pending:
    path: Path
    job_id: str
    snapshot: str
    size: int
    sha256: str


def pending_dir(work_dir: str) -> Path:
    return Path(work_dir) / DIRNAME


def sha256_file(path: Path, cancelled: Callable[[], bool] | None = None) -> str | None:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            if cancelled and cancelled():
                return None
            block = fh.read(4 * 1024 * 1024)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _meta_path(archive: Path) -> Path:
    return archive.with_suffix(".json")


def park(archive: Path, work_dir: str, log: LogFn) -> Pending | None:
    """Move a just-failed archive into pending/ with its verification record.
    Returns None (and leaves the archive where it was) if that fails - the
    next run then simply compresses a fresh snapshot, as before."""
    m = STAMP_RE.search(archive.name)
    if not m:
        return None
    pdir = pending_dir(work_dir)
    try:
        pdir.mkdir(parents=True, exist_ok=True)
        digest = sha256_file(archive)
        dest = pdir / archive.name
        os.replace(archive, dest)
        meta = {
            "job_id": uuid.uuid4().hex,
            "snapshot": m.group(1),
            "size": dest.stat().st_size,
            "sha256": digest,
            "created_at": time.time(),
        }
        tmp = _meta_path(dest).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta), encoding="utf-8")
        os.replace(tmp, _meta_path(dest))
    except OSError as exc:
        log(f"pending 보관 실패: {archive.name} ({exc})")
        return None
    log(f"pending 보관: {dest.name} (스냅샷 {meta['snapshot']}, job {meta['job_id'][:8]})")
    return Pending(dest, meta["job_id"], meta["snapshot"], meta["size"], meta["sha256"])


def _remove(path: Path, log: LogFn, why: str) -> None:
    for p in (_meta_path(path), path):
        try:
            p.unlink(missing_ok=True)
        except OSError as exc:
            log(f"pending 삭제 실패: {p.name} ({exc})")
            return
    log(f"pending 삭제({why}): {path.name}")


def take(work_dir: str, source_dir: str, log: LogFn,
         cancelled: Callable[[], bool] | None = None,
         now: float | None = None) -> Pending | None:
    """Expire old entries, then return the newest pending archive for this
    source if it still verifies (size, sha256, snapshot stamp). A pending
    archive that fails verification is deleted, never resent."""
    pdir = pending_dir(work_dir)
    if not pdir.is_dir():
        return None
    now = time.time() if now is None else now
    base = Path(source_dir).name or "backup"

    for meta_file in pdir.glob("*.json"):
        if not meta_file.with_suffix(".7z").exists():
            meta_file.unlink(missing_ok=True)

    entries: list[tuple[Path, dict]] = []
    for archive in pdir.glob("*.7z"):
        try:
            meta = json.loads(_meta_path(archive).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _remove(archive, log, "기록 없음")
            continue
        if not archive.name.startswith(f"{base}_"):
            continue
        if now - float(meta.get("created_at", 0)) > KEEP_DAYS * 86400:
            _remove(archive, log, f"{KEEP_DAYS}일 경과")
            continue
        entries.append((archive, meta))

    entries.sort(key=lambda e: e[0].name, reverse=True)
    for archive, _meta in entries[KEEP_COUNT:]:
        _remove(archive, log, f"최신 {KEEP_COUNT}개 초과")
    if not entries:
        return None

    archive, meta = entries[0]
    m = STAMP_RE.search(archive.name)
    if not m or m.group(1) != meta.get("snapshot"):
        _remove(archive, log, "스냅샷 시각 불일치")
        return None
    if archive.stat().st_size != meta.get("size"):
        _remove(archive, log, "크기 불일치")
        return None
    digest = sha256_file(archive, cancelled)
    if digest is None:
        return None
    if digest != meta.get("sha256"):
        _remove(archive, log, "해시 불일치")
        return None
    return Pending(archive, str(meta.get("job_id", "")), meta["snapshot"], meta["size"], digest)


def complete(p: Pending, work_dir: str) -> Path:
    """Mark a pending archive as sent: drop its record first (from then on it
    is no longer pending, even if the move below never happens), then move it
    back into work_dir, where keep_local governs it like any sent archive."""
    _meta_path(p.path).unlink(missing_ok=True)
    dest = Path(work_dir) / p.path.name
    try:
        os.replace(p.path, dest)
    except OSError:
        return p.path  # record already gone: the next take() removes it
    return dest
