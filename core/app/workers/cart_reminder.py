"""core/app/workers/cart_reminder.py - the cart_reminder handler (H89/H92).

Runs INSIDE the job's tenant transaction with the injected clock (H84). Check
order (tested as a table):
  1. cart FOR UPDATE - gone/final => Cancel('cart_closed')
  2. not actually due yet (newer activity landed between schedule and run) =>
     Defer(last_activity + DELAY) - NO attempt consumed (H88)
  3. no ai_core conversation for the customer => Cancel('no_conversation')
  4. template not registered (the case in ALL of P3.2: marketing is dark) =>
     Cancel('template_not_registered') - no outbox row, no effect
  5. registered (tests inject it in-test only) => enqueue via proactive.py ONLY
     (never insert_outbox, S22) with idempotency_key cart:{id}:stage1
  6. carts.status ⇐ 'reminded' in the SAME transaction, then Done.

The send-time gate (policy_gate) is untouched and runs later, as its own
independent verdict (H76: two verdicts, not one).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import psycopg

from app.db import repos_carts
from app.obs import metrics
from app.workers import config, proactive, verify
from app.workers.config import WorkerSettings
from app.workers.job_results import Cancel, Defer, Done

TEMPLATE_ID = "cart_reminder"


def _merge_fields(settings: WorkerSettings, snapshot: dict) -> dict[str, str]:
    """Closed merge whitelist (OQ-P3-08 default: first item title truncated +
    item count ONLY - no free text from the store event)."""
    items = snapshot.get("items") or []
    first_title = ""
    if items and isinstance(items[0], dict):
        first_title = str(items[0].get("title", ""))[: settings.cart_item_title_max]
    return {"title": first_title, "item_count": str(int(snapshot.get("item_count", 0)))}


def handle_cart_reminder(
    settings: WorkerSettings, conn: psycopg.Connection, *,
    tenant_id, payload: dict, now: datetime,
):
    platform_cart_id = str(payload.get("cart_id", ""))
    cart = repos_carts.lock_cart(conn, tenant_id=tenant_id, platform_cart_id=platform_cart_id)
    if cart is None or cart["status"] != "open":
        return Cancel("cart_closed")

    due_at = cart["last_activity_at"] + timedelta(hours=settings.cart_reminder_delay_h)
    if now < due_at:
        # Activity landed between scheduling and execution: push out, free.
        return Defer(due_at)

    conversation_id = repos_carts.latest_ai_core_conversation(
        conn, tenant_id=tenant_id, customer_id=cart["customer_id"],
    )
    if conversation_id is None:
        return Cancel("no_conversation")

    entry = config.proactive_template(TEMPLATE_ID)
    if entry is None:
        # "Wired but dark": no marketing template is registered in P3.2, so the
        # engine runs and cancels - a clean shadow, never a message.
        return Cancel("template_not_registered")

    _meta, _text, merge_keys = entry
    merge = {k: v for k, v in _merge_fields(settings, cart["snapshot"] or {}).items()
             if k in merge_keys}
    proactive.enqueue_proactive(
        conn, settings=settings, rules=verify.build_rules(settings),
        tenant_id=tenant_id, conversation_id=conversation_id,
        template_id=TEMPLATE_ID, merge=merge,
        idempotency_key=f"cart:{platform_cart_id}:stage1",
    )
    repos_carts.mark_cart_reminded(conn, tenant_id=tenant_id, platform_cart_id=platform_cart_id)
    metrics.cart_events_total.labels("cart.updated", "reminded").inc()
    return Done()
