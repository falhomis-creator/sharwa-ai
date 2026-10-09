"""core/app/api/routes_gift.py - the platform's gift-cart lookup (P4 Task 14).

POST /webhooks/platform/gift-cart - the platform (Sharwa) resolves the id in a
customer's link https://<platform>/checkout/gift/<cart_id> to the basket the
gift curator proposed, then prices it and builds the real checkout itself.

Same contract as the catalog/cart webhooks (no JWT - the HMAC signature IS the
authentication): X-Platform-Signature = HMAC-SHA256(PLATFORM_WEBHOOK_SECRET,
"<X-Platform-Timestamp>." + raw body), a written replay window and body cap.
Body: {"tenant_ref": "<store>", "cart_id": "<uuid>"}.
200 {"status": "ok", "cart": {cart_id, items[{platform_product_id,
platform_variant_id, qty}], currency, expires_at}} - identifiers only, never a
price; 404 NOT_FOUND for an unknown store, unknown cart or an expired link.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Request

from app import db as core_db
from app.api.errors import ApiError
from app.api.routes_catalog import verify_platform_signature, verify_webhook_timestamp
from app.db import repos, repos_gift

router = APIRouter()

GIFT_CART_BODY_MAX_BYTES = 1024


@router.post("/webhooks/platform/gift-cart")
async def platform_gift_cart(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    raw_body = await request.body()
    if len(raw_body) > GIFT_CART_BODY_MAX_BYTES:
        raise ApiError("PLATFORM_PAYLOAD_TOO_LARGE")
    signature = request.headers.get("X-Platform-Signature", "")
    timestamp = request.headers.get("X-Platform-Timestamp", "")
    if not verify_platform_signature(settings.platform_webhook_secret, timestamp, raw_body, signature):
        raise ApiError("PLATFORM_SIGNATURE_INVALID")
    if not verify_webhook_timestamp(timestamp, time.time(), settings.platform_timestamp_skew_s):
        raise ApiError("PLATFORM_TIMESTAMP_SKEW")
    try:
        payload = json.loads(raw_body.decode("utf-8"))
        tenant_ref = payload["tenant_ref"]
        cart_id = uuid.UUID(str(payload["cart_id"]))
    except (ValueError, UnicodeDecodeError, KeyError, TypeError) as exc:
        raise ApiError("VALIDATION_FAILED") from exc
    if not isinstance(tenant_ref, str) or not tenant_ref:
        raise ApiError("VALIDATION_FAILED")

    with core_db.system_tx() as conn:
        resolved = repos.resolve_tenant(conn, tenant_ref)
    if resolved is None or resolved[1] == "suspended":
        raise ApiError("NOT_FOUND")
    with core_db.tenant_tx(resolved[0]) as conn:
        cart = repos_gift.read_gift_cart(conn, tenant_id=resolved[0], cart_id=cart_id)
    if cart is None:
        raise ApiError("NOT_FOUND")
    return {"status": "ok", "cart": cart}
