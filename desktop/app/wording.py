"""How 나루 says things (나루 디자인 시스템 §08 문구).

· 해요체, 능동형; the passive only for 만료·종료.
· Times in people's words: "어제 새벽 4:26", "오늘 새벽 4시", "3일째"; in lists
  short: "10월 6일 22:05".
· Sizes rounded to one unit: 4,741,108,614 bytes -> 4.4 GB.
· Buttons end in a verb.

Dates are built from struct_time numbers, never strftime with Korean in the
format: on non-Korean Windows that raises UnicodeEncodeError.

engine.py's status strings stay as they are - code compares them - and are
only translated here, on their way to the screen.
"""

from __future__ import annotations

import time

STATUS = {
    "대기": "꺼져 있어요",
    "실행 중": "켜져 있어요",
    "압축·전송 중": "보내는 중이에요",
    "일시중지": "잠시 멈췄어요",
    "수신 대기": "받을 준비가 됐어요",
}


def status(raw: str) -> str:
    return STATUS.get(raw, raw)


def _day_word(ts: float, now: float | None = None) -> str:
    now = time.time() if now is None else now
    lt, ln = time.localtime(ts), time.localtime(now)
    days = (time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
            - time.mktime((ln.tm_year, ln.tm_mon, ln.tm_mday, 0, 0, 0, 0, 0, -1)))
    d = round(days / 86400)
    return {0: "오늘", -1: "어제", 1: "내일"}.get(d, f"{lt.tm_mon}월 {lt.tm_mday}일")


def period(hour: int) -> str:
    if hour < 6:
        return "새벽"
    if hour < 12:
        return "오전"
    if hour < 18:
        return "오후"
    if hour < 21:
        return "저녁"
    return "밤"


def clock(hour: int, minute: int) -> str:
    """"새벽 4시", "오후 1:05"."""
    h12 = hour % 12 or 12
    return f"{period(hour)} {h12}시" if minute == 0 else f"{period(hour)} {h12}:{minute:02d}"


def when(ts: float, now: float | None = None) -> str:
    """Long form: "어제 새벽 4:26", "오늘 새벽 4시", "10월 6일 밤 10:05"."""
    lt = time.localtime(ts)
    return f"{_day_word(ts, now)} {clock(lt.tm_hour, lt.tm_min)}"


def when_short(ts: float, now: float | None = None) -> str:
    """List form: "어제 4:26", "10월 6일 22:05"."""
    lt = time.localtime(ts)
    return f"{_day_word(ts, now)} {lt.tm_hour}:{lt.tm_min:02d}"


def month_day(ts: float) -> str:
    lt = time.localtime(ts)
    return f"{lt.tm_mon}월 {lt.tm_mday}일"


def days_since(ts: float, now: float | None = None) -> int:
    now = time.time() if now is None else now
    return max(1, int((now - ts) // 86400))


def size(num_bytes: float | None) -> str:
    """One unit, one decimal: 4.4 GB, 12.3 MB, 812 KB."""
    if not num_bytes:
        return "0 B"
    n = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.1f} {unit}" if n < 100 else f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def duration(seconds: float) -> str:
    s = int(round(seconds))
    if s < 1:
        return f"{seconds:.1f}초"
    if s < 60:
        return f"{s}초"
    if s < 3600:
        return f"{s // 60}분 {s % 60}초" if s % 60 else f"{s // 60}분"
    return f"{s // 3600}시간 {s % 3600 // 60}분"


def schedule_name(minutes: int) -> str:
    m = int(minutes or 0)
    if m == 1440:
        return "하루에 한 번"
    if m == 10080:
        return "일주일에 한 번"
    if m and m % 60 == 0:
        return f"{m // 60}시간마다"
    return f"{m}분마다"


def schedule_note(minutes: int, at: str) -> str:
    """The right-hand note of a schedule choice: "새벽 4시", "일요일 새벽 4시",
    "하루 네 번"."""
    from tsbackup.schedule import parse_at

    h, mi = parse_at(at)
    m = int(minutes or 0)
    if m == 1440:
        return clock(h, mi)
    if m == 10080:
        return f"일요일 {clock(h, mi)}"
    if m and 1440 % m == 0:
        n = 1440 // m
        words = {2: "두", 3: "세", 4: "네", 6: "여섯", 8: "여덟", 12: "열두", 24: "스물네"}
        return f"하루 {words.get(n, str(n))} 번"
    return ""


def schedule_full(minutes: int, at: str) -> str:
    """Settings row value: "하루에 한 번, 새벽 4시"."""
    note = schedule_note(minutes, at)
    return f"{schedule_name(minutes)}, {note}" if note and int(minutes) in (1440, 10080) else schedule_name(minutes)


def first_send_estimate(source_bytes: int) -> str:
    """"20분쯤". From the measured real run (docs/VERIFICATION.md §9): 6.3 GB
    compressed and sent in about 15 minutes, i.e. ~7 MB of source a second."""
    minutes = max(1, round(source_bytes / 7_000_000 / 60))
    if minutes < 60:
        return f"{minutes}분쯤"
    return f"{minutes // 60}시간 {minutes % 60}분쯤" if minutes % 60 else f"{minutes // 60}시간쯤"
