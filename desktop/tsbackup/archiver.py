"""Compressing the source directory into a timestamped 7z archive.

py7zr, not a bundled 7z.exe: the deliverable is a single PyInstaller binary
that must work on a machine with nothing installed. Shelling out to an external
7-Zip would put the one dependency this app cannot guarantee back into the
picture. py7zr gives real LZMA2 and bundles cleanly.

Nothing is excluded. .env and .git go in like everything else - the whole
reason this project exists is to preserve exactly those.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import py7zr

# <basename>_YYYY_MM_DD_HH_MM.7z - the same scheme the batch/PowerShell side
# uses, so the receiver's timestamp-as-git-tag convention carries straight over.
STAMP_RE = re.compile(r"_(\d{4}_\d{2}_\d{2}_\d{2}_\d{2})\.7z$")


@dataclass
class ArchiveResult:
    ok: bool
    path: Path | None
    size: int
    seconds: float
    error: str = ""


def timestamp() -> str:
    return time.strftime("%Y_%m_%d_%H_%M")


def archive_name(source_dir: str, stamp: str | None = None) -> str:
    base = Path(source_dir).name or "backup"
    return f"{base}_{stamp or timestamp()}.7z"


def prune_local(work_dir: str, source_dir: str, keep: int,
                log: Callable[[str], None] | None = None) -> None:
    """Delete older archives for this source, keeping the newest `keep`.

    This is the "지우고" half of "지우고 새로 압축": run it before building a new
    archive so a slow disk never holds two full snapshots longer than it must.
    Only files matching this source's own naming are touched - an unrelated
    .7z in the work directory is left alone.
    """
    base = Path(source_dir).name or "backup"
    work = Path(work_dir)
    if not work.is_dir():
        return
    mine = sorted(
        (p for p in work.glob(f"{base}_*.7z") if STAMP_RE.search(p.name)),
        key=lambda p: p.name,
    )
    excess = mine[:-keep] if keep > 0 else mine
    for path in excess:
        try:
            path.unlink()
            if log:
                log(f"이전 압축 삭제: {path.name}")
        except OSError as exc:
            if log:
                log(f"삭제 실패: {path.name} ({exc})")


def create_archive(source_dir: str, work_dir: str, level: int,
                   progress: Callable[[int], None] | None = None,
                   cancelled: Callable[[], bool] | None = None,
                   log: Callable[[str], None] | None = None) -> ArchiveResult:
    """Compress source_dir whole into work_dir, named by the current time.

    progress(percent) is called as files are added, so the UI can show a real
    bar rather than a spinner. cancelled() lets a stop request abandon a run
    partway without leaving the half-written archive behind.
    """
    src = Path(source_dir)
    if not src.is_dir():
        return ArchiveResult(False, None, 0, 0.0, f"대상 폴더가 없습니다: {source_dir}")

    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    out = work / archive_name(source_dir)
    started = time.time()

    # Enumerate first so progress is a real fraction, not a guess. For a large
    # tree this walk is cheap next to the compression that follows.
    entries: list[tuple[Path, str]] = []
    top = src.name
    for root, dirs, files in os.walk(src):
        rel_root = os.path.relpath(root, src)
        arc_root = top if rel_root == "." else os.path.join(top, rel_root)
        for name in files:
            entries.append((Path(root) / name, os.path.join(arc_root, name)))

    total = max(len(entries), 1)
    filters = [{"id": py7zr.FILTER_LZMA2, "preset": max(0, min(int(level), 9))}]

    try:
        with py7zr.SevenZipFile(out, "w", filters=filters) as archive:
            for index, (disk_path, arc_path) in enumerate(entries, start=1):
                if cancelled and cancelled():
                    archive.close()
                    out.unlink(missing_ok=True)
                    return ArchiveResult(False, None, 0, time.time() - started, "취소됨")
                try:
                    archive.write(disk_path, arc_path)
                except (OSError, PermissionError) as exc:
                    # A file held open by another process (an .idea lock, a
                    # sqlite db) must not sink the whole archive. Skip it, note
                    # it, keep going - the batch version tolerates the same.
                    if log:
                        log(f"건너뜀(잠김): {arc_path} ({exc})")
                if progress and index % 50 == 0:
                    progress(int(index * 100 / total))
        if progress:
            progress(100)
    except Exception as exc:  # noqa: BLE001 - report anything, never crash the loop
        out.unlink(missing_ok=True)
        return ArchiveResult(False, None, 0, time.time() - started, f"압축 실패: {exc}")

    size = out.stat().st_size if out.exists() else 0
    if size <= 0:
        out.unlink(missing_ok=True)
        return ArchiveResult(False, None, 0, time.time() - started, "빈 압축 파일")

    return ArchiveResult(True, out, size, time.time() - started)
