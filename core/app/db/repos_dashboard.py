"""core/app/db/repos_dashboard.py - P3.5: READ-ONLY queries behind the dashboard.

Every function here runs on a tenant_tx() connection (RLS scoped to ONE tenant)
and returns counts/states only - never a phone, a customer id, a message body or
a cart line (H20/H48). The single exception is list_tenants_admin(), the
SECURITY DEFINER wrapper that exists because `tenants` itself is RLS'd
(0018); it runs on system_tx() and returns identifiers only.
No writes in this module (the activation writer stays repos_marketing, S29).
"""
from __future__ import annotations

import uuid
from typing import Any

import psycopg


def list_tenants_admin(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """system_tx() only (app.admin_list_tenants, 0018)."""
    rows = conn.execute(
        "SELECT id, platform_ref, name, status, timezone FROM app.admin_list_tenants()"
    ).fetchall()
    return [{"id": r[0], "ref": r[1], "name": r[2], "status": r[3], "timezone": r[4]} for r in rows]


def tenant_header(conn: psycopg.Connection, *, tenant_id: uuid.UUID) -> dict[str, Any] | None:
    """The calling tenant's own row (RLS tenant_self shows only this one)."""
    row = conn.execute(
        "SELECT id, platform_ref, name, status, timezone FROM tenants WHERE id = %s",
        [tenant_id],
    ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "ref": row[1], "name": row[2], "status": row[3], "timezone": row[4]}


def cart_counts(conn: psycopg.Connection, *, tenant_id: uuid.UUID, days: int) -> dict[str, int]:
    """Carts that changed state in the window, by status (the 5-value CHECK)."""
    rows = conn.execute(
        "SELECT status, count(*) FROM carts WHERE tenant_id = %s "
        "AND updated_at >= clock_timestamp() - make_interval(days => %s) GROUP BY status",
        [tenant_id, days],
    ).fetchall()
    out = {"open": 0, "recovered": 0, "cleared": 0, "reminded": 0, "expired": 0}
    for status, n in rows:
        out[str(status)] = int(n)
    return out


def sent_by_day(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, days: int,
) -> list[tuple[str, str, int]]:
    """(UTC date iso, message_class, count) of SENT outbox rows in the window."""
    rows = conn.execute(
        "SELECT (sent_at AT TIME ZONE 'UTC')::date, message_class, count(*) FROM outbox "
        "WHERE tenant_id = %s AND status = 'sent' "
        "AND sent_at >= clock_timestamp() - make_interval(days => %s) GROUP BY 1, 2",
        [tenant_id, days],
    ).fetchall()
    return [(r[0].isoformat(), str(r[1]), int(r[2])) for r in rows]


def outbox_status_counts(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, days: int,
) -> dict[str, dict[str, int]]:
    """{message_class: {status: n}} for rows CREATED in the window."""
    rows = conn.execute(
        "SELECT message_class, status, count(*) FROM outbox WHERE tenant_id = %s "
        "AND created_at >= clock_timestamp() - make_interval(days => %s) GROUP BY 1, 2",
        [tenant_id, days],
    ).fetchall()
    out: dict[str, dict[str, int]] = {}
    for cls, status, n in rows:
        out.setdefault(str(cls), {})[str(status)] = int(n)
    return out


def consent_events_by_day(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, days: int,
) -> list[tuple[str, bool, int]]:
    """(UTC date iso, granted, DISTINCT customers) of marketing consent events -
    a repeated STOP by one customer counts once per day."""
    rows = conn.execute(
        "SELECT (created_at AT TIME ZONE 'UTC')::date, granted, count(DISTINCT customer_id) "
        "FROM consents WHERE tenant_id = %s AND scope = 'marketing' "
        "AND created_at >= clock_timestamp() - make_interval(days => %s) GROUP BY 1, 2",
        [tenant_id, days],
    ).fetchall()
    return [(r[0].isoformat(), bool(r[1]), int(r[2])) for r in rows]


def open_cart_count(conn: psycopg.Connection, *, tenant_id: uuid.UUID) -> int:
    row = conn.execute(
        "SELECT count(*) FROM carts WHERE tenant_id = %s AND status = 'open'", [tenant_id],
    ).fetchone()
    return int(row[0]) if row is not None else 0


def recent_audit(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, limit: int = 30,
) -> list[dict[str, Any]]:
    """Marketing-related audit rows (actor sub, action, time) - no before/after blobs."""
    rows = conn.execute(
        "SELECT actor_sub, actor_role, action, created_at FROM audit_log "
        "WHERE tenant_id = %s AND action LIKE 'marketing.%%' "
        "ORDER BY created_at DESC, id DESC LIMIT %s",
        [tenant_id, limit],
    ).fetchall()
    return [{"actor": r[0], "role": r[1], "action": r[2], "at": r[3].isoformat()} for r in rows]
