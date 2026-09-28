"""Pure console rate-limit tests (P1.8 §3.2, H59) - no DB, no live redis.

Covers the sliding-window log (under/over ceiling + expiry) and the two policy
paths in `_enforce`: fail-open when redis-cache is down (request passes, counter
rises) and RATE_LIMITED with retry_after_s above the ceiling.
"""
from __future__ import annotations

import time
import uuid
from types import SimpleNamespace

import pytest

import redis

from app.api import rate_limit
from app.api.errors import ApiError
from app.security.permissions import StaffContext


class _FakeRedis:
    """In-memory sliding-window-log stand-in for check_rate_limit."""

    def __init__(self) -> None:
        self.logs: dict[str, list[tuple[str, float]]] = {}

    def pipeline(self, transaction=True):
        return _Pipe(self)

    def zrange(self, key, start, stop, withscores=False):
        items = sorted(self.logs.get(key, []), key=lambda kv: kv[1])
        sl = items[start:stop + 1] if stop >= 0 else items[start:]
        if withscores:
            return [(m, s) for m, s in sl]
        return [m for m, _ in sl]

    def zrem(self, key, member):
        self.logs[key] = [(m, s) for m, s in self.logs.get(key, []) if m != member]
        return 1


class _Pipe:
    def __init__(self, client: "_FakeRedis") -> None:
        self._c = client
        self._ops: list[tuple] = []

    def zadd(self, key, mapping):
        self._ops.append(("zadd", key, mapping))
        return self

    def zremrangebyscore(self, key, mn, mx):
        self._ops.append(("zrem", key, mn, mx))
        return self

    def zcard(self, key):
        self._ops.append(("zcard", key))
        return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))
        return self

    def execute(self):
        out = []
        for op in self._ops:
            if op[0] == "zadd":
                _, key, mapping = op
                self._c.logs.setdefault(key, []).extend(mapping.items())
                out.append(1)
            elif op[0] == "zrem":
                _, key, mn, mx = op
                self._c.logs[key] = [
                    (m, s) for m, s in self._c.logs.get(key, []) if not (mn <= s <= mx)
                ]
                out.append(0)
            elif op[0] == "zcard":
                out.append(len(self._c.logs.get(op[1], [])))
            elif op[0] == "expire":
                out.append(True)
        return out


class _FailRedis:
    def pipeline(self, transaction=True):
        raise redis.RedisError("down")


class _FakeSettings:
    console_rate_limit_read = 5
    console_rate_limit_write = 60
    console_rate_limit_ticket = 10
    console_rate_limit_window_s = 60.0


class _FakeCounter:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def labels(self, group: str):
        self._calls.append(group)
        return self

    def inc(self) -> None:
        pass


def _request(client) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        settings=_FakeSettings(),
        redis_sync=SimpleNamespace(client=client),
    )))


def _staff() -> StaffContext:
    return StaffContext(
        tenant_id=uuid.uuid4(), staff_id=uuid.uuid4(), role="owner", permissions=frozenset(),
    )


# --- check_rate_limit (pure sliding window) ------------------------------------


def test_under_ceiling_passes():
    client = _FakeRedis()
    assert rate_limit.check_rate_limit(client, key="k", limit=5, window_s=60.0, now=100.0) is None


def test_over_ceiling_returns_retry_after():
    client = _FakeRedis()
    for _ in range(6):
        rate_limit.check_rate_limit(client, key="k", limit=5, window_s=60.0, now=100.0)
    retry = rate_limit.check_rate_limit(client, key="k", limit=5, window_s=60.0, now=100.0)
    assert retry is not None
    assert retry > 0


def test_window_slides_so_old_attempts_expire():
    client = _FakeRedis()
    for _ in range(6):
        rate_limit.check_rate_limit(client, key="k", limit=5, window_s=60.0, now=100.0)
    # 61 seconds later the window has moved past those attempts.
    assert rate_limit.check_rate_limit(client, key="k", limit=5, window_s=60.0, now=161.0) is None


# --- _enforce policy paths ------------------------------------------------------


def test_enforce_rate_limited_above_ceiling(monkeypatch):
    client = _FakeRedis()
    staff = _staff()
    key = f"rl:{staff.tenant_id}:{staff.staff_id}:read"
    now = time.time()
    for _ in range(6):
        rate_limit.check_rate_limit(client, key=key, limit=5, window_s=60.0, now=now)
    hits: list[str] = []
    monkeypatch.setattr(rate_limit.metrics, "rate_limit_hits_total", _FakeCounter(hits))
    with pytest.raises(ApiError) as exc:
        rate_limit._enforce(_request(client), staff, "read")
    assert exc.value.code == "RATE_LIMITED"
    assert exc.value.retry_after_s is not None and exc.value.retry_after_s > 0
    assert hits == ["read"]


def test_enforce_fail_open_when_redis_down(monkeypatch):
    staff = _staff()
    hits: list[str] = []
    monkeypatch.setattr(rate_limit.metrics, "rate_limit_fail_open_total", _FakeCounter(hits))
    # _FailRedis raises RedisError on pipeline() -> fail-open, no raise, counter rises.
    rate_limit._enforce(_request(_FailRedis()), staff, "read")
    assert hits == ["read"]