"""core/app/db/repos_summary.py - the P1.5b rolling-summary raw SQL (H2).

H43: conversations.summary is internal bot memory (context, not content). It is
read ONLY to build the prompt input and written ONLY to conversations.summary -
never read by compose.py, never passed to insert_outbox, never shown to a client
or staff. To keep that enforceable, this SQL lives in its OWN module (NOT
repos_outbox.py, which defines insert_outbox and is therefore on the S9 send
path). Only app/workers/summary.py imports this module.
"""
from __future__ import annotations

import uuid
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

# V7 / V11: the ONLY keys conversations.slots may carry in this batch, and every
# one of them is written by CODE (never by the model, H38 in spirit). Same
# whitelist pattern as INBOX_EVENT_ALLOWED_KEYS (P1.3).
SLOT_ALLOWED_KEYS = frozenset({
    "summary_seq",             # last_inbound_seq the last summary was built from
    "last_search_query_hash",  # fingerprint of the last search query (not the text, H20)
    "last_shown_product_ids",  # ids of the last shown cards (<= 3)
    "optout",                  # bool from suppressions
})


def validate_slots(slots: dict[str, Any]) -> None:
    """Any key outside the whitelist raises ValueError (V11)."""
    disallowed = set(slots) - SLOT_ALLOWED_KEYS
    if disallowed:
        raise ValueError(f"slots has non-whitelisted keys: {sorted(disallowed)}")


MAX_SHOWN_PRODUCTS = 3


def record_shown_products(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID,
    platform_product_ids: list[str],
) -> None:
    """F-P4-07 (Task 18b-2a): the turn records the platform_product_id of the
    cards it just showed (display order, <= 3) - written by CODE, never by the
    model. Merges into slots like update_summary (other keys are preserved)."""
    slots = {"last_shown_product_ids": [str(p) for p in platform_product_ids[:MAX_SHOWN_PRODUCTS]]}
    validate_slots(slots)
    conn.execute(
        "UPDATE conversations SET slots = COALESCE(slots, '{}'::jsonb) || %s::jsonb "
        "WHERE tenant_id = %s AND id = %s",
        (Jsonb(slots), tenant_id, conversation_id),
    )


def list_conversations_needing_summary(
    conn: psycopg.Connection, *, limit: int, trigger_messages: int,
    min_between: int, max_per_day: int,
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Cross-tenant candidates via the SECURITY DEFINER
    app.list_conversations_needing_summary() (0010) - pointers only
    (conversation_id, tenant_id), never any tenant content. system_tx()."""
    rows = conn.execute(
        "SELECT * FROM app.list_conversations_needing_summary(%s, %s, %s, %s)",
        (limit, trigger_messages, min_between, max_per_day),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def read_summary_state(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID,
) -> tuple[str | None, dict[str, Any], int, int] | None:
    """(summary, slots, last_inbound_seq, message_seq) or None when the
    conversation is gone. Called inside a tenant_tx()."""
    row = conn.execute(
        "SELECT summary, slots, last_inbound_seq, message_seq FROM conversations "
        "WHERE tenant_id = %s AND id = %s",
        (tenant_id, conversation_id),
    ).fetchone()
    if row is None:
        return None
    slots = row[1] if isinstance(row[1], dict) else {}
    return row[0], slots, int(row[2]), int(row[3])


def read_recent_messages(
    conn: psycopg.Connection, *, conversation_id: uuid.UUID, limit: int,
) -> list[tuple[int, str, str]]:
    """Last `limit` messages (both directions), oldest first: (seq, direction,
    body). `body` is empty string for a NULL body."""
    rows = conn.execute(
        "SELECT seq, direction, body FROM messages "
        "WHERE conversation_id = %s ORDER BY seq DESC LIMIT %s",
        (conversation_id, limit),
    ).fetchall()
    rows = list(rows)
    rows.reverse()
    return [(int(r[0]), r[1], r[2] or "") for r in rows]


def count_summaries_today(
    conn: psycopg.Connection, *, conversation_id: uuid.UUID,
) -> int:
    """Number of summary calls for this conversation in the last 24h (V9 daily
    ceiling). llm_calls is the state-of-record; no new counter column."""
    row = conn.execute(
        "SELECT count(*) FROM llm_calls WHERE conversation_id = %s "
        "AND purpose = 'summary' AND created_at > now() - interval '24 hours'",
        (conversation_id,),
    ).fetchone()
    return int(row[0]) if row is not None else 0


def update_summary(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID,
    summary: str, slots: dict[str, Any],
) -> None:
    """Write the summary text + merge the given slots (jsonb || overwrites the
    named keys and preserves the rest). slots is validated BEFORE any write."""
    validate_slots(slots)
    conn.execute(
        "UPDATE conversations SET summary = %s, "
        "slots = COALESCE(slots, '{}'::jsonb) || %s::jsonb "
        "WHERE tenant_id = %s AND id = %s",
        (tenant_id, Jsonb(slots), conversation_id),
    )
