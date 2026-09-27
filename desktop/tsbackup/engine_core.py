"""The work itself, with no Qt in sight.

Keeping the one-shot sender and receiver passes as plain functions means they
can be tested headlessly - which is where the bugs are. The Qt engine in
engine.py is a timer and some signals wrapped around these two calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import archiver
from .transports import build as build_transport

# A failed run retries at this short interval, up to this many times, before
# falling back to waiting for the next full `interval_minutes` - otherwise a
# 04:00 failure gets no second try until the whole period (12h by default)
# has passed again. See next_retry_state() below for the actual decision;
# engine.py only wires it to Qt timers.
RETRY_INTERVAL_MINUTES = 20
MAX_RETRIES = 3


@dataclass
class RunResult:
    ok: bool
    archive: str = ""
    size: int = 0
    seconds: float = 0.0
    transport: str = ""
    detail: str = ""


def next_retry_state(
    ok: bool, retry_count: int, max_retries: int = MAX_RETRIES
) -> tuple[str, int]:
    """Decide what happens after one run, independent of Qt so it can be
    tested headlessly (engine.py's Engine wraps this with actual timers).

    Returns (action, new_retry_count):
      "reset"     - success (or the caller isn't retrying at all): schedule
                    the normal next run, counter back to zero.
      "retry"     - failure, retries remain: schedule a short retry.
      "exhausted" - failure, retries just ran out: notify once, then fall
                    back to the normal schedule with the counter reset - the
                    same effective outcome as "reset", but the caller should
                    fire its one failure notification here, not on every
                    individual failed attempt.
    """
    if ok:
        return "reset", 0
    retry_count += 1
    if retry_count <= max_retries:
        return "retry", retry_count
    return "exhausted", 0


# phase is one of: "compress", "transfer" - lets the UI label the bar.
ProgressFn = Callable[[str, int], None]
LogFn = Callable[[str], None]
CancelFn = Callable[[], bool]


def run_sender_once(cfg, log: LogFn,
                    progress: ProgressFn | None = None,
                    cancelled: CancelFn | None = None) -> RunResult:
    """One trigger: prune old archives, compress fresh, send.

    The order matters. Pruning first is the "지우고" in "지우고 새로 압축", so a
    tight disk never has to hold the old and new archive at once. The new
    archive is deleted only after it is sent, so a failed transfer leaves
    something to retry rather than nothing.
    """
    s = cfg.sender
    log("=== 실행 시작 ===")

    archiver.prune_local(s.work_dir, s.source_dir, s.keep_local, log)

    def comp_progress(p: int) -> None:
        if progress:
            progress("compress", p)

    result = archiver.create_archive(
        s.source_dir, s.work_dir, s.level,
        progress=comp_progress, cancelled=cancelled, log=log,
    )
    if not result.ok:
        log(f"압축 실패: {result.error}")
        return RunResult(False, detail=result.error)
    log(f"압축 완료: {result.path.name}, {result.size} bytes, {result.seconds:.1f}s")

    order = [s.transport] + [t for t in s.fallback_transports if t != s.transport]
    last = "전송 대상 없음"
    for name in order:
        if cancelled and cancelled():
            return RunResult(False, detail="취소됨")
        try:
            transport = build_transport(name, s, log)
        except ValueError as exc:
            last = str(exc)
            continue

        def tx_progress(p: int) -> None:
            if progress:
                progress("transfer", p)

        try:
            tr = transport.send(result.path, progress=tx_progress)
        finally:
            transport.close()

        if tr.ok:
            log(f"전송 성공: {name} -> {tr.detail} ({tr.seconds:.1f}s)")
            _cleanup_sent(result.path, s, log)
            return RunResult(True, result.path.name, result.size,
                             result.seconds, name, tr.detail)
        last = f"{name}: {tr.detail}"
        log(f"전송 실패: {last}")

    log(f"모든 전송 실패 - 압축 파일은 다음 실행까지 보관됩니다: {result.path.name}")
    return RunResult(False, result.path.name, result.size, result.seconds, detail=last)


def _cleanup_sent(path: Path, sender_cfg, log: LogFn) -> None:
    # A sent archive counts toward keep_local for the NEXT run's prune, so it
    # is normally left on disk as the newest kept copy. Only when keep_local is
    # 0 (keep nothing) is it removed now.
    if sender_cfg.keep_local <= 0:
        try:
            path.unlink()
            log(f"전송 후 삭제: {path.name}")
        except OSError:
            pass


def run_receiver_scan(receiver, log: LogFn) -> int:
    """One receiver pass: unpack whatever settled. Returns count unpacked."""
    return receiver.scan_once()
