"""core/app/api/rate_limit.py - fail-open per-route console rate limiting (H59).

Every public console route has a written ceiling. The sliding-window log lives in
redis-cache (key = tenant_id + staff_id + route_group), and a hit above the
ceiling raises RATE_LIMITED with retry_after_s. When redis-cache is down the
request PASSES and a fail-open counter rises - rate limiting is abuse protection,
not a correctness guarantee (H47 is the opposite; this is deliberately fail-open).
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import Depends, Request

from app.api.errors import ApiError
from app.obs import metrics
from app.security.permissions import StaffContext, require_permission

_GROUP_LIMIT_ATTR = {
    "read": "console_rate_limit_read",
    "write": "console_rate_limit_write",
    "ticket": "console_rate_limit_ticket",
}


def check_rate_limit(
    client: Any,
    *,
    key: str,
    limit: int,
    window_s: float,
    now: float | None = None,
) -> float | None:
    """One sliding-window-log attempt. Returns None to allow, or the number of
    seconds to wait (retry_after_s) to block. Raises redis.RedisError on a cache
    failure - the caller decides fail-open (H59)."""
    now = time.time() if now is None else now
    member = f"{now:.6f}:{uuid.uuid4().hex}"
    pipe = client.pipeline(transaction=True)
    pipe.zadd(key, {member: now})
    pipe.zremrangebyscore(key, 0, now - window_s)
    pipe.zcard(key)
    pipe.expire(key, int(window_s) + 1)
    count = pipe.execute()[2]
    if count <= limit:
        return None
    # N2 (P1.8 audit): a rejected attempt must not count toward its own window.
    # Remove the member we just added - otherwise a blocked caller extends its
    # own block and retry_after_s (computed from the oldest ALLOWED member)
    # reads lower than reality.
    client.zrem(key, member)
    oldest = client.zrange(key, 0, 0, withscores=True)
    if oldest:
        retry = (oldest[0][1] + window_s) - now
    else:
        retry = window_s
    return max(retry, 0.0)


def _enforce(request: Request, staff: StaffContext, group: str) -> None:
    settings = request.app.state.settings
    limit: int = getattr(settings, _GROUP_LIMIT_ATTR[group])
    window_s: float = settings.console_rate_limit_window_s
    key = f"rl:{staff.tenant_id}:{staff.staff_id or 'platform_admin'}:{group}"
    client = request.app.state.redis_sync.client
    try:
        retry = check_rate_limit(client, key=key, limit=limit, window_s=window_s)
    except Exception:  # noqa: BLE001
        # H59 + N1 (P1.8 audit): fail-open must be fail-open, not fail-on-one-
        # exception-class. The path reaches the cache through the public accessor
        # RedisSync.client; if the wrapper is uninitialised or raises ANY error,
        # the console still must not go down - count it and let the request pass.
        metrics.rate_limit_fail_open_total.labels(group).inc()
        return
    if retry is not None:
        metrics.rate_limit_hits_total.labels(group).inc()
        raise ApiError("RATE_LIMITED", retry_after_s=int(retry) + 1)


def rate_limited(group: str, permission: str):
    """One dependency declaring BOTH the permission gate and the route's
    rate-limit ceiling, so every console route carries both (H59)."""
    def _check(
        request: Request,
        staff: StaffContext = Depends(require_permission(permission)),
    ) -> StaffContext:
        _enforce(request, staff, group)
        return staff
    return _check