"""core/app/db/repos_stock.py - the back-in-stock (P2.3) raw SQL.

Same discipline as repos_catalog/repos_outbox/repos_geo: every SQL string for
the stock path lives here and ONLY here. Callers pass an already-open
connection from tenant_tx()/system_tx() (H2).

The atomic allocation is `app.allocate_stock_holds` / `app.expire_stock_holds`
(0001) - FOR UPDATE on the stock_levels row + FOR UPDATE SKIP LOCKED on
waitlist_entries. This module does NOT re-implement that logic (H69).
"""
from __future__ import annotations

import uuid
from typing import Any

import psycopg


def read_conversation_slots(conn: psycopg.Connection, *, conversation_id: uuid.UUID) -> dict[str, Any]:
    """The conversation's `slots` JSONB (carries `last_shown_product_ids`)."""
    row = conn.execute("SELECT slots FROM conversations WHERE id = %s", (conversation_id,)).fetchone()
    if row is None or not isinstance(row[0], dict):
        return {}
    return row[0]


def variant_ids_for_product(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, platform_product_id: str,
) -> list[str]:
    """F-P4-07 (Task 18b-2a): the platform variant ids of ONE active product,
    sorted. The waitlist coordinator joins only when this is exactly one variant;
    the slot carries PRODUCT ids, never variant ids."""
    rows = conn.execute(
        "SELECT v.platform_variant_id FROM catalog_variants v "
        "JOIN catalog_products p ON p.id = v.product_id "
        "WHERE p.tenant_id = %s AND p.platform_product_id = %s AND p.active = true "
        "ORDER BY v.platform_variant_id",
        (tenant_id, platform_product_id),
    ).fetchall()
    return [str(r[0]) for r in rows]


def has_active_waitlist(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
    platform_variant_id: str,
) -> bool:
    """Application-level dedupe (P2.3 §3 item 2): a customer must never hold two
    LIVE rows (waiting|held) for the same variant. Read-before-write in the SAME
    transaction as the insert - no new migration, no new index."""
    row = conn.execute(
        "SELECT 1 FROM waitlist_entries "
        "WHERE tenant_id = %s AND customer_id = %s AND platform_variant_id = %s "
        "AND status IN ('waiting','held') LIMIT 1",
        (tenant_id, customer_id, platform_variant_id),
    ).fetchone()
    return row is not None


def count_active_waitlists(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
) -> int:
    """Number of LIVE (waiting|held) rows for one customer, across variants -
    the STOCK_MAX_WAITLIST_PER_CUSTOMER ceiling is enforced on this count."""
    row = conn.execute(
        "SELECT count(*) FROM waitlist_entries "
        "WHERE tenant_id = %s AND customer_id = %s AND status IN ('waiting','held')",
        (tenant_id, customer_id),
    ).fetchone()
    return int(row[0]) if row is not None else 0


def insert_waitlist_entry(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
    conversation_id: uuid.UUID | None, platform_variant_id: str,
) -> uuid.UUID:
    """Register one waiter as `waiting` (the caller already checked dedupe)."""
    row = conn.execute(
        "INSERT INTO waitlist_entries "
        "(tenant_id, customer_id, conversation_id, platform_variant_id) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (tenant_id, customer_id, conversation_id, platform_variant_id),
    ).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING id produced no row")
    return row[0]


def list_waiting_variants(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, limit: int,
) -> list[str]:
    """Distinct variants with at least one `waiting` entry (for the sweep)."""
    rows = conn.execute(
        "SELECT DISTINCT platform_variant_id FROM waitlist_entries "
        "WHERE tenant_id = %s AND status = 'waiting' "
        "ORDER BY platform_variant_id LIMIT %s",
        (tenant_id, limit),
    ).fetchall()
    return [r[0] for r in rows]


def allocate_holds(
    conn: psycopg.Connection, *, platform_variant_id: str, available: int, ttl_s: int,
) -> list[dict[str, Any]]:
    """Call app.allocate_stock_holds (0001) - atomically promotes the oldest
    waiters up to `available - active_holds` and returns the created holds."""
    rows = conn.execute(
        "SELECT id, waitlist_entry_id, platform_variant_id, qty, status, expires_at "
        "FROM app.allocate_stock_holds(%s, %s, make_interval(secs => %s))",
        (platform_variant_id, available, ttl_s),
    ).fetchall()
    return [
        {
            "id": r[0], "waitlist_entry_id": r[1], "platform_variant_id": r[2],
            "qty": int(r[3]), "status": r[4], "expires_at": r[5],
        }
        for r in rows
    ]


