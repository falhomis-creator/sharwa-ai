"""core/app/api/routes_catalog.py - the P1.4 platform catalog webhook (C1).

POST /webhooks/platform/catalog - the platform's outbound event contract. No
JWT (the HMAC signature IS the authentication); the body is bounded by a
written limit; the timestamp is checked against a written replay window;
tenant_ref resolves via app.resolve_tenant (unknown => 200
unknown_tenant_ignored, the same good-faith posture as an anonymous session -
the sender acted correctly, no retry is wanted).

The signature verification and timestamp check are pure functions (testable
without a network), kept in this module so the contract lives next to the route.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Request

from app import db as core_db
from app.api.errors import ApiError
from app.db import repos
from app.db import repos_catalog
from app.obs import logging as obs_logging
from app.obs import metrics

router = APIRouter()

_log = obs_logging.get_logger("catalog")

# Process-wide: an unknown event type is logged once per NEW type, never per
# event (PROMPT §5.2: "سجل واحد لكل نوع جديد").
_SEEN_UNKNOWN_TYPES: set[str] = set()


def verify_platform_signature(secret: str, timestamp: str, raw_body: bytes, signature: str) -> bool:
    """HMAC-SHA256 over f"{timestamp}." + raw_body, compared in constant time.

    The secret is PLATFORM_WEBHOOK_SECRET - independent of the gateway and every
    other secret. An empty secret or signature always fails (H5).
    """
    if not secret or not signature:
        return False
    message = f"{timestamp}.".encode("utf-8") + raw_body
    expected = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_webhook_timestamp(value: str, now: float, skew_s: int) -> bool:
    """True when X-Platform-Timestamp is within the replay window.

    False (=> reject as PLATFORM_TIMESTAMP_SKEW) when unparseable, non-positive,
    or older than skew_s seconds (replay protection).
    """
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return False
    return timestamp > 0 and (now - timestamp) <= skew_s


@router.post("/webhooks/platform/catalog")
async def platform_catalog_webhook(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            declared = -1
        if declared > settings.platform_webhook_body_max_bytes:
            metrics.catalog_webhook_requests_total.labels("413").inc()
            raise ApiError("PLATFORM_PAYLOAD_TOO_LARGE")

    raw_body = await request.body()
    if len(raw_body) > settings.platform_webhook_body_max_bytes:
        metrics.catalog_webhook_requests_total.labels("413").inc()
        raise ApiError("PLATFORM_PAYLOAD_TOO_LARGE")

    signature = request.headers.get("X-Platform-Signature", "")
    timestamp = request.headers.get("X-Platform-Timestamp", "")
    if not verify_platform_signature(settings.platform_webhook_secret, timestamp, raw_body, signature):
        metrics.catalog_webhook_requests_total.labels("401").inc()
        raise ApiError("PLATFORM_SIGNATURE_INVALID")
    if not verify_webhook_timestamp(timestamp, time.time(), settings.platform_timestamp_skew_s):
        metrics.catalog_webhook_requests_total.labels("401").inc()
        raise ApiError("PLATFORM_TIMESTAMP_SKEW")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        metrics.catalog_webhook_requests_total.labels("422").inc()
        raise ApiError("VALIDATION_FAILED")

    tenant_ref = payload.get("tenant_ref")
    events = payload.get("events")
    if not isinstance(tenant_ref, str) or not tenant_ref or not isinstance(events, list):
        metrics.catalog_webhook_requests_total.labels("422").inc()
        raise ApiError("VALIDATION_FAILED")
    if len(events) > settings.platform_event_batch_max:
        metrics.catalog_webhook_requests_total.labels("422").inc()
        raise ApiError("VALIDATION_FAILED")

    with core_db.system_tx() as conn:
        resolved = repos.resolve_tenant(conn, tenant_ref)
    if resolved is None:
        metrics.catalog_webhook_requests_total.labels("200").inc()
        return {"status": "unknown_tenant_ignored"}
    tenant_id, status = resolved
    if status == "suspended":
        metrics.catalog_webhook_requests_total.labels("200").inc()
        return {"status": "unknown_tenant_ignored"}

    with core_db.tenant_tx(tenant_id) as conn:
        counts = repos_catalog.apply_catalog_events(
            conn, tenant_id=tenant_id, events=events,
            logger=_log, seen_unknown_types=_SEEN_UNKNOWN_TYPES,
        )

    metrics.catalog_webhook_requests_total.labels("200").inc()
    return {"status": "accepted", "counts": counts}
