"""app/policy/quiet_hours.py - the daily quiet window, in tenant local time (H76).

S21: no clock reads here - `now_aware` is injected; wall-clock comparison uses
minute-of-day integers.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta


def _min_of_day(t: time) -> int:
    return t.hour * 60 + t.minute


def in_quiet_hours(now_aware: datetime, tz, start: time, end: time) -> bool:
    local = now_aware.astimezone(tz)
    m = _min_of_day(time(local.hour, local.minute))
    s = _min_of_day(start)
    e = _min_of_day(end)
    if e > s:
        return s <= m < e
    return m >= s or m < e


def next_allowed_at(now_aware: datetime, tz, start: time, end: time) -> datetime:
    """The earliest instant >= now_aware that is OUTSIDE [start, end). Invariant:
    never returns an instant inside the quiet window."""
    local = now_aware.astimezone(tz)
    m = _min_of_day(time(local.hour, local.minute))
    s = _min_of_day(start)
    e = _min_of_day(end)
    if e > s:
        if s <= m < e:
            return datetime.combine(local.date(), end, tzinfo=tz)
        return now_aware
    if m >= s:
        return datetime.combine(local.date() + timedelta(days=1), end, tzinfo=tz)
    if m < e:
        return datetime.combine(local.date(), end, tzinfo=tz)
    return now_aware
