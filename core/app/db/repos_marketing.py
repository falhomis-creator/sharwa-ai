"""core/app/db/repos_marketing.py - the SINGLE marketing_activation writer
(H100/S29).

Every SQL string that writes marketing_activation / marketing_activation_log
lives here and ONLY here - the S29-a static gate enforces it (testsupport.py
is the declared test-seeding exception, like its other raw test SQL).
S29-b: set_enabled(..., enabled=True) may be called ONLY from app/cli.py and
tests - enabling is a deliberate human act through the operator CLI (H100);
set_enabled(False) and set_cap are additionally called by the rollback
coordinator app/workers/marketing.py (H101 level 2). Readers serve the CLI
and policy_gate.

H100: absence of a row means DISABLED - no migration, seed, or fixture ever
enables a tenant. H102: the canary count is read from the LEDGER
(marketing_sent_24h), never from memory. H20/H48: the counters return
numbers only - never a phone or customer text.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import psycopg

MARKETING_DISABLED_REASON = "marketing_disabled"
DEFAULT_CANARY_CAP = 5


@contextmanager
def operator_conn(dsn: str, *, read_only: bool = False) -> Iterator[psycopg.Connection]:
    """The operator CLI's connection (migration role, DSN-based - the same
    pattern as scheduler_cancel/consent_history). Lives HERE so app/cli.py never
    imports psycopg (the import-linter boundary: psycopg only inside app.db).
    Commits on a clean exit, rolls back on an exception; `read_only` makes the
    session itself refuse writes (the dry-run `preview` relies on it)."""
    with psycopg.connect(dsn) as conn:
        if read_only:
            conn.read_only = True
        yield conn


def set_enabled(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, enabled: bool,
    actor: str, reason: str | None, cap: int | None = None,
) -> bool:
    """Upsert the activation row AND append the log row in the caller's ONE
    transaction. `cap` is written when provided (the enable flow passes the
    canary cap). Returns True when the enabled state actually changed."""
    previous = read_activation(conn, tenant_id=tenant_id)
    conn.execute(
        "INSERT INTO marketing_activation "
        "(tenant_id, enabled, canary_cap_per_day, enabled_by, enabled_at, disabled_at, updated_at) "
        "VALUES (%s, %s, COALESCE(%s, %s), %s, "
        "CASE WHEN %s THEN clock_timestamp() ELSE NULL END, "
        "CASE WHEN %s THEN NULL ELSE clock_timestamp() END, clock_timestamp()) "
        "ON CONFLICT (tenant_id) DO UPDATE SET "
        "enabled = EXCLUDED.enabled, "
        "canary_cap_per_day = COALESCE(EXCLUDED.canary_cap_per_day, marketing_activation.canary_cap_per_day), "
        "enabled_by = EXCLUDED.enabled_by, enabled_at = EXCLUDED.enabled_at, "
        "disabled_at = EXCLUDED.disabled_at, updated_at = clock_timestamp()",
        (tenant_id, enabled, cap, DEFAULT_CANARY_CAP, actor if enabled else None,
         enabled, enabled),
    )
    conn.execute(
        "INSERT INTO marketing_activation_log (tenant_id, action, actor, reason) "
        "VALUES (%s, %s, %s, %s)",
        (tenant_id, "enable" if enabled else "disable", actor, reason),
    )
    return previous is None or bool(previous["enabled"]) != enabled


def set_cap(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, cap: int,
    actor: str, reason: str | None,
) -> None:
    """Change the canary cap (upserting a DISABLED row when absent - a cap
    change never enables anyone, H100) + the 'cap_change' log row, one tx."""
    conn.execute(
        "INSERT INTO marketing_activation (tenant_id, enabled, canary_cap_per_day, updated_at) "
        "VALUES (%s, false, %s, clock_timestamp()) "
        "ON CONFLICT (tenant_id) DO UPDATE SET "
        "canary_cap_per_day = EXCLUDED.canary_cap_per_day, updated_at = clock_timestamp()",
        (tenant_id, cap),
    )
    conn.execute(
        "INSERT INTO marketing_activation_log (tenant_id, action, actor, reason) "
        "VALUES (%s, 'cap_change', %s, %s)",
        (tenant_id, actor, reason),
    )


def read_activation(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID,
) -> dict[str, Any] | None:
    """The tenant's activation row, or None (H100: absence = disabled)."""
    row = conn.execute(
        "SELECT enabled, canary_cap_per_day, enabled_by, enabled_at, disabled_at, updated_at "
        "FROM marketing_activation WHERE tenant_id = %s",
        (tenant_id,),
    ).fetchone()
    if row is None:
        return None
    return {"enabled": bool(row[0]), "canary_cap_per_day": int(row[1]),
            "enabled_by": row[2], "enabled_at": row[3], "disabled_at": row[4],
            "updated_at": row[5]}


