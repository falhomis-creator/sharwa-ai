"""core/app/db/repos_ingest.py - the ingest worker's raw SQL (P1.1.3/P1.1.4).

Every SQL string the worker needs lives here, and ONLY here (hunt_gate rules
6/8: psycopg import + raw SQL stay inside core/app/db/). Callers in
app/workers/ pass an already-open connection from tenant_tx()/system_tx() and
never see a SQL string or psycopg import themselves.

The single atomic transaction (H17) is the heart of this batch. The worker
opens `with tenant_tx(tenant_id) as conn:` and calls the step functions below
in order; tenant_tx()'s `conn.transaction()` COMMITs when the block exits
cleanly, so a crash at any point rolls everything back and the entry stays in
the PEL to be reclaimed. The DB-level UNIQUE constraints (inbound_events,
customers, conversations, messages(conversation_id,seq)) are the permanent,
Redis-independent idempotency guarantee (disaster #5).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import errors as pg_errors
from psycopg.types.json import Jsonb
from psycopg_pool import PoolTimeout


@dataclass(frozen=True)
class ConversationRow:
    id: uuid.UUID
    bot_status: str
    epoch: int
    version: int


def classify_db_error(exc: BaseException) -> str:
    """'transient' for a retryable DB failure (connection loss, a UNIQUE race on
    messages(conversation_id,seq), serialization failure, pool timeout) and
    'permanent' otherwise. The caller retries transient (never swallows, H3);
    permanent errors propagate and fail the process fast."""
    if isinstance(exc, pg_errors.UniqueViolation):
        return "transient"
    if isinstance(exc, (pg_errors.OperationalError, pg_errors.SerializationFailure)):
        return "transient"
    if isinstance(exc, (pg_errors.InterfaceError, PoolTimeout)):
        return "transient"
    return "permanent"


def resolve_session(
    conn: psycopg.Connection, session_id: str,
) -> tuple[uuid.UUID, uuid.UUID, str] | None:
    """The only bridge from a WAL session_id to (channel_account_id, tenant_id,
    engine) - the SECURITY DEFINER app.resolve_session(), GRANTed to
    sharwa_system. None => unknown session (caller ACKs + skips)."""
    row = conn.execute("SELECT * FROM app.resolve_session(%s)", (session_id,)).fetchone()
    if row is None:
        return None
    return row[0], row[1], row[2]


def insert_inbound_event(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID,
    channel_account_id: uuid.UUID, provider_message_id: str,
) -> bool:
    """True when a NEW event row was inserted; False when it already existed
    (duplicate). On False the caller aborts the transaction with no further
    writes, XACKs, and bumps ingest_core_duplicates_total."""
    row = conn.execute(
        "INSERT INTO inbound_events (tenant_id, channel_account_id, provider_message_id) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (channel_account_id, provider_message_id) DO NOTHING "
        "RETURNING id",
        (tenant_id, channel_account_id, provider_message_id),
    ).fetchone()
    return row is not None


def upsert_customer(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, wa_id: str,
    phone_e164: str | None, display_name: str | None,
) -> uuid.UUID:
    """Insert or update a customer by (tenant_id, wa_id). Never overwrites an
    existing phone_e164/display_name with NULL (COALESCE), and never fabricates
    a phone from wa_id (H2)."""
    row = conn.execute(
        "INSERT INTO customers (tenant_id, wa_id, phone_e164, display_name) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (tenant_id, wa_id) DO UPDATE "
        "SET phone_e164 = COALESCE(EXCLUDED.phone_e164, customers.phone_e164), "
        "    display_name = COALESCE(EXCLUDED.display_name, customers.display_name) "
        "RETURNING id",
        (tenant_id, wa_id, phone_e164, display_name),
    ).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING id produced no row")
    return row[0]


def upsert_conversation(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID,
    channel_account_id: uuid.UUID, customer_id: uuid.UUID,
) -> ConversationRow:
    """Insert (or bump last_message_at on) the 1:1 conversation for a
    (tenant, channel, customer) triple. last_message_at is one of the columns
    sharwa_app is GRANTed to UPDATE (§6.2); bot_status/epoch/version are NOT."""
    row = conn.execute(
        "INSERT INTO conversations (tenant_id, channel_account_id, customer_id) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (tenant_id, channel_account_id, customer_id) DO UPDATE "
        "SET last_message_at = now() "
        "RETURNING id, bot_status, epoch, version",
        (tenant_id, channel_account_id, customer_id),
    ).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING produced no row")
    return ConversationRow(id=row[0], bot_status=row[1], epoch=row[2], version=row[3])


def fetch_conversation(
    conn: psycopg.Connection, *, conversation_id: uuid.UUID,
) -> ConversationRow:
    """Re-read a conversation's optimistic-lock fields (used for the one retry
    of a stale reopen)."""
    row = conn.execute(
        "SELECT id, bot_status, epoch, version FROM conversations WHERE id = %s",
        (conversation_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("conversation vanished mid-transaction")
    return ConversationRow(id=row[0], bot_status=row[1], epoch=row[2], version=row[3])


def reopen_closed_conversation(
    conn: psycopg.Connection, *, conversation_id: uuid.UUID, expected_version: int,
) -> bool:
    """Reopen a closed conversation with reason 'reopened_by_customer' via the
    SECURITY DEFINER app.set_bot_status() - the ONLY way to change bot_status.
    Returns False when the version was stale (caller re-reads + retries once,
    then treats as transient)."""
    row = conn.execute(
        "SELECT app.set_bot_status(%s, %s, %s, %s, %s)",
        (conversation_id, expected_version, "active", "reopened_by_customer", None),
    ).fetchone()
    if row is None:
        return False
    return row[0] is not None


def insert_message(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID,
    direction: str, sent_by: str, msg_type: str, body: str | None,
    media_object_key: str | None, media_type: str | None,
    location: dict[str, Any] | None, provider_message_id: str | None,
    status: str, staff_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, int]:
    """Insert one messages row. `seq` is NOT set here - app.trg_messages_seq()
    assigns it atomically and, for direction='out'/sent_by='staff', pauses the
    bot in the SAME transaction (disaster #11). location is stored as JSONB.
    Returns (id, seq) so the caller can write the matching inbox_events row."""
    row = conn.execute(
        "INSERT INTO messages "
        "(tenant_id, conversation_id, direction, sent_by, staff_id, type, body, "
        " media_object_key, media_type, location, provider_message_id, status) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING id, seq",
        (
            tenant_id, conversation_id, direction, sent_by, staff_id, msg_type,
            body, media_object_key, media_type,
            Jsonb(location) if location is not None else None,
            provider_message_id, status,
        ),
    ).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING id, seq produced no row")
    return row[0], row[1]


OPTOUT_SCOPES = ("marketing", "back_in_stock", "review_request")
OPTOUT_REASON = "customer_message_optout"


def insert_suppressions(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
    scopes: tuple[str, ...], reason: str,
) -> int:
    """Write suppression rows (idempotent ON CONFLICT DO NOTHING). order_updates
    is deliberately never suppressed (service fails open, marketing fails
    closed - H4). Returns the number of NEW rows written."""
    written = 0
    for scope in scopes:
        cur = conn.execute(
            "INSERT INTO suppressions (tenant_id, customer_id, scope, reason) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (tenant_id, customer_id, scope) DO NOTHING",
            (tenant_id, customer_id, scope, reason),
        )
        written += cur.rowcount
    return written
