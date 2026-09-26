"""core/app/api/routes_killswitches.py

GET /v1/kill-switches, PUT /v1/kill-switches/{capability} (merchant_admin,
tenant/channel scope only), PUT /v1/admin/kill-switches/global/{capability}
(platform_admin only). Every write goes through app.set_kill_switch (the only
path sharwa_app may use to touch kill_switches - see 0002_p0_api.sql), writes
audit_log in the SAME transaction, then publishes to redis-cache and verifies
the publish before returning success (spec: "لا يُعرَض نجاح إلا بعد أن يتحقق
الـAPI من نشر الحالة").
"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app import db as core_db
from app.api.deps import AuthContext, authenticate, require_role
from app.api.errors import ApiError
from app.db import repos
from app.killswitch.redis_sync import RedisSync, get_redis_sync

router = APIRouter()

CAPABILITIES = ("*", "ai_reply", "marketing")


class KillSwitchOut(BaseModel):
    capability: str
    scope: str
    state: str
    reason: str | None
    set_by: str
    changed_by_platform: bool


class SetKillSwitchIn(BaseModel):
    scope: str = Field(pattern="^(tenant|channel_account)$")
    channel_id: uuid.UUID | None = None
    state: str = Field(pattern="^(on|degraded|off)$")
    reason: str | None = None


@router.get("/v1/kill-switches")
def list_kill_switches(ctx: AuthContext = Depends(authenticate)) -> list[KillSwitchOut]:
    with core_db.tenant_tx(ctx.tenant_id) as conn:
        rows = repos.call_list_kill_switches(conn)
    out = []
    for rec in rows:
        # kill_switches' own NOT NULL columns (capability, scope, state, set_by)
        # are guaranteed present by the schema - indexing (not .get()) both
        # documents that invariant and gives mypy a plain Any rather than the
        # Any | None a dict.get() default would carry.
        out.append(KillSwitchOut(
            capability=str(rec["capability"]),
            scope=str(rec["scope"]),
            state=str(rec["state"]),
            reason=None if rec.get("reason") is None else str(rec["reason"]),
            set_by=str(rec["set_by"]),
            changed_by_platform=rec["scope"] == "global",
        ))
    return out


@router.put("/v1/kill-switches/{capability}")
def set_kill_switch(
    request: Request,
    capability: str,
    body: SetKillSwitchIn,
    ctx: AuthContext = Depends(require_role("merchant_admin")),
    redis_sync: RedisSync = Depends(get_redis_sync),
) -> dict[str, str]:
    if capability not in CAPABILITIES:
        raise ApiError("VALIDATION_FAILED")
    request_id = getattr(request.state, "request_id", "unknown")

    channel_id = str(body.channel_id) if body.channel_id else None
    with core_db.tenant_tx(ctx.tenant_id) as conn:
        before_state = repos.fetch_kill_switch_state(
            conn, scope=body.scope, capability=capability, scope_id=channel_id,
        )
        result_row = repos.call_set_kill_switch(
            conn, role=ctx.principal.role, scope=body.scope, scope_id=channel_id,
            capability=capability, state=body.state, reason=body.reason, sub=ctx.principal.sub,
        )
        repos.insert_audit_log(
            conn, tenant_id=str(ctx.tenant_id), actor_sub=ctx.principal.sub,
            actor_role=ctx.principal.role, action="kill_switch.set",
            target=f"{body.scope}:{capability}",
            before=json.dumps({"state": before_state}) if before_state is not None else None,
            after=json.dumps({"state": body.state}), request_id=request_id,
        )

    redis_sync.publish_and_verify(scope=body.scope, tenant_id=str(ctx.tenant_id),
                                   channel_id=str(body.channel_id) if body.channel_id else None,
                                   capability=capability, state=body.state)
    # Echo back the state app.set_kill_switch actually persisted (real DB
    # truth), not just the request body - trivially the same value here since
    # the function either applies body.state verbatim or raises, but this is
    # the honest source of truth rather than an unchecked echo.
    return {"capability": capability, "state": str(result_row["state"])}


@router.put("/v1/admin/kill-switches/global/{capability}")
def set_global_kill_switch(
    request: Request,
    capability: str,
    body: SetKillSwitchIn,
    ctx: AuthContext = Depends(require_role("platform_admin")),
    redis_sync: RedisSync = Depends(get_redis_sync),
) -> dict[str, str]:
    if capability not in CAPABILITIES:
        raise ApiError("VALIDATION_FAILED")
    request_id = getattr(request.state, "request_id", "unknown")

    with core_db.system_tx() as conn:
        before_state = repos.fetch_kill_switch_state(
            conn, scope="global", capability=capability, scope_id=None,
        )
        result_row = repos.call_set_kill_switch(
            conn, role=ctx.principal.role, scope="global", scope_id=None,
            capability=capability, state=body.state, reason=body.reason, sub=ctx.principal.sub,
        )
        # Global switches are platform-wide, not tenant-scoped, but audit_log
        # is RLS'd by tenant_id (0002_p0_api.sql) - system_tx() carries no
        # tenant context, so this INSERT relies on sharwa_system's own grant
        # on audit_log rather than the tenant_isolation policy; tenant_id is
        # recorded as NULL to mean "platform-wide action, not one tenant's".
        repos.insert_audit_log(
            conn, tenant_id=None, actor_sub=ctx.principal.sub, actor_role=ctx.principal.role,
            action="kill_switch.set_global", target=f"global:{capability}",
            before=json.dumps({"state": before_state}) if before_state is not None else None,
            after=json.dumps({"state": body.state}), request_id=request_id,
        )

    redis_sync.publish_and_verify(scope="global", tenant_id=None, channel_id=None,
                                   capability=capability, state=body.state)
    return {"capability": capability, "state": str(result_row["state"])}