def read_log(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID,
) -> list[tuple[str, str, str | None, Any]]:
    """The append-only activation history, chronological: (action, actor,
    reason, created_at). Identifiers only - no phone, no customer text."""
    rows = conn.execute(
        "SELECT action, actor, reason, created_at FROM marketing_activation_log "
        "WHERE tenant_id = %s ORDER BY created_at ASC, id ASC",
        (tenant_id,),
    ).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def marketing_sent_24h(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, now: datetime,
    exclude_outbox_id: uuid.UUID | None = None,
) -> int:
    """H102 (canary): the tenant's marketing deliveries in the last 24h,
    counted from the LEDGER (reserved + handed_off - a reservation is a
    delivery in flight), never from memory. `now` is injected (H84).
    `exclude_outbox_id` removes the row BEING GATED (F-P3-20 pattern): a
    re-processed reserved row must not count its own reservation into the cap."""
    sql = (
        "SELECT count(*) FROM proactive_ledger "
        "WHERE tenant_id = %s AND message_class = 'marketing' "
        "AND status IN ('reserved','handed_off') "
        "AND reserved_at > %s - make_interval(hours => 24)"
    )
    params: list[Any] = [tenant_id, now]
    if exclude_outbox_id is not None:
        sql += " AND outbox_id <> %s"
        params.append(exclude_outbox_id)
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row is not None else 0


def had_marketing_24h(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
) -> bool:
    """True when this customer was handed a marketing message in the last 24h
    (ledger `handed_off`). Feeds ONLY the MarketingOptoutRatioHigh numerator;
    the answer is a boolean - nothing identifying leaves the database."""
    row = conn.execute(
        "SELECT 1 FROM proactive_ledger "
        "WHERE tenant_id = %s AND customer_id = %s AND message_class = 'marketing' "
        "AND status = 'handed_off' "
        "AND reserved_at > clock_timestamp() - make_interval(hours => 24) LIMIT 1",
        (tenant_id, customer_id),
    ).fetchone()
    return row is not None


def count_eligible_subscribers(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID,
) -> int:
    """H20/H48: a COUNT ONLY (never phones, never text). Eligible = the
    customer's LATEST marketing consent row is granted with source
    'customer_message_optin' (H95 - checkout_optin/import stay blocked) AND
    no marketing suppression (H78)."""
    row = conn.execute(
        "SELECT count(*) FROM ( "
        "  SELECT DISTINCT ON (cs.customer_id) cs.customer_id, cs.granted, cs.source "
        "    FROM consents cs "
        "   WHERE cs.tenant_id = %s AND cs.scope = 'marketing' "
        "   ORDER BY cs.customer_id, cs.created_at DESC, cs.id DESC "
        ") latest "
        "WHERE latest.granted AND latest.source = 'customer_message_optin' "
        "AND NOT EXISTS (SELECT 1 FROM suppressions s "
        " WHERE s.tenant_id = %s AND s.customer_id = latest.customer_id "
        "   AND s.scope = 'marketing')",
        (tenant_id, tenant_id),
    ).fetchone()
    return int(row[0]) if row is not None else 0


def resolve_tenant(conn: psycopg.Connection, *, platform_ref: str) -> uuid.UUID | None:
    """Operator-CLI helper (migration role): tenants.platform_ref -> id."""
    row = conn.execute(
        "SELECT id FROM tenants WHERE platform_ref = %s", (platform_ref,),
    ).fetchone()
    return None if row is None else row[0]


def read_tenant_channels(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """The tenant's ai_core WhatsApp channels with their number_health state -
    the preflight/status inputs. Identifiers + states only (H48): never a phone."""
    rows = conn.execute(
        "SELECT ca.id, ca.status, nh.state, nh.warmup_started_at "
        "FROM channel_accounts ca "
        "LEFT JOIN number_health nh ON nh.channel_account_id = ca.id "
        "WHERE ca.tenant_id = %s AND ca.type = 'whatsapp_baileys' "
        "ORDER BY ca.created_at, ca.id",
        (tenant_id,),
    ).fetchall()
    return [{"channel_id": r[0], "status": r[1], "health_state": r[2], "warmup_started_at": r[3]}
            for r in rows]


def preview_cart_counts(conn: psycopg.Connection, *, tenant_id: uuid.UUID) -> dict[str, int]:
    """P3.4 `preview` (dry-run): how many OPEN carts belong to eligible
    subscribers (would reach the gate with consent) versus not (would be dropped
    `no_consent`/`suppressed`). COUNTS ONLY - never a phone, a cart line or a
    customer id leaves the database (H20/H48)."""
    row = conn.execute(
        "WITH latest AS ( "
        "  SELECT DISTINCT ON (customer_id) customer_id, granted, source "
        "    FROM consents WHERE tenant_id = %s AND scope = 'marketing' "
        "   ORDER BY customer_id, created_at DESC, id DESC ), "
        "open_carts AS (SELECT customer_id FROM carts WHERE tenant_id = %s AND status = 'open') "
        "SELECT count(*), "
        "       count(*) FILTER (WHERE l.granted AND l.source = 'customer_message_optin' "
        "         AND NOT EXISTS (SELECT 1 FROM suppressions s WHERE s.tenant_id = %s "
        "                          AND s.customer_id = oc.customer_id AND s.scope = 'marketing')) "
        "  FROM open_carts oc LEFT JOIN latest l ON l.customer_id = oc.customer_id",
        (tenant_id, tenant_id, tenant_id),
    ).fetchone()
    total, eligible = (int(row[0]), int(row[1])) if row is not None else (0, 0)
    return {"open_carts": total, "eligible": eligible, "ineligible": total - eligible}
