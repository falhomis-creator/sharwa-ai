"""core/app/db/repos_outbox.py - the outbound (P1.2) raw SQL.

Same discipline as repos_ingest.py: every SQL string for the turn engine, the
dispatcher and the evt:{shard} consumer lives here and ONLY here; callers pass
an already-open connection from tenant_tx()/system_tx().

Hard guarantees (H22/H24): idempotency_key is the UNIQUE outbox column AND the
gateway client_msg_id; bot_status/epoch/version are never written directly.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class ConversationLockRow:
    id: uuid.UUID
    bot_status: str
    epoch: int
    version: int
    last_inbound_seq: int
    last_processed_seq: int


@dataclass(frozen=True)
class OutboxRow:
    id: uuid.UUID
    tenant_id: uuid.UUID
    conversation_id: uuid.UUID | None
    channel_account_id: uuid.UUID
    idempotency_key: str
    origin: str
    message_class: str
    expected_epoch: int | None
    to_wa_id: str
    payload: dict[str, Any]
    attempts: int


def lock_conversation(
    conn: psycopg.Connection, *, conversation_id: uuid.UUID, tenant_id: uuid.UUID,
) -> ConversationLockRow | None:
    """FOR UPDATE SKIP LOCKED - no row => another turn is already on it."""
    row = conn.execute(
        "SELECT id, bot_status, epoch, version, last_inbound_seq, last_processed_seq "
        "FROM conversations WHERE id = %s AND tenant_id = %s FOR UPDATE SKIP LOCKED",
        (conversation_id, tenant_id),
    ).fetchone()
    if row is None:
        return None
    return ConversationLockRow(
        id=row[0], bot_status=row[1], epoch=row[2], version=row[3],
        last_inbound_seq=row[4], last_processed_seq=row[5],
    )


def fetch_needs_turn(conn: psycopg.Connection, limit: int) -> list[tuple[uuid.UUID, uuid.UUID]]:
    rows = conn.execute(
        "SELECT id, tenant_id FROM conversations WHERE needs_turn "
        "ORDER BY last_message_at LIMIT %s",
        (limit,),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def count_consecutive_bot_replies(conn: psycopg.Connection, conversation_id: uuid.UUID) -> int:
    """Outbound bot messages after the latest inbound message (H22 cap)."""
    row = conn.execute(
        "SELECT count(*) FROM messages "
        "WHERE conversation_id = %s AND direction = 'out' AND sent_by = 'bot' "
        "AND seq > COALESCE((SELECT max(seq) FROM messages "
        "   WHERE conversation_id = %s AND direction = 'in'), 0)",
        (conversation_id, conversation_id),
    ).fetchone()
    assert row is not None
    return int(row[0])


def latest_inbound_texts(
    conn: psycopg.Connection, conversation_id: uuid.UUID, after_seq: int,
) -> list[tuple[int, str | None]]:
    """(seq, body) of unprocessed inbound messages (seq > after_seq), oldest first."""
    rows = conn.execute(
        "SELECT seq, body FROM messages "
        "WHERE conversation_id = %s AND direction = 'in' AND seq > %s ORDER BY seq",
        (conversation_id, after_seq),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def effective_switch(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID,
    channel_account_id: uuid.UUID, capability: str,
) -> str | None:
    """DB is the kill-switch authority (app.effective_switch)."""
    row = conn.execute(
        "SELECT app.effective_switch(%s, %s, %s)",
        (tenant_id, channel_account_id, capability),
    ).fetchone()
    return None if row is None else row[0]


def insert_outbox(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID,
    channel_account_id: uuid.UUID, idempotency_key: str, origin: str,
    message_class: str, expected_epoch: int | None, to_wa_id: str,
    template_id: str, text: str,
) -> uuid.UUID:
    row = conn.execute(
        "INSERT INTO outbox "
        "(tenant_id, conversation_id, channel_account_id, idempotency_key, "
        " origin, message_class, expected_epoch, to_wa_id, payload) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            tenant_id, conversation_id, channel_account_id, idempotency_key,
            origin, message_class, expected_epoch, to_wa_id,
            Jsonb({"template": template_id, "text": text}),
        ),
    ).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING id produced no row")
    return row[0]


def insert_verifier_block(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID | None,
    reason: str, draft_excerpt: str | None,
) -> int:
    """Write one verifier_blocks audit row (H48's single exception). Append-only:
    0001 REVOKEs UPDATE/DELETE on the table. `draft_excerpt` is caller-masked
    (mask_phones) and capped before it reaches this function."""
    row = conn.execute(
        "INSERT INTO verifier_blocks (tenant_id, conversation_id, reason, draft_excerpt) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (tenant_id, conversation_id, reason, draft_excerpt),
    ).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING id produced no row")
    return int(row[0])


def order_lookup_blocked(conn: psycopg.Connection, *, customer_id: uuid.UUID, order_ref_hash: str) -> bool:
    """app.order_lookup_blocked (0001, STABLE) - the single authority for the
    3/24h + 5/24h brute-force thresholds. Never re-implemented in Python."""
    row = conn.execute(
        "SELECT app.order_lookup_blocked(%s, %s)", (customer_id, order_ref_hash),
    ).fetchone()
    return bool(row is not None and row[0])


def insert_order_lookup_attempt(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID | None,
    customer_id: uuid.UUID, path: str, order_ref_hash: str, outcome: str,
) -> None:
    """Append-only audit of one order-tracking attempt (0001 REVOKEs UPDATE/DELETE).
    `order_ref_hash` is the HMAC, never the raw order number (H54)."""
    conn.execute(
        "INSERT INTO order_lookup_attempts "
        "(tenant_id, conversation_id, customer_id, path, order_ref_hash, outcome) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (tenant_id, conversation_id, customer_id, path, order_ref_hash, outcome),
    )


def count_order_lookup_attempts_for_conversation(
    conn: psycopg.Connection, *, conversation_id: uuid.UUID,
) -> int:
    """The operational per-conversation-per-day ceiling (N1, P1.7 audit): count
    EVERY attempt of this conversation in the last 24h, whatever its outcome.
    Above ORDER_LOOKUP_MAX_PER_CONVERSATION_PER_DAY the coordinator blocks - this
    is a platform-call cost ceiling on top of the 3/24h + 5/24h brute-force
    thresholds, not a replacement for them."""
    row = conn.execute(
        "SELECT count(*) FROM order_lookup_attempts "
        "WHERE conversation_id = %s AND created_at > now() - interval '24 hours'",
        (conversation_id,),
    ).fetchone()
    return int(row[0]) if row is not None else 0


def mark_turn_processed(
    conn: psycopg.Connection, *, conversation_id: uuid.UUID, last_processed_seq: int,
) -> None:
    conn.execute(
        "UPDATE conversations SET last_processed_seq = %s, needs_turn = false WHERE id = %s",
        (last_processed_seq, conversation_id),
    )


def claim_outbox(
    conn: psycopg.Connection, *, limit: int, lease_s: int, marketing_limit: int = 4,
) -> list[OutboxRow]:
    """app.claim_outbox (SECURITY DEFINER, sharwa_system). F-P1-12: typed params
    (integer/interval) and by-NAME columns - never SELECT * by position."""
    # F-P3-11: psycopg 3 Connection.execute has no row_factory kwarg - a dict
    # row factory must be attached to a cursor.
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            "SELECT id, tenant_id, conversation_id, channel_account_id, idempotency_key, "
            "origin, message_class, expected_epoch, to_wa_id, payload, attempts, status "
            "FROM app.claim_outbox(%s::integer, make_interval(secs => %s), %s::integer)",
            (limit, lease_s, marketing_limit),
        ).fetchall()
    return [_outbox_from_row(r) for r in rows]


def claim_due_turns(conn: psycopg.Connection, *, limit: int) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """app.claim_due_turns (SECURITY DEFINER, cross-tenant) -> (conversation_id,
    tenant_id) candidates with needs_turn=true, ordered by last_message_at."""
    rows = conn.execute("SELECT * FROM app.claim_due_turns(%s)", (limit,)).fetchall()
    return [(r[0], r[1]) for r in rows]


def _outbox_from_row(row: dict[str, Any]) -> OutboxRow:
    # F-P1-12: columns are read BY NAME (dict_row), never by position - a future
    # ALTER TABLE that adds a column can no longer silently shift `attempts`.
    return OutboxRow(
        id=row["id"], tenant_id=row["tenant_id"], conversation_id=row["conversation_id"],
        channel_account_id=row["channel_account_id"], idempotency_key=row["idempotency_key"],
        origin=row["origin"], message_class=row["message_class"],
        expected_epoch=row["expected_epoch"], to_wa_id=row["to_wa_id"],
        payload=row["payload"], attempts=int(row["attempts"]),
    )


def read_channel_session_id(conn: psycopg.Connection, channel_account_id: uuid.UUID) -> str | None:
    row = conn.execute(
        "SELECT session_id FROM channel_accounts WHERE id = %s", (channel_account_id,),
    ).fetchone()
    return None if row is None else row[0]


def read_conversation_epoch_status(
    conn: psycopg.Connection, conversation_id: uuid.UUID,
) -> tuple[int, str] | None:
    row = conn.execute(
        "SELECT epoch, bot_status FROM conversations WHERE id = %s", (conversation_id,),
    ).fetchone()
    return None if row is None else (row[0], row[1])


def read_conversation_version(conn: psycopg.Connection, conversation_id: uuid.UUID) -> int | None:
    row = conn.execute(
        "SELECT version FROM conversations WHERE id = %s", (conversation_id,),
    ).fetchone()
    return None if row is None else int(row[0])


def customer_id_for_conversation(conn: psycopg.Connection, conversation_id: uuid.UUID) -> uuid.UUID | None:
    row = conn.execute(
        "SELECT customer_id FROM conversations WHERE id = %s", (conversation_id,),
    ).fetchone()
    return None if row is None else row[0]


def resolve_channel_for_conversation(conn: psycopg.Connection, conversation_id: uuid.UUID) -> uuid.UUID:
    row = conn.execute(
        "SELECT channel_account_id FROM conversations WHERE id = %s", (conversation_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("conversation vanished mid-transaction")
    return row[0]


def wa_id_for_conversation(conn: psycopg.Connection, conversation_id: uuid.UUID) -> str:
    row = conn.execute(
        "SELECT c.wa_id FROM customers c JOIN conversations v ON v.customer_id = c.id "
        "WHERE v.id = %s", (conversation_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("customer vanished mid-transaction")
    return row[0]


def last_inbound_seq(conn: psycopg.Connection, conversation_id: uuid.UUID) -> int:
    row = conn.execute(
        "SELECT last_inbound_seq FROM conversations WHERE id = %s", (conversation_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("conversation vanished mid-transaction")
    return int(row[0])


def has_suppression(
    conn: psycopg.Connection, tenant_id: uuid.UUID, customer_id: uuid.UUID, scope: str,
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM suppressions WHERE tenant_id = %s AND customer_id = %s AND scope = %s",
        (tenant_id, customer_id, scope),
    ).fetchone()
    return row is not None


def mark_outbox_status(
    conn: psycopg.Connection, *, outbox_id: uuid.UUID, status: str,
    provider_message_id: str | None = None,
) -> None:
    conn.execute(
        "UPDATE outbox SET status = %s, provider_message_id = COALESCE(%s, provider_message_id) "
        "WHERE id = %s",
        (status, provider_message_id, outbox_id),
    )


def mark_outbox_sent(
    conn: psycopg.Connection, *, outbox_id: uuid.UUID, provider_message_id: str | None = None,
) -> None:
    conn.execute(
        "UPDATE outbox SET status = 'sent', sent_at = now(), "
        "provider_message_id = COALESCE(%s, provider_message_id) WHERE id = %s",
        (provider_message_id, outbox_id),
    )


def claim_sent_transition(
    conn: psycopg.Connection, *, idempotency_key: str, wa_message_id: str,
) -> OutboxRow | None:
    """State-transition guard: returns the row only on the first sent transition
    (status <> 'sent'); None when already processed (idempotent)."""
    row = conn.execute(
        "UPDATE outbox SET status = 'sent', sent_at = now(), provider_message_id = %s "
        "WHERE idempotency_key = %s AND status <> 'sent' "
        "RETURNING id, tenant_id, conversation_id, channel_account_id, idempotency_key, "
        "          origin, message_class, expected_epoch, to_wa_id, payload, attempts",
        (wa_message_id, idempotency_key),
    ).fetchone()
    if row is None:
        return None
    return _outbox_from_row(row)


def outbox_exists(conn: psycopg.Connection, idempotency_key: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM outbox WHERE idempotency_key = %s", (idempotency_key,),
    ).fetchone()
    return row is not None


def mark_outbox_by_idempotency(
    conn: psycopg.Connection, *, idempotency_key: str, status: str,
) -> None:
    """Mark a row by idempotency_key (evt path), never overwriting a 'sent'."""
    conn.execute(
        "UPDATE outbox SET status = %s WHERE idempotency_key = %s AND status <> 'sent'",
        (status, idempotency_key),
    )


def set_bot_status(
    conn: psycopg.Connection, *, conversation_id: uuid.UUID, expected_version: int,
    new_status: str, reason: str, staff_id: uuid.UUID | None = None,
) -> int | None:
    """The ONLY way to change bot_status (SECURITY DEFINER, optimistic lock).
    Returns the new epoch, or None when the version is stale. Lives here because
    all this batch's SQL lives in repos_outbox (audit §3)."""
    row = conn.execute(
        "SELECT app.set_bot_status(%s, %s, %s, %s, %s)",
        (conversation_id, expected_version, new_status, reason, staff_id),
    ).fetchone()
    if row is None:
        return None
    return row[0]


def requeue_with_backoff(
    conn: psycopg.Connection, *, outbox_id: uuid.UUID, delay_s: float,
) -> None:
    """D3: put a 429/retryable row back to 'pending' with a FUTURE next_attempt_at
    (exponential backoff + jitter, bounded) - so a throttled send is deferred,
    never left spinning in 'sending' until the lease expires."""
    conn.execute(
        "UPDATE outbox SET status = 'pending', next_attempt_at = now() + make_interval(secs => %s) "
        "WHERE id = %s",
        (delay_s, outbox_id),
    )


def outbox_stats(conn: psycopg.Connection) -> list[tuple[str, int, float]]:
    """Per-message_class (depth, oldest_pending_seconds) via app.outbox_stats()
    (SECURITY DEFINER - the system role has no direct SELECT on outbox). F-P1-12."""
    rows = conn.execute("SELECT * FROM app.outbox_stats()").fetchall()
    return [(r[0], int(r[1]), float(r[2])) for r in rows]


def insert_outbound_message(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID,
    sent_by: str, body: str, provider_message_id: str,
) -> uuid.UUID:
    """The outbound message row, written only on the real `sent` event (H22)."""
    row = conn.execute(
        "INSERT INTO messages "
        "(tenant_id, conversation_id, direction, sent_by, type, body, "
        " provider_message_id, status) "
        "VALUES (%s, %s, 'out', %s, 'text', %s, %s, 'sent') RETURNING id",
        (tenant_id, conversation_id, sent_by, body, provider_message_id),
    ).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING id produced no row")
    return row[0]
