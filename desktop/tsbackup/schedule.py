"""When the sender runs: at clock times, not "N minutes after the app opened".

The design (나루 디자인 시스템 §09 마법사, 홈) offers 6시간마다 / 하루에 한 번
(새벽 4시) / 일주일에 한 번 (일요일 새벽 4시) and promises "그때 컴퓨터가 꺼져
있으면, 다음에 켤 때 보내요". So a schedule is a series of slots anchored
to an "HH:MM" time:

  1440 minutes   every day at HH:MM
  10080 minutes  every Sunday at HH:MM
  anything else  HH:MM and every N minutes from it, restarting each day

A slot missed while the computer was off is caught up once at the next
start (due_now), never once per missed slot. Pure functions over datetimes,
so the rules are tested without Qt or a real clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta

DAY = 1440
WEEK = 10080


def parse_at(at: str) -> tuple[int, int]:
    try:
        h, m = (int(x) for x in str(at).split(":", 1))
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except ValueError:
        pass
    return 4, 0


def _slots_around(now: datetime, minutes: int, at: str):
    """Yield slot datetimes from a little before `now` to a little after."""
    h, m = parse_at(at)
    if minutes == WEEK:
        # Sunday is weekday() == 6.
        base = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(
            hour=h, minute=m, second=0, microsecond=0)
        for k in (-1, 0, 1, 2):
            yield base + timedelta(days=7 * k)
        return
    period = max(1, int(minutes))
    for day in (-1, 0, 1):
        anchor = (now + timedelta(days=day)).replace(hour=h, minute=m, second=0, microsecond=0)
        if period >= DAY:
            yield anchor
            continue
        # Slots repeat through the day, starting from HH:MM and wrapping.
        start = anchor - timedelta(minutes=period * ((h * 60 + m) // period))
        t = start
        while t < start + timedelta(days=1):
            yield t
            t += timedelta(minutes=period)


def next_slot(now: datetime, minutes: int, at: str) -> datetime:
    return min(t for t in _slots_around(now, minutes, at) if t > now)


def last_slot(now: datetime, minutes: int, at: str) -> datetime:
    return max(t for t in _slots_around(now, minutes, at) if t <= now)


def due_now(now: datetime, last_run: datetime | None, minutes: int, at: str) -> bool:
    """At start-up: was a slot missed since the last run? Never-run installs
    wait for their first slot (the wizard's test transfer already sent)."""
    if last_run is None:
        return False
    return last_run < last_slot(now, minutes, at)
