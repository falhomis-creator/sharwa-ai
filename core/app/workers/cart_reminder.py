"""core/app/workers/cart_reminder.py - the cart_reminder handler (H89/H92).

Runs INSIDE the job's tenant transaction with the injected clock (H84). Check
order (tested as a table):
  1. cart FOR UPDATE - gone/final => Cancel('cart_closed')
  2. not actually due yet (newer activity landed between schedule and run) =>
     Defer(last_activity + DELAY) - NO attempt consumed (H88)
  3. no ai_core conversation for the customer => Cancel('no_conversation')
  4. template not registered => Cancel('template_not_registered') - no outbox
     row, no effect. P3.4: tenant not enabled for marketing (H100, absence =
     disabled) => Cancel('marketing_disabled') (revivable, the cart stays open)
  5. registered (tests inject it in-test only) => enqueue via proactive.py ONLY
     (never insert_outbox, S22) with idempotency_key cart:{id}:stage1
  6. carts.status ⇐ 'reminded' in the SAME transaction, then Done.

The send-time gate (policy_gate) is untouched and runs later, as its own
independent verdict (H76: two verdicts, not one).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.db import repos_carts, repos_marketing
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
    item_count = int(snapshot.get("item_count", 0))
    return {
        "title": first_title, "item_count": str(item_count),
        "items_phrase": items_phrase(first_title, item_count),
    }


def items_phrase(title: str, item_count: int) -> str:
    """P3.4 / OQ-P3-08: the ONLY merge value the production template takes. A
    PURE, deterministic function of (first item title, total count): the title
    in «» then, if more items, "و{n} …" with correct Arabic number agreement.
    No price, no customer name, no address - the title is already truncated by
    the caller and stripped of guillemets here (it must never close the quote)."""
    clean = title.replace("«", "").replace("»", "").strip()
    if not clean:
        return "منتجات"
    head = f"«{clean}»"
    extra = max(0, item_count - 1)
    if extra == 0:
        return head
    if extra == 1:
        return f"{head} ومنتج آخر"
    if extra == 2:
        return f"{head} ومنتجان آخران"
    if extra <= 10:
        return f"{head} و{extra} منتجات أخرى"
    return f"{head} و{extra} منتجاً آخر"


def handle_cart_reminder(
    settings: WorkerSettings, conn: Any, *,
    tenant_id, payload: dict, now: datetime,
):
    platform_cart_id = str(payload.get("cart_id", ""))
    cart = repos_carts.lock_cart(conn, tenant_id=tenant_id, platform_cart_id=platform_cart_id)
    if cart is None or cart["status"] != "open":
        return Cancel("cart_closed")
    tombstoned = repos_carts.cart_tombstone_status(
        conn, tenant_id=tenant_id, platform_cart_id=platform_cart_id,
    )
    if tombstoned is not None:
        # F-P3-24 (H89): a terminal event for this cart committed while it was
        # being opened; the cart row lock makes this read final. Close the cart
        # in the same transaction and never send.
        repos_carts.finalize_cart(
            conn, tenant_id=tenant_id, platform_cart_id=platform_cart_id, status=tombstoned,
        )
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

    # P3.4 (H100): marketing is enabled PER TENANT, absence = disabled. A
    # disabled tenant's cart is NOT burned: Cancel('marketing_disabled') is a
    # REVIVABLE reason, so fresh cart activity after enabling re-schedules it.
    activation = repos_marketing.read_activation(conn, tenant_id=tenant_id)
    if activation is None or not activation["enabled"]:
        return Cancel("marketing_disabled")

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
