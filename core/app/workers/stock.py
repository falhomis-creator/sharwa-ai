"""core/app/workers/stock.py - the back-in-stock coordinator (P2.3, H40/H69-H73).

Three phases, mirroring turn.py/orders.py. The platform read happens OUTSIDE any
transaction (H40); the allocation + notification happen in ONE short transaction
(PROMPT §5.2 "المصيدة"): a held unit without a notification is a double loss, and
a notification written in a second transaction can leave a hold the customer
never learns about. The outbox write is a single INSERT with no network call, so
the stock_levels serialization point stays short (H69: the data row itself is the
serialization point - never Redis).
"""
from __future__ import annotations

import datetime
import time
import uuid
from typing import Any

from app import db as core_db
from app.channels.commerce_client import CommerceClientError, CommerceUnavailableError
from app.db import repos_catalog
from app.db import repos_consent
from app.db import repos_outbox
from app.db import repos_stock
from app.obs import metrics
from app.tools import join_waitlist as join_waitlist_tool
from app.tools.registry import ToolContext
from app.workers import proactive
from app.workers.config import WorkerSettings


def _age_seconds(observed_at: Any) -> float:
    """Seconds since the platform's own observation timestamp; +inf when absent
    or unparseable (=> treated as stale, H73: never guess availability)."""
    if not observed_at:
        return float("inf")
    try:
        dt = datetime.datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds()


def join_waitlist(
    conn: Any,
    settings: WorkerSettings,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    bodies: tuple[str, ...],
) -> join_waitlist_tool.JoinWaitlistDecision:
    """The join flow (§5.1), run on the CALLER's open connection (inside the turn
    write-phase transaction, like address.resolve_and_persist): read slots
    (last_shown_product_ids) -> pure tool picks the product -> its single variant
    (F-P4-07) -> read-before-write
    dedupe + insert. A duplicate is recorded as a rejection, never a second live
    row (P2.3 §3); the per-customer ceiling is enforced, not just written (§7)."""
    customer_id = repos_outbox.customer_id_for_conversation(conn, conversation_id)
    slots = repos_stock.read_conversation_slots(conn, conversation_id=conversation_id)

    last_shown = tuple(str(x) for x in (slots.get("last_shown_product_ids") or []))
    ctx = ToolContext(
        tenant_id=tenant_id, conversation_id=conversation_id, tenant_ref="",
        channel_phone_e164=None, message_texts=bodies, commerce=None, settings=settings,
        order_ref="", phone_candidates=(), path="other_number",
        last_shown_product_ids=last_shown,
    )
    picked = join_waitlist_tool.run(ctx)
    if picked.kind != "joined" or picked.platform_product_id is None or customer_id is None:
        metrics.waitlist_entries_total.labels("no_variant").inc()
        return join_waitlist_tool.JoinWaitlistDecision(kind="no_variant")
    # F-P4-07: the slot holds PRODUCT ids; a waitlist entry needs ONE variant.
    # Zero or several variants => no_variant (never guess which size/colour).
    variants = repos_stock.variant_ids_for_product(
        conn, tenant_id=tenant_id, platform_product_id=picked.platform_product_id,
    )
    if len(variants) != 1:
        metrics.waitlist_entries_total.labels("no_variant").inc()
        return join_waitlist_tool.JoinWaitlistDecision(kind="no_variant")

    variant = variants[0]
    decision = join_waitlist_tool.JoinWaitlistDecision(
        kind="joined", platform_variant_id=variant, platform_product_id=picked.platform_product_id,
    )
    if repos_stock.has_active_waitlist(
        conn, tenant_id=tenant_id, customer_id=customer_id, platform_variant_id=variant,
    ):
        metrics.waitlist_entries_total.labels("duplicate").inc()
        return join_waitlist_tool.JoinWaitlistDecision(kind="already_waiting", platform_variant_id=variant)
    if repos_stock.count_active_waitlists(
        conn, tenant_id=tenant_id, customer_id=customer_id,
    ) >= settings.stock_max_waitlist_per_customer:
        metrics.waitlist_entries_total.labels("cap").inc()
        return join_waitlist_tool.JoinWaitlistDecision(kind="unavailable")
    entry_id = repos_stock.insert_waitlist_entry(
        conn, tenant_id=tenant_id, customer_id=customer_id,
        conversation_id=conversation_id, platform_variant_id=variant,
    )
    # H50 / D3 / OQ-P3-12 (owner-approved default): the join is an explicit
    # act - the back_in_stock consent is written HERE (the coordinator), in
    # the same join transaction, never in the pure join_waitlist tool, and it
    # lifts exactly the optout-caused back_in_stock suppression (otherwise the
    # stock_joined promise would be a lie). Marketing stays untouched.
    action = repos_consent.record_waitlist_join(
        conn, tenant_id=tenant_id, customer_id=customer_id, entry_id=entry_id,
    )
    if action == "granted":
        metrics.consent_events_total.labels("granted", "waitlist_join").inc()
    elif action == "granted_and_lifted":
        metrics.consent_events_total.labels("granted", "waitlist_join").inc()
        metrics.consent_events_total.labels("lifted", "waitlist_join").inc()
    else:  # noop: already granted by an earlier join
        metrics.consent_events_total.labels("noop", "waitlist_join").inc()
    metrics.waitlist_entries_total.labels("joined").inc()
    return decision


