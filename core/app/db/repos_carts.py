"""core/app/db/repos_carts.py - P3.2 carts read/write SQL (H91/H92).

Handler-side functions (lock/mark) run inside the scheduler's tenant_tx; the
webhook-side application functions are added in Stage D. The snapshot is the
H91 minimal liveness picture (item_count, total_minor, currency, <=3 truncated
titles) and is NEVER returned to a log line.
"""
from __future__ import annotations

import uuid
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def lock_cart(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, platform_cart_id: str,
) -> dict[str, Any] | None:
    """SELECT ... FOR UPDATE: the cart row is the serialization point for the
    reminder (H89 - a concurrent finalizing event blocks here, in the same tx).
    Returns the cart in ANY status; the handler decides what it means."""
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(
            "SELECT id, customer_id, status, last_activity_at, snapshot "
            "FROM carts WHERE tenant_id = %s AND platform_cart_id = %s FOR UPDATE",
            (tenant_id, platform_cart_id),
        ).fetchone()
    return dict(row) if row is not None else None


def mark_cart_reminded(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, platform_cart_id: str,
) -> None:
    """The single-stage reminder marker (a reminded cart never gets a second
    job - Stage 1 only in P3.2)."""
    conn.execute(
        "UPDATE carts SET status = 'reminded', updated_at = now() "
        "WHERE tenant_id = %s AND platform_cart_id = %s",
        (tenant_id, platform_cart_id),
    )


def latest_ai_core_conversation(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
) -> uuid.UUID | None:
    """The customer's most recent conversation on an ai_core channel of this
    merchant (H77: automation needs a conversation; the webhook never creates
    one, the handler only LOOKS it up at execution time)."""
    row = conn.execute(
        "SELECT c.id FROM conversations c "
        "JOIN channel_accounts ch ON ch.id = c.channel_account_id "
        "WHERE c.tenant_id = %s AND c.customer_id = %s AND ch.engine = 'ai_core' "
        "ORDER BY c.last_message_at DESC NULLS LAST, c.created_at DESC LIMIT 1",
        (tenant_id, customer_id),
    ).fetchone()
    return None if row is None else row[0]


# --- webhook-side application (Stage D, H91/H92) ------------------------------

# H91 minimal snapshot: item_count, total_minor, currency, <=3 truncated titles.
# Anything else the platform sends is dropped HERE, before it ever reaches the
# database - the store must not use the cart event as a data dump.
SNAPSHOT_MAX_ITEMS = 3


def customer_for_identity(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID,
    wa_id: str | None, phone_e164: str | None, default_country_code: str,
) -> uuid.UUID | None:
    """Match an EXISTING customer by wa_id (digits) or phone_e164 (normalized
    through the ONE core E.164 normalizer, app/tools/extract.to_e164 - the same
    function the order-tracking path uses). H91: identity is strict - no match
    => None; a customer is NEVER created from a cart event."""
    import re

    from app.tools.extract import to_e164

    if wa_id:
        digits = re.sub(r"\D", "", str(wa_id))
        if digits:
            row = conn.execute(
                "SELECT id FROM customers WHERE tenant_id = %s AND wa_id = %s",
                (tenant_id, digits),
            ).fetchone()
            if row is not None:
                return row[0]
    if phone_e164:
        raw_digits = re.sub(r"\D", "", str(phone_e164))
        e164 = to_e164(raw_digits, default_country_code) or f"+{raw_digits}"
        row = conn.execute(
            "SELECT id FROM customers WHERE tenant_id = %s AND phone_e164 = %s LIMIT 1",
            (tenant_id, e164),
        ).fetchone()
        if row is not None:
            return row[0]
    return None


def build_snapshot(
    *, item_count: int, total_minor: int, currency: str,
    items: list, title_max: int,
) -> dict:
    """The H91 minimal liveness snapshot; titles truncated, at most 3 items."""
    kept = []
    for item in items[:SNAPSHOT_MAX_ITEMS]:
        title = str(item.get("title", ""))[:title_max] if isinstance(item, dict) else ""
        kept.append({"title": title})
    return {
        "item_count": int(item_count), "total_minor": int(total_minor),
        "currency": currency, "items": kept,
    }


def upsert_cart_open(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
    platform_cart_id: str, occurred_at, snapshot: dict,
) -> tuple[object, str]:
    """cart.updated application: INSERT, or update ONLY an open cart. An
    out-of-order (older) event never rewinds last_activity_at (GREATEST), and a
    FINAL cart is never reopened (the DO UPDATE's WHERE). Returns the cart's
    effective (last_activity_at, status) for scheduling decisions."""
    row = conn.execute(
        "INSERT INTO carts (tenant_id, customer_id, platform_cart_id, status, last_activity_at, snapshot) "
        "VALUES (%s, %s, %s, 'open', %s, %s) "
        "ON CONFLICT (tenant_id, platform_cart_id) DO UPDATE "
        "SET last_activity_at = GREATEST(carts.last_activity_at, EXCLUDED.last_activity_at), "
        "    customer_id = EXCLUDED.customer_id, snapshot = EXCLUDED.snapshot, updated_at = now() "
        "WHERE carts.status = 'open' "
        "RETURNING last_activity_at, status",
        (tenant_id, customer_id, platform_cart_id, occurred_at, Jsonb(snapshot)),
    ).fetchone()
    if row is not None:
        return row[0], row[1]
    # Conflict on a FINAL cart (or a reminded one): read the authoritative row.
    cur = conn.execute(
        "SELECT last_activity_at, status FROM carts "
        "WHERE tenant_id = %s AND platform_cart_id = %s",
        (tenant_id, platform_cart_id),
    ).fetchone()
    if cur is None:  # unreachable: ON CONFLICT means the row exists
        raise RuntimeError("carts row vanished inside the upsert transaction")
    return cur[0], cur[1]


def finalize_cart(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, platform_cart_id: str, status: str,
) -> bool:
    """cart.recovered / cart.cleared: open -> final. Idempotent - an event for
    an already-final (or missing) cart changes nothing."""
    cur = conn.execute(
        "UPDATE carts SET status = %s, updated_at = now() "
        "WHERE tenant_id = %s AND platform_cart_id = %s AND status = 'open'",
        (status, tenant_id, platform_cart_id),
    )
    return cur.rowcount > 0
