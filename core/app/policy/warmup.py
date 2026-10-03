"""app/policy/warmup.py - the warm-up ladder (H81: the cap rises only with days)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping


def parse_ladder(src: str) -> dict[int, int]:
    """'0:20,3:40,7:80,14:150,30:250' -> {day_index: cap}."""
    out: dict[int, int] = {}
    for chunk in src.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        day_s, cap_s = chunk.split(":", 1)
        out[int(day_s)] = int(cap_s)
    if not out:
        raise ValueError("warm-up ladder is empty")
    return out


def day_index(started_at: datetime, now: datetime, tz) -> int:
    """Whole local days since the warm-up started (day 0 = the first send)."""
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    local_start = started_at.astimezone(tz)
    local_now = now.astimezone(tz)
    return max(0, (local_now.date() - local_start.date()).days)


def cap_for(day: int, ladder: Mapping[int, int]) -> int:
    """The marketing daily cap for a warm-up day: the highest ladder entry whose
    day <= `day`. An empty ladder fails closed (ValueError)."""
    if not ladder:
        raise ValueError("warm-up ladder is empty")
    return max(cap for d, cap in ladder.items() if d <= day)
