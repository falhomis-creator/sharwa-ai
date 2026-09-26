"""core/app/api/routes_channels.py - P0.7 channel-lifecycle routes (spec:
prompts/P0_DEEPSEEK_PROMPT.md's "الواجهات (/v1)" table):

  GET  /v1/me
  GET  /v1/channels
  POST /v1/channels/whatsapp        (Idempotency-Key)
  GET  /v1/channels/{id}
  GET  /v1/channels/{id}/qr
  POST /v1/channels/{id}/reconnect

GET /v1/channels/{id}/delivery-stats is deliberately NOT built here - its
gateway-side dependency (GET /sessions/:id/stats) does not exist in the real
gateway code (verified by reading gateway/src/index.js in full; grepped for
"stats" across gateway/src - zero route matches). Building it would mean
inventing an entire gateway-side stats-tracking subsystem (stats:{sid}:
{YYYYMMDD} counters, a 35-day retention policy) that nobody has asked for yet
and that this batch's approved scope (the engine/tenant_id/channel_account_id
metadata gap) never covered - see docs/P0_FINDINGS.md for this as its own,
separately tracked open item, exactly like OQ-7 before it.

Every route here goes through tenant_tx(ctx.tenant_id) - never a bare
psycopg connection - so RLS's tenant_isolation policy is always the real
backstop, not just this file's own WHERE clauses (H2).
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel

from app import db as core_db
from app.api.deps import AuthContext, authenticate, require_role
from app.api.errors import ApiError
from app.channels.gateway_client import GatewayUnavailableError
from app.db import repos

router = APIRouter()

# spec: "engine='ai_core'" - P0.7's channel-lifecycle routes are the strangler
# switch's core-side half; every channel THIS API creates opts a channel into
# the new engine. A channel created the legacy way (directly in Postgres, e.g.
# by the Django admin/CLI - engine defaults to 'django' in the schema) is
# never touched by these routes' own creation path, only ever read/listed by
# GET /v1/channels and GET /v1/channels/{id}.
ENGINE = "ai_core"


class ChannelOut(BaseModel):
    id: uuid.UUID
    type: str
    engine: str
    status: str
    phone_e164: str | None


def _channel_out(row: dict[str, Any]) -> ChannelOut:
    return ChannelOut(
        id=row["id"], type=row["type"], engine=row["engine"],
        status=row["status"], phone_e164=row["phone_e164"],
    )


class ChannelHealthOut(ChannelOut):
    # Additive fields from the gateway's own GET /sessions/:id/health (P0.5) -
    # None whenever the gateway call itself could not be made (GATEWAY_UNAVAILABLE
    # is raised instead in that case, so these are only ever None if the
    # gateway responded 404 for a session id the DB still thinks exists - see
    # the comment at the call site).
    reconnect_attempts: int | None = None
    last_disconnect_reason: str | None = None
    queue_depth: int | None = None


@router.get("/v1/me")
def get_me(ctx: AuthContext = Depends(authenticate)) -> dict[str, str]:
    with core_db.tenant_tx(ctx.tenant_id) as conn:
        name = repos.fetch_tenant_name(conn, ctx.tenant_id)
    return {"name": name, "role": ctx.principal.role}


@router.get("/v1/channels")
def list_channels(ctx: AuthContext = Depends(authenticate)) -> list[ChannelOut]:
    with core_db.tenant_tx(ctx.tenant_id) as conn:
        rows = repos.list_channel_accounts(conn, ctx.tenant_id)
    return [_channel_out(row) for row in rows]


def _hash_request_body(body: dict[str, Any]) -> str:
    # sha256 of the normalized (sorted-key) body - so key ORDER in the raw
    # request never causes a false "different body" conflict (0002_p0_api.sql's
    # own comment: "sha256 of the normalized request body").
    normalized = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@router.post("/v1/channels/whatsapp", status_code=201)
def create_whatsapp_channel(
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: AuthContext = Depends(require_role("merchant_admin")),
) -> ChannelOut:
    if not idempotency_key:
        raise ApiError("VALIDATION_FAILED")
    # This route has no request body of its own (spec lists only the
    # Idempotency-Key header - session_id/engine are generated here, never
    # accepted from the caller) - the "request body" the idempotency record
    # hashes is deliberately the empty object, so a genuine replay (same key,
    # same - empty - body) is recognized as one.
    request_hash = _hash_request_body({})

    with core_db.tenant_tx(ctx.tenant_id) as conn:
        existing = repos.fetch_idempotency_record(
            conn, tenant_id=ctx.tenant_id, idempotency_key=idempotency_key,
        )
        if existing is not None:
            if existing["request_hash"] != request_hash:
                # Same key, genuinely different request - a real conflict, not
                # a replay (0002_p0_api.sql's own comment on api_idempotency).
                # No dedicated error code exists for this in the spec's fixed
                # catalog, so this is VALIDATION_FAILED - a documented,
                # deliberate interpretation (see docs/P0_FINDINGS.md), not an
                # invented new code.
                raise ApiError("VALIDATION_FAILED")
            status = existing["response_status"]
            assert isinstance(status, int)
            response.status_code = status
            body = existing["response_body"]
            assert isinstance(body, dict)
            return ChannelOut(**body)

        session_id = secrets.token_urlsafe(32)  # spec: "عشوائي غير قابل للتخمين"
        try:
            row = repos.insert_whatsapp_channel_account(
                conn, tenant_id=ctx.tenant_id, session_id=session_id, engine=ENGINE,
            )
        except repos.ChannelAlreadyExistsError as exc:
            raise ApiError("CHANNEL_ALREADY_EXISTS") from exc

        channel_out = _channel_out(row)

        try:
            gw_resp = request.app.state.gateway_client.create_session(
                session_id=session_id, tenant_id=str(ctx.tenant_id),
                channel_account_id=str(channel_out.id), engine=ENGINE,
            )
        except GatewayUnavailableError as exc:
            raise ApiError("GATEWAY_UNAVAILABLE") from exc
        if gw_resp.status_code >= 400:
            raise ApiError("GATEWAY_UNAVAILABLE")
        gw_status = gw_resp.json()
        # The gateway's own frozen status vocabulary (mapConnectionStatus,
        # gateway/src/sessions.js) uses UPPER_SNAKE ('QR_PENDING', 'CONNECTED',
        # ...) - channel_accounts.status's CHECK constraint (0001_baseline.sql)
        # is lower_snake ('qr_pending', 'connected', ...). This mapping is the
        # ONLY place that translation happens; nowhere else stores or compares
        # the gateway's raw vocabulary directly.
        mapped_status = _GATEWAY_STATUS_TO_DB.get(gw_status.get("status", ""), "unknown")
        repos.update_channel_account_status(
            conn, channel_id=channel_out.id, tenant_id=ctx.tenant_id,
            status=mapped_status, phone_e164=gw_status.get("connected_phone_number"),
        )
        channel_out = channel_out.model_copy(update={"status": mapped_status})

        repos.store_idempotency_record(
            conn, tenant_id=ctx.tenant_id, idempotency_key=idempotency_key,
            request_hash=request_hash, response_status=201,
            response_body=channel_out.model_dump(mode="json"),
        )

    return channel_out


# gateway/src/sessions.js's mapConnectionStatus is the frozen source of truth
# for the left-hand vocabulary (comment there: "contract-frozen vocabulary").
# 'UNKNOWN' is its own synthetic pre-tracking sentinel, mapped to the DB's own
# 'unknown' default rather than invented as a 7th DB status value.
_GATEWAY_STATUS_TO_DB = {
    "UNKNOWN": "unknown",
    "QR_PENDING": "qr_pending",
    "CONNECTED": "connected",
    "DISCONNECTED": "disconnected",
    "BANNED": "banned",
}


@router.get("/v1/channels/{channel_id}")
def get_channel(
    channel_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(authenticate),
) -> ChannelHealthOut:
    with core_db.tenant_tx(ctx.tenant_id) as conn:
        row = repos.fetch_channel_account(conn, tenant_id=ctx.tenant_id, channel_id=channel_id)
        if row is None:
            raise ApiError("NOT_FOUND")  # spec: another tenant's resource is NEVER FORBIDDEN
        session_id = repos.fetch_channel_session_id(conn, tenant_id=ctx.tenant_id, channel_id=channel_id)
        assert session_id is not None  # fetch_channel_account above already proved this channel exists

        try:
            gw_resp = request.app.state.gateway_client.session_health(session_id)
        except GatewayUnavailableError as exc:
            raise ApiError("GATEWAY_UNAVAILABLE") from exc

        extra: dict[str, Any] = {}
        if gw_resp.status_code == 200:
            health = gw_resp.json()
            extra = {
                "reconnect_attempts": health.get("reconnect_attempts"),
                "last_disconnect_reason": health.get("last_disconnect_reason"),
                "queue_depth": health.get("queue_depth"),
            }
        elif gw_resp.status_code != 404:
            # 404 legitimately means "the gateway process holding this session
            # is not the one that answered" or "never started here" - the DB
            # row (this tenant's own record) is still the source of truth for
            # existence; any OTHER gateway status is unexpected (H3: surface
            # it, never silently treat as "no extra health data").
            raise ApiError("GATEWAY_UNAVAILABLE")

    return ChannelHealthOut(**_channel_out(row).model_dump(), **extra)


@router.get("/v1/channels/{channel_id}/qr")
def get_channel_qr(
    channel_id: uuid.UUID, request: Request, response: Response, ctx: AuthContext = Depends(authenticate),
) -> dict[str, str | None]:
    response.headers["Cache-Control"] = "no-store"  # spec, literal
    with core_db.tenant_tx(ctx.tenant_id) as conn:
        row = repos.fetch_channel_account(conn, tenant_id=ctx.tenant_id, channel_id=channel_id)
        if row is None:
            raise ApiError("NOT_FOUND")  # spec: another tenant's resource is NEVER FORBIDDEN
        session_id = repos.fetch_channel_session_id(conn, tenant_id=ctx.tenant_id, channel_id=channel_id)
        assert session_id is not None  # fetch_channel_account above already proved this channel exists
        try:
            gw_resp = request.app.state.gateway_client.session_qr(session_id)
        except GatewayUnavailableError as exc:
            raise ApiError("GATEWAY_UNAVAILABLE") from exc
    if gw_resp.status_code != 200:
        raise ApiError("GATEWAY_UNAVAILABLE")
    body = gw_resp.json()
    return {"qr": body.get("qr")}


_RECONNECT_ALLOWED_STATUSES = ("conflict", "disconnected", "logged_out")


@router.post("/v1/channels/{channel_id}/reconnect")
def reconnect_channel(
    channel_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(require_role("merchant_admin")),
) -> ChannelOut:
    settings = request.app.state.settings
    redis_client = request.app.state.redis_sync._client
    rate_limit_key = f"reconnect_rl:{channel_id}"
    # spec: "حدّ معدل 1/30ث لكل قناة" - writes are fail-closed (spec's own
    # "الحماية" section: "عمليات الكتابة: fail-closed"), so an unreachable
    # redis-cache here must refuse the reconnect, never silently allow it
    # through unlimited.
    try:
        acquired = redis_client.set(rate_limit_key, "1", nx=True, ex=settings.reconnect_rate_limit_s)
    except Exception as exc:  # redis.RedisError and friends; fail-closed either way, never swallowed
        raise ApiError("GATEWAY_UNAVAILABLE") from exc
    if not acquired:
        raise ApiError("RATE_LIMITED", retry_after_s=settings.reconnect_rate_limit_s)

    with core_db.tenant_tx(ctx.tenant_id) as conn:
        row = repos.fetch_channel_account(conn, tenant_id=ctx.tenant_id, channel_id=channel_id)
        if row is None:
            raise ApiError("NOT_FOUND")  # spec: another tenant's resource is NEVER FORBIDDEN
        if row["status"] not in _RECONNECT_ALLOWED_STATUSES:
            raise ApiError("CHANNEL_CONFLICT")
        session_id = repos.fetch_channel_session_id(conn, tenant_id=ctx.tenant_id, channel_id=channel_id)
        assert session_id is not None

        try:
            gw_resp = request.app.state.gateway_client.session_reconnect(session_id)
        except GatewayUnavailableError as exc:
            raise ApiError("GATEWAY_UNAVAILABLE") from exc
        if gw_resp.status_code >= 400:
            raise ApiError("GATEWAY_UNAVAILABLE")

    return _channel_out(row)
