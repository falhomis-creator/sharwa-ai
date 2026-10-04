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
