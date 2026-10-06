"""core/app/api/routes_carts.py - the P3.2 platform cart webhook (H91).

POST /webhooks/platform/cart - REUSES routes_catalog's pure verification
functions (verify_platform_signature / verify_webhook_timestamp) and the same
written limits (platform_webhook_body_max_bytes / platform_timestamp_skew_s /
platform_event_batch_max). No copy-pasted crypto.

Literal order (S26 enforces it): size limit => signature => replay window =>
json.loads => schema. Identity is STRICT: no customer is ever created from a
cart event; no match => the event is counted and ignored. Event payloads and
item titles are NEVER logged (H91) - log lines carry type names and counts
only. The schema is CLOSED: unknown event types are ignored + counted once per
new type (like the catalog webhook); unknown keys inside a known event are
dropped at the snapshot boundary, never stored.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request

from app import db as core_db
from app.api.errors import ApiError
from app.api.routes_catalog import verify_platform_signature, verify_webhook_timestamp
from app.db import repos
from app.obs import metrics
from app import cart_events

router = APIRouter()

# Process-wide: an unknown event type is logged once per NEW type, never per
# event (the catalog webhook's own §5.2 posture).
_SEEN_UNKNOWN_TYPES: set[str] = set()

CART_EVENT_TYPES = frozenset({"cart.updated", "cart.recovered", "cart.cleared"})
_CART_ID_MAX = 64
_TITLE_MAX_SENTINEL = 10_000  # schema ceiling; the snapshot re-truncates to settings


def _parse_occurred_at(value: Any) -> datetime | None:
    """ISO-8601; a trailing Z is accepted; a naive value is read as UTC."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _validate_event(event: Any) -> str | None:
    """None when the event is well-formed; otherwise a short reason (422)."""
    if not isinstance(event, dict):
        return "event_not_object"
    etype = event.get("type")
    if not isinstance(etype, str):
        return "type_missing"
    if etype not in CART_EVENT_TYPES:
        return None  # unknown types are IGNORED + counted, never a 422
    if not isinstance(event.get("cart_id"), str) or not 1 <= len(event["cart_id"]) <= _CART_ID_MAX:
        return "cart_id_invalid"
    if _parse_occurred_at(event.get("occurred_at")) is None:
        return "occurred_at_invalid"
    if etype != "cart.updated":
        return None
    customer = event.get("customer")
    if not isinstance(customer, dict):
        return "customer_missing"
    wa_id = customer.get("wa_id")
    phone = customer.get("phone_e164")
    has_identity = (isinstance(wa_id, str) and wa_id) or (isinstance(phone, str) and phone)
    if not has_identity:
        return "customer_identity_missing"
    for key in ("item_count", "total_minor"):
        value = event.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return f"{key}_invalid"
    currency = event.get("currency")
    if not isinstance(currency, str) or len(currency) != 3:
        return "currency_invalid"
    items = event.get("items")
    if not isinstance(items, list) or len(items) > 3:
        return "items_invalid"
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("title"), str):
            return "item_title_invalid"
        if len(item["title"]) > _TITLE_MAX_SENTINEL:
            return "item_title_invalid"
    return None


@router.post("/webhooks/platform/cart")
async def platform_cart_webhook(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings

    # 1. size limit (declared + actual).
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            declared = -1
        if declared > settings.platform_webhook_body_max_bytes:
            metrics.cart_webhook_requests_total.labels("413").inc()
            raise ApiError("PLATFORM_PAYLOAD_TOO_LARGE")
    raw_body = await request.body()
    if len(raw_body) > settings.platform_webhook_body_max_bytes:
        metrics.cart_webhook_requests_total.labels("413").inc()
        raise ApiError("PLATFORM_PAYLOAD_TOO_LARGE")

    # 2. signature - BEFORE any parsing (S26).
    signature = request.headers.get("X-Platform-Signature", "")
    timestamp = request.headers.get("X-Platform-Timestamp", "")
    if not verify_platform_signature(settings.platform_webhook_secret, timestamp, raw_body, signature):
        metrics.cart_webhook_requests_total.labels("401").inc()
        raise ApiError("PLATFORM_SIGNATURE_INVALID")

    # 3. replay window.
    if not verify_webhook_timestamp(timestamp, time.time(), settings.platform_timestamp_skew_s):
        metrics.cart_webhook_requests_total.labels("401").inc()
        raise ApiError("PLATFORM_TIMESTAMP_SKEW")

    # 4. JSON.
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        metrics.cart_webhook_requests_total.labels("422").inc()
        raise ApiError("VALIDATION_FAILED")

    # 5. closed schema.
    tenant_ref = payload.get("tenant_ref")
    events = payload.get("events")
    if not isinstance(tenant_ref, str) or not tenant_ref or not isinstance(events, list):
        metrics.cart_webhook_requests_total.labels("422").inc()
        raise ApiError("VALIDATION_FAILED")
    if len(events) > settings.platform_event_batch_max:
        metrics.cart_webhook_requests_total.labels("422").inc()
        raise ApiError("VALIDATION_FAILED")
    for event in events:
        reason = _validate_event(event)
        if reason is not None:
            metrics.cart_webhook_requests_total.labels("422").inc()
            raise ApiError("VALIDATION_FAILED")

    with core_db.system_tx() as conn:
        resolved = repos.resolve_tenant(conn, tenant_ref)
    if resolved is None or resolved[1] == "suspended":
        # Good-faith 200 (the sender acted correctly; no retry is wanted).
        metrics.cart_webhook_requests_total.labels("200").inc()
        return {"status": "unknown_tenant_ignored"}
    tenant_id = resolved[0]

    counts: dict[str, int] = {}
    for event in events:
        etype = str(event.get("type"))
        if etype not in CART_EVENT_TYPES:
            if etype not in _SEEN_UNKNOWN_TYPES:
                _SEEN_UNKNOWN_TYPES.add(etype)
                cart_events.log_unknown_cart_type(etype)
            outcome = "unknown_type"
        else:
            enriched = dict(event)
            enriched["occurred_at"] = _parse_occurred_at(event.get("occurred_at"))
            with core_db.tenant_tx(tenant_id) as conn:  # one tenant tx PER event
                outcome = cart_events.apply_cart_event(
                    conn, tenant_id=tenant_id, event=enriched,
                    delay_h=settings.cart_reminder_delay_h,
                    max_late_s=settings.cart_reminder_max_late_h * 3600,
                    title_max=settings.cart_item_title_max,
                    default_country_code=settings.default_country_code,
                )
        metrics.cart_events_total.labels(etype, outcome).inc()
        counts[outcome] = counts.get(outcome, 0) + 1

    metrics.cart_webhook_requests_total.labels("200").inc()
    return {"status": "accepted", "counts": counts}
