"""core/app/workers/marketing.py - P3.4 operator-side coordinator (H100-H102).

Not a poller and not a sender: the three things the operator CLI needs that are
more than one repo call -

  preflight()      the checks `enable` must pass (H100). Pure reads; returns the
                   list of FAILED check names (closed vocabulary) - empty = go.
  disable_tenant() H101 level 2: ONE transaction that flips the tenant off, drops
                   its pending marketing outbox rows, gives their reserved slots
                   back (H85) and cancels its pending cart_reminder jobs.
                   utility/service are never touched.
  preview()        a dry run - COUNTS and a synthetic-data render (H20/H48).

No network, no clock of its own (`now` injected), no phone, no customer text.
`set_enabled(enabled=True)` is deliberately NOT called here - only app/cli.py
may enable (S29-b): the enable is a human act, the rollback is not.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.db import repos_marketing, repos_outbox, repos_policy, repos_scheduler
from app.policy import warmup
from app.workers import cart_reminder, config, proactive
from app.workers.config import WorkerSettings

TEMPLATE_ID = cart_reminder.TEMPLATE_ID

# Closed vocabulary of preflight failures (printed by the CLI, never free text).
PREFLIGHT_FAILURES = (
    "template_not_registered", "footer_not_explicit", "no_connected_channel",
    "number_not_healthy", "warmup_not_started", "warmup_too_young",
    "global_switch_off", "no_eligible_subscribers",
)


def confirm_phrase(platform_ref: str) -> str:
    """The literal confirmation `enable` demands (H100) - typing it is the act."""
    return f"ENABLE-MARKETING {platform_ref}"


def preflight(
    conn: Any, *, tenant_id: uuid.UUID, settings: WorkerSettings,
    now: datetime, footer_explicit: bool,
) -> list[str]:
    failed: list[str] = []
    if config.proactive_template(TEMPLATE_ID) is None:
        failed.append("template_not_registered")
    # H103: the footer must be a deliberate, owner-set value - not just the
    # code default. `footer_explicit` = MARKETING_FOOTER_AR is set in the env.
    if not footer_explicit or not settings.marketing_footer_ar.strip():
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
        elif not all(
            warmup.day_index(s, now, timezone.utc) >= settings.marketing_min_warmup_days
            for s in started
        ):
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
    """H101 level 2, ONE transaction (the caller's): off + cancel the queue +
    give the slots back. Idempotent - disabling a disabled tenant just logs."""
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


def preview(
    conn: Any, *, tenant_id: uuid.UUID, settings: WorkerSettings,
) -> dict[str, object]:
    """Dry run: counts + the template rendered with SYNTHETIC data. Writes nothing."""
    counts = repos_marketing.preview_cart_counts(conn, tenant_id=tenant_id)
    phrase = cart_reminder.items_phrase("عنوان تجريبي", 3)
    text, _meta = proactive.render_text(TEMPLATE_ID, {"items_phrase": phrase})
    return {**counts, "sample": f"{text}\n{settings.marketing_footer_ar}"}
