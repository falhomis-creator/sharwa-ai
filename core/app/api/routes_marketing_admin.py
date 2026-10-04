"""core/app/api/routes_marketing_admin.py - P3.5 (H104): the marketing control
plane as HTTP, for the dashboard.

  superadmin (platform_admin ONLY):
    GET  /v1/admin/marketing/tenants                   all tenants + activation state
    GET  /v1/admin/marketing/tenants/{ref}             one tenant: state, channels, history, preflight
    GET  /v1/admin/marketing/metrics                   platform-wide + per-tenant metrics
    POST /v1/admin/marketing/tenants/{ref}/enable      cap + reason + literal confirm phrase
    POST /v1/admin/marketing/tenants/{ref}/disable     H101 level 2 (queue cancelled, slots returned)
    POST /v1/admin/marketing/tenants/{ref}/set-cap     canary cap (never enables anyone)
  tenant (merchant_admin | platform_admin) - READ-ONLY, the token's own tenant:
    GET  /v1/marketing/overview
    GET  /v1/marketing/metrics

Security model (the point of this module):
  * Mutations are POST (the console CORS allowlist has no PUT/DELETE) and carry a
    machine error envelope only (H3).
  * The target tenant of an ADMIN route comes from the path `{ref}` - the one
    deliberate exception to H2 ("tenant only from the token"), gated to
    platform_admin and resolved server-side by app.resolve_tenant. It buys no
    bypass: every read/write then runs inside tenant_tx(target), i.e. UNDER RLS
    exactly like the owner-scoped role would. The cross-tenant list uses ONE
    SECURITY DEFINER function (0018) that returns identifiers only.
  * `enable` needs the SAME gates as the CLI: literal confirm phrase, the preflight
    list, a reason. The actor recorded is the JWT `sub` (never a body field).
    Every mutation writes audit_log AND the append-only marketing_activation_log
    in the same transaction.
  * Counts and states only (H20/H48): no phone, no customer id, no message text.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, Field

from app import db as core_db
from app import marketing_ops
from app.api.deps import AuthContext, require_role
from app.api.errors import ApiError
from app.db import repos, repos_dashboard, repos_marketing
from app.marketing_ops import MarketingEnv

router = APIRouter()

_REF_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
_ACTOR_PREFIX = "dashboard:"


class EnableIn(BaseModel):
    cap: int = Field(strict=True, ge=1, le=500)
    reason: str = Field(min_length=1, max_length=200)
    confirm: str = Field(min_length=1, max_length=200)


class DisableIn(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


class SetCapIn(BaseModel):
    cap: int = Field(strict=True, ge=1, le=500)
    reason: str = Field(min_length=1, max_length=200)


def _env(request: Request) -> MarketingEnv:
    env: MarketingEnv = request.app.state.marketing_env
    return env


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


def _target(ref: str) -> tuple[Any, str]:
    """platform_ref -> (tenant_id, status) via the SECURITY DEFINER resolver."""
    if not _REF_RE.match(ref):
        raise ApiError("VALIDATION_FAILED")
    with core_db.system_tx() as conn:
        resolved = repos.resolve_tenant(conn, ref)
    if resolved is None:
        raise ApiError("NOT_FOUND")
    return resolved


def _global_marketing_switch() -> str:
    with core_db.system_tx() as conn:
        state = repos.fetch_kill_switch_state(
            conn, scope="global", capability="marketing", scope_id=None,
        )
    return "on" if state is None else str(state)


def _audit(conn: Any, ctx: AuthContext, request: Request, *, tenant_id: Any, action: str,
           ref: str, before: dict[str, Any] | None, after: dict[str, Any]) -> None:
    """One audit_log row for the TARGET tenant (RLS: conn is tenant_tx(target))."""
    repos.insert_audit_log(
        conn, tenant_id=str(tenant_id), actor_sub=ctx.principal.sub,
        actor_role=ctx.principal.role, action=action, target=f"tenant:{ref}",
        before=None if before is None else json.dumps(before),
        after=json.dumps(after), request_id=_request_id(request),
    )


# ---------------------------------------------------------------- superadmin reads


@router.get("/v1/admin/marketing/tenants")
def admin_list_tenants(
    ctx: AuthContext = Depends(require_role("platform_admin")),
) -> dict[str, Any]:
    now = _now()
    with core_db.system_tx() as conn:
        tenants = repos_dashboard.list_tenants_admin(conn)
    rows: list[dict[str, Any]] = []
    for t in tenants:
        with core_db.tenant_tx(t["id"]) as conn:
            summary = marketing_ops.tenant_summary(conn, tenant_id=t["id"], now=now)
        rows.append({"ref": t["ref"], "name": t["name"], "status": t["status"],
                     "timezone": t["timezone"], **summary})
    totals = {
        "tenants": len(rows),
        "enabled": sum(1 for r in rows if r["activation"]["enabled"]),
        "sent_24h": sum(r["activation"]["sent_24h"] for r in rows),
        "open_carts": sum(r["open_carts"] for r in rows),
    }
    return {"tenants": rows, "totals": totals, "global_marketing_switch": _global_marketing_switch()}


@router.get("/v1/admin/marketing/tenants/{ref}")
def admin_tenant_detail(
    request: Request,
    ref: str = Path(),
    ctx: AuthContext = Depends(require_role("platform_admin")),
) -> dict[str, Any]:
    env = _env(request)
    tenant_id, status = _target(ref)
    now = _now()
    with core_db.tenant_tx(tenant_id) as conn:
        header = repos_dashboard.tenant_header(conn, tenant_id=tenant_id)
        summary = marketing_ops.tenant_summary(conn, tenant_id=tenant_id, now=now)
        channels = marketing_ops.channels_view(conn, tenant_id=tenant_id, now=now)
        failed = marketing_ops.preflight(
            conn, tenant_id=tenant_id, now=now, template_registered=env.template_registered,
            footer_explicit=env.footer_explicit, min_warmup_days=env.min_warmup_days,
        )
        log = repos_marketing.read_log(conn, tenant_id=tenant_id)
        audit = repos_dashboard.recent_audit(conn, tenant_id=tenant_id)
        counts = repos_marketing.preview_cart_counts(conn, tenant_id=tenant_id)
    return {
        "ref": ref, "name": None if header is None else header["name"], "status": status,
        "timezone": None if header is None else header["timezone"],
        **summary,
        "channel_list": channels,
        "preflight_failed": failed,
        "cart_preview": counts,
        "confirm_phrase": marketing_ops.confirm_phrase(ref),
        "sample_text": env.sample_text,
        "global_marketing_switch": _global_marketing_switch(),
        "history": [{"action": a, "actor": actor, "reason": reason, "at": at.isoformat()}
                    for a, actor, reason, at in reversed(log[-50:])],
        "audit": audit,
    }


@router.get("/v1/admin/marketing/metrics")
def admin_metrics(
    days: int = Query(default=7, ge=1, le=90),
    ctx: AuthContext = Depends(require_role("platform_admin")),
) -> dict[str, Any]:
    now = _now()
    with core_db.system_tx() as conn:
        tenants = repos_dashboard.list_tenants_admin(conn)
    per: list[dict[str, Any]] = []
    for t in tenants:
        with core_db.tenant_tx(t["id"]) as conn:
            m = marketing_ops.tenant_metrics(conn, tenant_id=t["id"], days=days, now=now)
        per.append({"ref": t["ref"], "name": t["name"], **m})
    return {"days": days, **_aggregate(per), "tenants": [
        {"ref": p["ref"], "name": p["name"], "totals": p["totals"], "carts": p["carts"]} for p in per
    ]}


def _aggregate(per: list[dict[str, Any]]) -> dict[str, Any]:
    """Platform-wide sums of the per-tenant (RLS-scoped) results."""
    if not per:
        return {"series": [], "totals": {}, "carts": {}}
    series = []
    for i, day in enumerate(per[0]["series"]):
        merged: dict[str, Any] = {"date": day["date"]}
        for key in ("marketing", "utility", "service", "optouts", "optins"):
            merged[key] = sum(p["series"][i][key] for p in per)
        series.append(merged)
    totals: dict[str, int] = {}
    for p in per:
        for k, v in p["totals"].items():
            totals[k] = totals.get(k, 0) + int(v)
    carts: dict[str, Any] = {k: sum(p["carts"][k] for p in per)
                             for k in ("open", "recovered", "cleared", "reminded", "expired")}
    finished = carts["recovered"] + carts["reminded"] + carts["expired"]
    carts["recovery_rate_pct"] = round(100 * carts["recovered"] / finished, 1) if finished else None
    return {"series": series, "totals": totals, "carts": carts}


# ---------------------------------------------------------------- superadmin writes


@router.post("/v1/admin/marketing/tenants/{ref}/enable")
def admin_enable(
    request: Request,
    body: EnableIn,
    ref: str = Path(),
    ctx: AuthContext = Depends(require_role("platform_admin")),
) -> dict[str, Any]:
    env = _env(request)
    tenant_id, status = _target(ref)
    if status == "suspended":
        raise ApiError("TENANT_SUSPENDED")
    # H100: typing the literal phrase IS the act - same phrase as the CLI.
    if body.confirm != marketing_ops.confirm_phrase(ref):
        raise ApiError("VALIDATION_FAILED", details={"field": "confirm"})
    now = _now()
    actor = f"{_ACTOR_PREFIX}{ctx.principal.sub}"
    with core_db.tenant_tx(tenant_id) as conn:
        before = repos_marketing.read_activation(conn, tenant_id=tenant_id)
        failed = marketing_ops.preflight(
            conn, tenant_id=tenant_id, now=now, template_registered=env.template_registered,
            footer_explicit=env.footer_explicit, min_warmup_days=env.min_warmup_days,
        )
        if failed:
            # The refused attempt is itself auditable (committed); the activation is not.
            _audit(conn, ctx, request, tenant_id=tenant_id, action="marketing.enable_refused", ref=ref,
                   before=None, after={"failed": failed})
        else:
            repos_marketing.set_enabled(
                conn, tenant_id=tenant_id, enabled=True, actor=actor,
                reason=body.reason, cap=body.cap,
            )
            _audit(conn, ctx, request, tenant_id=tenant_id, action="marketing.enable", ref=ref,
                   before=None if before is None else {"enabled": before["enabled"],
                                                       "cap": before["canary_cap_per_day"]},
                   after={"enabled": True, "cap": body.cap})
    if failed:
        raise ApiError("PRECONDITION_FAILED", details={"failed": failed})
    return {"ref": ref, "enabled": True, "canary_cap_per_day": body.cap}


@router.post("/v1/admin/marketing/tenants/{ref}/disable")
def admin_disable(
    request: Request,
    body: DisableIn,
    ref: str = Path(),
    ctx: AuthContext = Depends(require_role("platform_admin")),
) -> dict[str, Any]:
    tenant_id, _status = _target(ref)  # a suspended tenant can still be switched OFF
    with core_db.tenant_tx(tenant_id) as conn:
        before = repos_marketing.read_activation(conn, tenant_id=tenant_id)
        result = marketing_ops.disable_tenant(
            conn, tenant_id=tenant_id, actor=f"{_ACTOR_PREFIX}{ctx.principal.sub}",
            reason=body.reason,
        )
        _audit(conn, ctx, request, tenant_id=tenant_id, action="marketing.disable", ref=ref,
               before=None if before is None else {"enabled": before["enabled"]},
               after={"enabled": False, **result})
    return {"ref": ref, "enabled": False, **result}


@router.post("/v1/admin/marketing/tenants/{ref}/set-cap")
def admin_set_cap(
    request: Request,
    body: SetCapIn,
    ref: str = Path(),
    ctx: AuthContext = Depends(require_role("platform_admin")),
) -> dict[str, Any]:
    tenant_id, status = _target(ref)
    if status == "suspended":
        raise ApiError("TENANT_SUSPENDED")
    with core_db.tenant_tx(tenant_id) as conn:
        before = repos_marketing.read_activation(conn, tenant_id=tenant_id)
        repos_marketing.set_cap(
            conn, tenant_id=tenant_id, cap=body.cap,
            actor=f"{_ACTOR_PREFIX}{ctx.principal.sub}", reason=body.reason,
        )
        _audit(conn, ctx, request, tenant_id=tenant_id, action="marketing.set_cap", ref=ref,
               before=None if before is None else {"cap": before["canary_cap_per_day"]},
               after={"cap": body.cap})
    return {"ref": ref, "canary_cap_per_day": body.cap}


# ---------------------------------------------------------------- tenant (read-only)


@router.get("/v1/marketing/overview")
def tenant_overview(
    ctx: AuthContext = Depends(require_role("merchant_admin", "platform_admin")),
) -> dict[str, Any]:
    now = _now()
    with core_db.tenant_tx(ctx.tenant_id) as conn:
        header = repos_dashboard.tenant_header(conn, tenant_id=ctx.tenant_id)
        summary = marketing_ops.tenant_summary(conn, tenant_id=ctx.tenant_id, now=now)
        channels = marketing_ops.channels_view(conn, tenant_id=ctx.tenant_id, now=now)
    return {"name": None if header is None else header["name"], **summary, "channel_list": channels,
            "global_marketing_switch": _global_marketing_switch()}


@router.get("/v1/marketing/metrics")
def tenant_metrics(
    days: int = Query(default=7, ge=1, le=90),
    ctx: AuthContext = Depends(require_role("merchant_admin", "platform_admin")),
) -> dict[str, Any]:
    with core_db.tenant_tx(ctx.tenant_id) as conn:
        return marketing_ops.tenant_metrics(conn, tenant_id=ctx.tenant_id, days=days, now=_now())
