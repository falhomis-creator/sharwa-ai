"""core/app/api/routes_health.py - /healthz, /readyz, /metrics (protected).

readyz checks: Postgres reachable (via system_tx doing SELECT 1), redis-cache
reachable, and the gateway's own /healthz (spec: "PG عبر PgBouncer + الـRedisين
+ وصول البوابة"). Any failure => 503, never an exception leaking to the client.
"""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app import db as core_db
from app.db import repos

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
def readyz(request: Request) -> dict[str, object]:
    checks: dict[str, bool] = {}
    try:
        with core_db.system_tx() as conn:
            repos.check_alive(conn)
        checks["postgres"] = True
    except Exception:  # noqa: BLE001
        checks["postgres"] = False

    try:
        request.app.state.redis_sync._client.ping()
        checks["redis_cache"] = True
    except Exception:  # noqa: BLE001
        checks["redis_cache"] = False

    try:
        resp = request.app.state.gateway_client.healthz()
        checks["gateway"] = resp
    except Exception:  # noqa: BLE001
        checks["gateway"] = False

    ok = all(checks.values())
    if not ok:
        raise HTTPException(status_code=503, detail=checks)
    return {"status": "ready", "checks": checks}


@router.get("/metrics")
def metrics(request: Request, authorization: str | None = Header(default=None)) -> PlainTextResponse:
    settings = request.app.state.settings
    expected = f"Bearer {settings.metrics_token}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401)
    registry = request.app.state.metrics_registry
    return PlainTextResponse(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