def expire_holds(conn: psycopg.Connection, *, platform_variant_id: str) -> int:
    """Call app.expire_stock_holds (0001); the caller re-allocates to promote."""
    row = conn.execute("SELECT app.expire_stock_holds(%s)", (platform_variant_id,)).fetchone()
    return int(row[0]) if row is not None and row[0] is not None else 0


def list_expired_held_entry_ids(
    conn: psycopg.Connection, *, platform_variant_id: str,
) -> list[uuid.UUID]:
    """Held entries whose TTL elapsed (notify before expiring - H71)."""
    rows = conn.execute(
        "SELECT sh.waitlist_entry_id FROM stock_holds sh "
        "WHERE sh.platform_variant_id = %s AND sh.status = 'held' AND sh.expires_at <= now()",
        (platform_variant_id,),
    ).fetchall()
    return [r[0] for r in rows]


def fetch_notify_target(
    conn: psycopg.Connection, *, waitlist_entry_id: uuid.UUID,
) -> dict[str, Any] | None:
    """(conversation_id, channel_account_id, to_wa_id, epoch) for one waitlist
    entry, for the outbox write (bot-origin rows need the conversation epoch -
    0001 CHECK). None when the entry has no conversation (a seeded race row), so
    no notification can be sent."""
    row = conn.execute(
        "SELECT w.conversation_id, v.channel_account_id, c.wa_id, v.epoch "
        "FROM waitlist_entries w "
        "JOIN conversations v ON v.id = w.conversation_id "
        "JOIN customers c ON c.id = w.customer_id "
        "WHERE w.id = %s",
        (waitlist_entry_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "conversation_id": row[0], "channel_account_id": row[1],
        "to_wa_id": row[2], "epoch": int(row[3]),
    }


def fetch_variant_title(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, platform_variant_id: str,
) -> str | None:
    """The merchant's own product title for a variant, verbatim (H35: title only)."""
    row = conn.execute(
        "SELECT p.title FROM catalog_variants v "
        "JOIN catalog_products p ON p.id = v.product_id "
        "WHERE v.tenant_id = %s AND v.platform_variant_id = %s",
        (tenant_id, platform_variant_id),
    ).fetchone()
    return None if row is None else row[0]


def fetch_waitlist_entry(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, waitlist_entry_id: uuid.UUID,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, customer_id, conversation_id, platform_variant_id, status "
        "FROM waitlist_entries WHERE tenant_id = %s AND id = %s",
        (tenant_id, waitlist_entry_id),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0], "customer_id": row[1], "conversation_id": row[2],
        "platform_variant_id": row[3], "status": row[4],
    }


def convert_hold(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, waitlist_entry_id: uuid.UUID,
) -> None:
    """§5.3: customer accepted -> hold converted + waitlist converted, one tx."""
    conn.execute(
        "UPDATE stock_holds SET status = 'converted' "
        "WHERE waitlist_entry_id = %s AND tenant_id = %s AND status = 'held'",
        (waitlist_entry_id, tenant_id),
    )
    conn.execute(
        "UPDATE waitlist_entries SET status = 'converted' WHERE id = %s AND tenant_id = %s",
        (waitlist_entry_id, tenant_id),
    )


def cancel_waitlist(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, waitlist_entry_id: uuid.UUID,
) -> str | None:
    """§5.3: cancel -> waitlist cancelled, hold released; returns the variant."""
    row = conn.execute(
        "UPDATE waitlist_entries SET status = 'cancelled' "
        "WHERE id = %s AND tenant_id = %s AND status IN ('waiting','held') "
        "RETURNING platform_variant_id",
        (waitlist_entry_id, tenant_id),
    ).fetchone()
    conn.execute(
        "UPDATE stock_holds SET status = 'released' "
        "WHERE waitlist_entry_id = %s AND tenant_id = %s AND status = 'held'",
        (waitlist_entry_id, tenant_id),
    )
    return None if row is None else row[0]
