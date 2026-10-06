"""core/app/workers/carts.py - P3.2 cart-event application (H91/H92).

The worker layer the webhook route calls INSIDE its per-event tenant
transaction. It owns the scheduled_jobs writes for carts (schedule /
reschedule / cancel in the SAME transaction as the cart change - H92), which
keeps the S24 rule intact: only app/workers/ modules (and tests) call the
repos_scheduler writers.

Outcome vocabulary (closed, feeds cart_events_total{type,outcome}):
  applied | ignored_no_customer | already_final | already_reminded
"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any


from app.db import repos_carts, repos_scheduler
from app.obs import logging as obs_logging

_log = obs_logging.get_logger("carts")

CART_JOB_KIND = "cart_reminder"


def _dedupe_key(platform_cart_id: str) -> str:
    return f"cart:{platform_cart_id}:stage1"


def apply_cart_event(
    conn: Any, *, tenant_id: uuid.UUID, event: dict,
    delay_h: int, max_late_s: int, title_max: int, default_country_code: str,
) -> str:
    """Apply ONE already-schema-validated cart event inside the caller's open
    tenant transaction. Returns the closed-vocabulary outcome."""
    etype = str(event["type"])
    platform_cart_id = str(event["cart_id"])
    occurred_at = event["occurred_at"]

    if etype in ("cart.recovered", "cart.cleared"):
        final_status = "recovered" if etype == "cart.recovered" else "cleared"
        reason = "cart_recovered" if etype == "cart.recovered" else "cart_cleared"
        finalized = repos_carts.finalize_cart(
            conn, tenant_id=tenant_id, platform_cart_id=platform_cart_id, status=final_status,
        )
        if not finalized:
            # F-P3-24: a terminal event for a cart never seen yet is remembered,
            # so a late cart.updated can never open it (no-op if a row exists).
            repos_carts.tombstone_unseen_cart(
                conn, tenant_id=tenant_id, platform_cart_id=platform_cart_id,
                status=final_status, occurred_at=occurred_at,
            )
        # H92: finality beats execution - the cancel rides the SAME transaction
        # (even for an already-final cart, a straggling pending job must die).
        repos_scheduler.cancel(
            conn, tenant_id=tenant_id, dedupe_key=_dedupe_key(platform_cart_id), reason=reason,
        )
        return "applied" if finalized else "already_final"

    # cart.updated
    if repos_carts.cart_tombstone_status(
        conn, tenant_id=tenant_id, platform_cart_id=platform_cart_id,
    ) is not None:
        # F-P3-24: the store already reported this cart as bought/cleared.
        return "already_final"

    customer = event.get("customer") or {}
    customer_id = repos_carts.customer_for_identity(
        conn, tenant_id=tenant_id,
        wa_id=customer.get("wa_id"), phone_e164=customer.get("phone_e164"),
        default_country_code=default_country_code,
    )
    if customer_id is None:
        # H91: strict identity - no customer is ever created from a cart event.
        return "ignored_no_customer"

    snapshot = repos_carts.build_snapshot(
        item_count=int(event.get("item_count", 0)),
        total_minor=int(event.get("total_minor", 0)),
        currency=str(event.get("currency", "")),
        items=list(event.get("items") or []),
        title_max=title_max,
    )
    last_activity_at, status = repos_carts.upsert_cart_open(
        conn, tenant_id=tenant_id, customer_id=customer_id,
        platform_cart_id=platform_cart_id, occurred_at=occurred_at, snapshot=snapshot,
    )
    if status != "open":
        # A final/remembered cart is never reopened and never re-scheduled.
        return "already_reminded" if status == "reminded" else "already_final"

    run_at = last_activity_at + timedelta(hours=delay_h)
    inserted = repos_scheduler.schedule(
        conn, tenant_id=tenant_id, kind=CART_JOB_KIND,
        dedupe_key=_dedupe_key(platform_cart_id), run_at=run_at,
        payload={"cart_id": platform_cart_id}, max_lateness_s=max_late_s,
    )
    if not inserted:
        # Fresh activity pushes an EXISTING pending reminder out (H88-adjacent:
        # the delay always counts from the LATEST activity).
        repos_scheduler.reschedule(
            conn, tenant_id=tenant_id, dedupe_key=_dedupe_key(platform_cart_id), run_at=run_at,
        )
    return "applied"


def log_unknown_cart_type(unknown_type: str) -> None:
    """One log line per NEW unknown type (never per event) - type name only,
    never any payload field (H48/H91)."""
    obs_logging.log_event(
        _log, event="cart.unknown_type", component="carts",
        level=logging.WARNING, type_=unknown_type,
    )
