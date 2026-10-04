"""core/app/marketing_ops.py - P3.4/P3.5: marketing operations shared by the operator
CLI, the rollback worker and the superadmin API (H100-H102, H104).

Deliberately NEUTRAL: it imports only app.db and app.policy - never app.workers or
app.api - so BOTH the worker layer and the HTTP layer may use it without breaking
the import-linter boundaries (api-no-workers, workers-no-http-layer). Facts that
live in the worker layer (is the template registered? is the footer an explicit
owner value?) are INJECTED by the caller as plain values.

No network, no clock of its own (`now` injected), no phone, no customer text.
`set_enabled(enabled=True)` is NOT called here - only the two human-act surfaces
may enable (S29-b): app/cli.py and app/api/routes_marketing_admin.py.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import repos_dashboard, repos_marketing, repos_outbox, repos_policy, repos_scheduler
from app.policy import warmup

# Closed vocabulary of preflight failures (printed by the CLI / returned by the API
# as machine codes, never free text).
PREFLIGHT_FAILURES = (
    "template_not_registered", "footer_not_explicit", "no_connected_channel",
    "number_not_healthy", "warmup_not_started", "warmup_too_young",
    "global_switch_off", "no_eligible_subscribers",
)


@dataclass(frozen=True)
class MarketingEnv:
    """Facts only the worker layer can see, injected into the HTTP layer at startup
    (main.py -> app.state.marketing_env) so app.api never imports app.workers."""
    template_registered: bool
    footer_explicit: bool
    min_warmup_days: int
    footer_text: str
    sample_text: str


def confirm_phrase(platform_ref: str) -> str:
    """The literal confirmation `enable` demands (H100) - typing it is the act."""
    return f"ENABLE-MARKETING {platform_ref}"


def preflight(
    conn: Any, *, tenant_id: uuid.UUID, now: datetime, template_registered: bool,
    footer_explicit: bool, min_warmup_days: int,
) -> list[str]:
    """The checks `enable` must pass (H100). Pure reads; returns the FAILED check
    names (closed vocabulary) - empty means go."""
    failed: list[str] = []
    if not template_registered:
        failed.append("template_not_registered")
    # H103: the footer must be a deliberate owner-set value, not just the code default.
    if not footer_explicit:
        failed.append("footer_not_explicit")

    channels = [c for c in repos_marketing.read_tenant_channels(conn, tenant_id=tenant_id)
                if c["status"] == "connected"]
    if not channels:
        failed.append("no_connected_channel")
    else:
        if any(c["health_state"] != "healthy" for c in channels):
            failed.append("number_not_healthy")
        started = [c["warmup_started_at"] for c in channels]
        if any(s is None for s in started):
            failed.append("warmup_not_started")
        elif not all(warmup.day_index(s, now, timezone.utc) >= min_warmup_days for s in started):
            failed.append("warmup_too_young")
        if any(
            repos_outbox.effective_switch(
                conn, tenant_id=tenant_id, channel_account_id=c["channel_id"],
                capability="marketing",
            ) == "off"
            for c in channels
        ):
            failed.append("global_switch_off")

    if repos_marketing.count_eligible_subscribers(conn, tenant_id=tenant_id) == 0:
        failed.append("no_eligible_subscribers")
    return failed


def disable_tenant(
    conn: Any, *, tenant_id: uuid.UUID, actor: str, reason: str | None,
) -> dict[str, int]:
    """H101 level 2, ONE transaction (the caller's): off + cancel the queue + give
    the slots back. Idempotent - disabling a disabled tenant just logs. utility and
    service rows are never touched."""
    repos_marketing.set_enabled(
        conn, tenant_id=tenant_id, enabled=False, actor=actor, reason=reason,
    )
    dropped = repos_policy.cancel_pending_marketing(
        conn, tenant_id=tenant_id, reason=repos_marketing.MARKETING_DISABLED_REASON,
    )
    channels = repos_policy.release_reserved_ledger(
        conn, outbox_ids=[oid for oid, _ch in dropped],
    )
    for channel_id in channels:
        repos_policy.release_send_slot(conn, channel_id=channel_id, message_class="marketing")
    jobs = repos_scheduler.cancel_pending_kind(
        conn, tenant_id=tenant_id, kind="cart_reminder",
        reason=repos_marketing.MARKETING_DISABLED_REASON,
    )
    return {"outbox_dropped": len(dropped), "slots_released": len(channels),
            "jobs_cancelled": jobs}


# --- read models for the dashboard (counts and states only - H20/H48) ----------


def _iso(value: Any) -> str | None:
    return None if value is None else str(value.isoformat())


def activation_view(conn: Any, *, tenant_id: uuid.UUID, now: datetime) -> dict[str, Any]:
    """The tenant's activation state + the live canary budget. Absence of a row is
    reported as disabled with the default cap (H100)."""
    row = repos_marketing.read_activation(conn, tenant_id=tenant_id)
    sent = repos_marketing.marketing_sent_24h(conn, tenant_id=tenant_id, now=now)
    cap = row["canary_cap_per_day"] if row else repos_marketing.DEFAULT_CANARY_CAP
    return {
        "enabled": bool(row and row["enabled"]),
        "configured": row is not None,
        "canary_cap_per_day": cap,
        "sent_24h": sent,
        "remaining_24h": max(0, cap - sent),
        "enabled_by": row["enabled_by"] if row else None,
        "enabled_at": _iso(row["enabled_at"]) if row else None,
        "disabled_at": _iso(row["disabled_at"]) if row else None,
        "updated_at": _iso(row["updated_at"]) if row else None,
    }


def channels_view(conn: Any, *, tenant_id: uuid.UUID, now: datetime) -> list[dict[str, Any]]:
    """Per WhatsApp channel: connection status, number_health state and warm-up age.
    Identifiers/states only - never a phone number."""
    out = []
    for c in repos_marketing.read_tenant_channels(conn, tenant_id=tenant_id):
        started = c["warmup_started_at"]
        out.append({
            "channel_id": str(c["channel_id"]), "status": c["status"],
            "health_state": c["health_state"],
            "warmup_day": None if started is None else warmup.day_index(started, now, timezone.utc),
        })
    return out


def tenant_summary(conn: Any, *, tenant_id: uuid.UUID, now: datetime) -> dict[str, Any]:
    """One row of the superadmin table / the merchant's own overview."""
    act = activation_view(conn, tenant_id=tenant_id, now=now)
    chans = channels_view(conn, tenant_id=tenant_id, now=now)
    return {
        "activation": act,
        "channels": {"total": len(chans), "connected": sum(1 for c in chans if c["status"] == "connected")},
        "open_carts": repos_dashboard.open_cart_count(conn, tenant_id=tenant_id),
        "eligible_subscribers": repos_marketing.count_eligible_subscribers(conn, tenant_id=tenant_id),
    }


def tenant_metrics(conn: Any, *, tenant_id: uuid.UUID, days: int, now: datetime) -> dict[str, Any]:
    """Messages / carts / opt-outs over the last `days` UTC days (zero-filled)."""
    today = now.astimezone(timezone.utc).date()
    labels = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    series = {d: {"marketing": 0, "utility": 0, "service": 0, "optouts": 0, "optins": 0} for d in labels}

    for day, cls, n in repos_dashboard.sent_by_day(conn, tenant_id=tenant_id, days=days):
        if day in series and cls in series[day]:
            series[day][cls] += n
    for day, granted, n in repos_dashboard.consent_events_by_day(conn, tenant_id=tenant_id, days=days):
        if day in series:
            series[day]["optins" if granted else "optouts"] += n

    carts = repos_dashboard.cart_counts(conn, tenant_id=tenant_id, days=days)
    finished = carts["recovered"] + carts["reminded"] + carts["expired"]
    marketing_sent = sum(s["marketing"] for s in series.values())
    optouts = sum(s["optouts"] for s in series.values())
    status_counts = repos_dashboard.outbox_status_counts(conn, tenant_id=tenant_id, days=days)
    mk = status_counts.get("marketing", {})
    return {
        "days": days,
        "series": [{"date": d, **series[d]} for d in labels],
        "totals": {
            "sent_marketing": marketing_sent,
            "sent_utility": sum(s["utility"] for s in series.values()),
            "sent_service": sum(s["service"] for s in series.values()),
            "optouts": optouts,
            "optins": sum(s["optins"] for s in series.values()),
            "marketing_dropped_policy": mk.get("dropped_policy", 0),
            "marketing_failed": mk.get("failed", 0),
            "marketing_pending": mk.get("pending", 0),
        },
        "carts": {**carts,
                  "recovery_rate_pct": round(100 * carts["recovered"] / finished, 1) if finished else None},
    }
