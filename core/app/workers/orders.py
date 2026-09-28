"""core/app/workers/orders.py - the order-tracking coordinator (P1.7, H40/H50-H54).

The tool (app.tools.track_order.run) is PURE; this module reads and writes. Three
phases, mirroring turn.py: read + brute-force gate (short tx) -> platform call
OUTSIDE any transaction (H40) -> log the attempt (short tx). Reply composition and
the outbox write stay in turn.py, which calls this coordinator and then writes via
verify.insert_verified_outbox as usual.
"""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from typing import Any

from app import db as core_db
from app.channels.commerce_client import CommerceClientError, CommerceUnavailableError
from app.db import repos
from app.db import repos_outbox
from app.obs import metrics
from app.tools import extract
from app.tools import track_order
from app.tools.registry import ToolContext
from app.workers.config import WorkerSettings


def order_ref_hash(order_ref: str, key: str) -> str:
    """HMAC-SHA256 of the order ref (H54) - deterministic across processes and
    restarts, so the brute-force counters are shared, not per-process."""
    return hmac.new(key.encode("utf-8"), order_ref.encode("utf-8"), hashlib.sha256).hexdigest()


def run_order_lookup(
    settings: WorkerSettings,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    commerce: Any,
    bodies: tuple[str, ...],
) -> track_order.OrderLookup:
    # ---- Phase 1: read + brute-force gate (short transaction) ----
    ref_hash: str = ""
    path: str = "other_number"
    customer_id: uuid.UUID | None = None
    channel_phone: str | None = None
    platform_ref: str = ""

    with core_db.tenant_tx(tenant_id) as conn:
        customer_id = repos_outbox.customer_id_for_conversation(conn, conversation_id)
        wa_id = repos_outbox.wa_id_for_conversation(conn, conversation_id)
        platform_ref = repos.platform_ref_for_tenant(conn, tenant_id) or ""
        channel_phone = extract.to_e164(wa_id, settings.default_country_code) if wa_id else None

        order_ref = extract.extract_order_ref(bodies, settings.order_ref_pattern)
        if order_ref is None:
            metrics.tool_calls_total.labels("track_order", "need_order_ref").inc()
            return track_order.OrderLookup(kind="need_order_ref")

        raw = extract.extract_phone_candidates(
            bodies, max_candidates=settings.order_lookup_max_phone_candidates,
        )
        candidates = tuple(c for c in (extract.to_e164(r, settings.default_country_code) for r in raw) if c)
        path = track_order.resolve_path(channel_phone, candidates)
        if path == "other_number" and not candidates:
            metrics.tool_calls_total.labels("track_order", "need_phone").inc()
            return track_order.OrderLookup(kind="need_phone")

        ref_hash = order_ref_hash(order_ref, settings.order_ref_hash_key)
        if customer_id is not None and repos_outbox.order_lookup_blocked(
            conn, customer_id=customer_id, order_ref_hash=ref_hash,
        ):
            repos_outbox.insert_order_lookup_attempt(
                conn, tenant_id=tenant_id, conversation_id=conversation_id,
                customer_id=customer_id, path=path, order_ref_hash=ref_hash, outcome="blocked",
            )
            metrics.order_lookup_total.labels(path, "blocked").inc()
            metrics.tool_calls_total.labels("track_order", "blocked").inc()
            return track_order.OrderLookup(kind="blocked")

    # ---- Phase 2: platform call, OUTSIDE any transaction (H40) ----
    ctx = ToolContext(
        tenant_id=tenant_id, conversation_id=conversation_id, tenant_ref=platform_ref,
        channel_phone_e164=channel_phone, message_texts=bodies, commerce=commerce, settings=settings,
    )
    started = time.monotonic()
    try:
        result = track_order.run(ctx)
    except CommerceUnavailableError:
        metrics.order_lookup_platform_errors_total.labels("unavailable").inc()
        result = track_order.OrderLookup(kind="unavailable")
    except CommerceClientError:
        metrics.order_lookup_platform_errors_total.labels("client_error").inc()
        result = track_order.OrderLookup(kind="unavailable")
    finally:
        metrics.order_lookup_duration_seconds.observe(time.monotonic() - started)

    # ---- Phase 3: log the attempt (short transaction) - only card/unverified ----
    if result.kind in ("card", "unverified") and customer_id is not None:
        outcome = "allowed" if result.kind == "card" else "denied"
        with core_db.tenant_tx(tenant_id) as conn:
            repos_outbox.insert_order_lookup_attempt(
                conn, tenant_id=tenant_id, conversation_id=conversation_id,
                customer_id=customer_id, path=path, order_ref_hash=ref_hash, outcome=outcome,
            )
        metrics.order_lookup_total.labels(path, outcome).inc()

    metrics.tool_calls_total.labels("track_order", result.kind).inc()
    return result