def sweep_once(commerce: Any, settings: WorkerSettings, rules: Any) -> None:
    """One sweep cycle (§5.2), run by the realtime worker's daemon thread."""
    metrics.stock_sweep_runs_total.inc()
    with core_db.system_tx() as conn:
        tenants = [(r[0], r[1]) for r in repos_catalog.list_catalog_sync_state(conn)]
    for tenant_id, platform_ref in tenants:
        if not platform_ref:
            continue
        with core_db.tenant_tx(tenant_id) as conn:
            variants = repos_stock.list_waiting_variants(
                conn, tenant_id=tenant_id, limit=settings.stock_max_variants_per_cycle,
            )
        for variant in variants:
            _sweep_variant(
                commerce, settings, rules, tenant_id=tenant_id,
                platform_ref=platform_ref, variant=variant,
            )


def _sweep_variant(
    commerce: Any, settings: WorkerSettings, rules: Any, *,
    tenant_id: uuid.UUID, platform_ref: str, variant: str,
) -> None:
    # Step 1: platform observation, OUTSIDE any transaction (H40).
    try:
        observation = commerce.get_stock_observation(tenant_ref=platform_ref, platform_variant_id=variant)
    except (CommerceUnavailableError, CommerceClientError):
        return
    if observation is None:
        return
    available = observation.get("available")
    if not isinstance(available, int) or isinstance(available, bool) or available < 0:
        return
    # Step 2: staleness (H73): an observation older than the written ceiling must
    # neither allocate nor notify.
    if _age_seconds(observation.get("observed_at")) > settings.stock_observation_max_age_s:
        metrics.stock_observation_stale_total.inc()
        return
    # Step 3 + 4: expire -> allocate -> notify, in ONE short transaction.
    with core_db.tenant_tx(tenant_id) as conn:
        _expire_allocate_notify(
            conn, settings, rules, tenant_id=tenant_id, variant=variant, available=available,
        )


def _expire_allocate_notify(
    conn: Any, settings: WorkerSettings, rules: Any, *,
    tenant_id: uuid.UUID, variant: str, available: int,
) -> None:
    # Notify customers whose hold just expired (H71), then release + promote.
    for entry_id in repos_stock.list_expired_held_entry_ids(conn, platform_variant_id=variant):
        target = repos_stock.fetch_notify_target(conn, waitlist_entry_id=entry_id)
        if target is None:
            continue
        _insert_notice(
            conn, settings, rules, tenant_id=tenant_id, entry_id=entry_id, target=target,
            template_id="stock_hold_expired",
        )
        metrics.stock_holds_total.labels("expired").inc()

    repos_stock.expire_holds(conn, platform_variant_id=variant)

    started = time.monotonic()
    holds = repos_stock.allocate_holds(
        conn, platform_variant_id=variant, available=available, ttl_s=settings.stock_hold_ttl_s,
    )
    metrics.stock_allocation_duration_seconds.observe(time.monotonic() - started)

    title = repos_stock.fetch_variant_title(
        conn, tenant_id=tenant_id, platform_variant_id=variant,
    ) or ""
    for hold in holds:
        target = repos_stock.fetch_notify_target(conn, waitlist_entry_id=hold["waitlist_entry_id"])
        if target is None:
            continue
        _insert_notice(
            conn, settings, rules, tenant_id=tenant_id, entry_id=hold["waitlist_entry_id"],
            target=target, template_id="stock_available", merge={"title": title},
        )
        metrics.stock_holds_total.labels("held").inc()


def _insert_notice(
    conn: Any, settings: WorkerSettings, rules: Any, *,
    tenant_id: uuid.UUID, entry_id: uuid.UUID, target: dict[str, Any],
    template_id: str, merge: dict[str, str] | None = None,
) -> None:
    """F-P3-03: the back-in-stock notice is now a PROACTIVE (automation) send
    through the gate, not a service row. One INSERT, no network - keeps the
    allocation transaction short (H69 unchanged)."""
    proactive.enqueue_proactive(
        conn, settings=settings, rules=rules,
        tenant_id=tenant_id, conversation_id=target["conversation_id"],
        template_id=template_id, merge=merge,
        idempotency_key=f"stock:{entry_id}:{template_id}",
    )
