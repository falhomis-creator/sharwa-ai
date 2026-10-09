"""core/app/db/repos_gift.py - gift carts (P4 Task 14, migration 0021).

A gift cart is one basket the curator proposed, stored so the customer's link
(https://<platform>/checkout/gift/<id>) can be resolved by the platform. It
holds identifiers only - never a price or a total (the platform prices it).
Every call runs inside the caller's tenant_tx (RLS is the tenant boundary).
"""
from __future__ import annotations

import uuid
from typing import Any

import psycopg
from psycopg.types.json import Jsonb


def insert_gift_cart(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID | None,
    items: list[dict[str, Any]], currency: str, budget_minor: int,
) -> uuid.UUID:
    """Store one basket; returns its id (the token in the customer's link)."""
    row = conn.execute(
        "INSERT INTO gift_carts (tenant_id, conversation_id, items, currency, budget_minor) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (tenant_id, conversation_id, Jsonb(items), currency, budget_minor),
    ).fetchone()
    if row is None:  # INSERT ... RETURNING always yields a row
        raise RuntimeError("gift cart insert returned no id")
    return uuid.UUID(str(row[0]))


def read_gift_cart(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, cart_id: uuid.UUID,
) -> dict[str, Any] | None:
    """The basket for the platform, or None when unknown or expired."""
    row = conn.execute(
        "SELECT id, items, currency, expires_at FROM gift_carts "
        "WHERE tenant_id = %s AND id = %s AND expires_at > now()",
        (tenant_id, cart_id),
    ).fetchone()
    if row is None:
        return None
    return {
        "cart_id": str(row[0]),
        "items": list(row[1]),
        "currency": str(row[2]),
        "expires_at": row[3].isoformat(),
    }
