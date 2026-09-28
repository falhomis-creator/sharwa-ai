"""core/app/db/repos_inbox.py - the P1.3 REST-inbox raw SQL (H2).

Every SQL string the inbox routes need lives here, and ONLY here. Callers in
app/api/routes_inbox.py pass an already-open connection from tenant_tx() and
never see a SQL string or a psycopg import themselves. RLS is the real tenant
boundary on every query; the explicit `tenant_id = %s` predicates are the
statement of intent (H29): a resource owned by another tenant returns zero rows,
which the route turns into NOT_FOUND - never FORBIDDEN - so another tenant's
existence is never disclosed.

The employee reply is deliberately NOT implemented here via a bot_status write:
`insert_staff_message` relies on the existing `app.trg_messages_seq()` trigger
(sent_by='staff', direction='out' pauses the bot in the SAME transaction). This
is the architectural requirement - no manual set_bot_status on the reply path.
"""
from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class StaffRow:
    id: uuid.UUID
    role: str
    active: bool


@dataclass(frozen=True)
class ReplyConversationView:
    id: uuid.UUID
    bot_status: str
    version: int
    customer_id: uuid.UUID
    channel_account_id: uuid.UUID
    to_wa_id: str


# --- staff resolution (RBAC, I6) ---------------------------------------------


def resolve_staff_member(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, platform_user_id: str,
) -> StaffRow | None:
    """The staff_members row for a JWT `sub`, via UNIQUE(tenant_id,
    platform_user_id). None => the caller raises STAFF_NOT_PROVISIONED (never an
    automatic row creation)."""
    row = conn.execute(
        "SELECT id, role, active FROM staff_members WHERE tenant_id = %s AND platform_user_id = %s",
        (tenant_id, platform_user_id),
    ).fetchone()
    if row is None:
        return None
    return StaffRow(id=row[0], role=row[1], active=row[2])


# --- api_idempotency (H30) ---------------------------------------------------


def fetch_idempotency(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, idempotency_key: str,
) -> tuple[str, int, dict[str, Any]] | None:
    """(request_hash, response_status, response_body) or None when the key is new."""
    row = conn.execute(
        "SELECT request_hash, response_status, response_body FROM api_idempotency "
        "WHERE tenant_id = %s AND idempotency_key = %s",
        (tenant_id, idempotency_key),
    ).fetchone()
    if row is None:
        return None
    return row[0], row[1], row[2]


def store_idempotency(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, idempotency_key: str,
    request_hash: str, response_status: int, response_body: dict[str, Any],
) -> None:
    conn.execute(
        "INSERT INTO api_idempotency "
        "(tenant_id, idempotency_key, request_hash, response_status, response_body) "
        "VALUES (%s, %s, %s, %s, %s)",
        (tenant_id, idempotency_key, request_hash, response_status, Jsonb(response_body)),
    )


# --- conversations (I1, keyset cursor - no OFFSET) ---------------------------


_CONVERSATION_COLS = (
    "id", "bot_status", "customer_id", "channel_account_id", "assigned_staff_id",
    "epoch", "version", "handoff_reason", "last_message_at",
)


def list_conversations(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, status: str | None,
    assigned: bool | uuid.UUID | None, cursor_time: Any, cursor_id: uuid.UUID | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Keyset-paginated conversations (last_message_at DESC, id DESC). `assigned`
    is True (IS NOT NULL), False (IS NULL), a staff uuid, or None (no filter).
    No OFFSET anywhere (G8)."""
    clauses = ["tenant_id = %s"]
    params: list[Any] = [tenant_id]
    if status is not None:
        clauses.append("bot_status = %s")
        params.append(status)
    if assigned is True:
        clauses.append("assigned_staff_id IS NOT NULL")
    elif assigned is False:
        clauses.append("assigned_staff_id IS NULL")
    elif assigned is not None:
        clauses.append("assigned_staff_id = %s")
        params.append(assigned)
    if cursor_time is not None and cursor_id is not None:
        clauses.append("(last_message_at, id) < (%s::timestamptz, %s::uuid)")
        params.extend([cursor_time, cursor_id])
    params.append(limit)
    rows = conn.execute(
        "SELECT id, bot_status, customer_id, channel_account_id, assigned_staff_id, "
        "       epoch, version, handoff_reason, last_message_at "
        f"FROM conversations WHERE {' AND '.join(clauses)} "
        "ORDER BY last_message_at DESC NULLS LAST, id DESC LIMIT %s",
        tuple(params),
    ).fetchall()
    return [dict(zip(_CONVERSATION_COLS, r, strict=True)) for r in rows]


def fetch_conversation(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, bot_status, customer_id, channel_account_id, assigned_staff_id, "
        "       epoch, version, handoff_reason, needs_turn, last_message_at, created_at "
        "FROM conversations WHERE id = %s AND tenant_id = %s",
        (conversation_id, tenant_id),
    ).fetchone()
    if row is None:
        return None
    cols = ("id", "bot_status", "customer_id", "channel_account_id", "assigned_staff_id",
            "epoch", "version", "handoff_reason", "needs_turn", "last_message_at", "created_at")
    return dict(zip(cols, row, strict=True))


def fetch_conversation_reply_view(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID,
) -> ReplyConversationView | None:
    row = conn.execute(
        "SELECT c.id, c.bot_status, c.version, c.customer_id, c.channel_account_id, cu.wa_id "
        "FROM conversations c JOIN customers cu ON cu.id = c.customer_id "
        "WHERE c.id = %s AND c.tenant_id = %s",
        (conversation_id, tenant_id),
    ).fetchone()
    if row is None:
        return None
    return ReplyConversationView(
        id=row[0], bot_status=row[1], version=row[2], customer_id=row[3],
        channel_account_id=row[4], to_wa_id=row[5],
    )


def fetch_customer_card(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
) -> dict[str, Any]:
    """Local-only customer card (§4.2): no sharwa_saas call, no order history."""
    row = conn.execute(
        "SELECT wa_id, phone_e164, display_name FROM customers WHERE id = %s AND tenant_id = %s",
        (customer_id, tenant_id),
    ).fetchone()
    if row is None:
        return {"wa_id": None, "phone_e164": None, "display_name": None,
                "message_count": 0, "suppression_scopes": [], "last_message_at": None}
    scopes = conn.execute(
        "SELECT scope FROM suppressions WHERE tenant_id = %s AND customer_id = %s",
        (tenant_id, customer_id),
    ).fetchall()
    counts = conn.execute(
        "SELECT count(*), max(m.created_at) FROM messages m "
        "JOIN conversations c ON c.id = m.conversation_id "
        "WHERE c.customer_id = %s AND c.tenant_id = %s",
        (customer_id, tenant_id),
    ).fetchone()
    message_count = int(counts[0]) if counts is not None else 0
    last_message_at = counts[1] if counts is not None else None
    return {
        "wa_id": row[0], "phone_e164": row[1], "display_name": row[2],
        "message_count": message_count,
        "suppression_scopes": [s[0] for s in scopes],
        "last_message_at": last_message_at,
    }


# --- messages (I1) -----------------------------------------------------------


def list_messages(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID,
    before_seq: int | None, limit: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, seq, direction, sent_by, type, body, provider_message_id, status, created_at "
        "FROM messages WHERE conversation_id = %s AND tenant_id = %s "
        "  AND (%s::bigint IS NULL OR seq < %s) "
        "ORDER BY seq DESC LIMIT %s",
        (conversation_id, tenant_id, before_seq, before_seq, limit),
    ).fetchall()
    cols = ("id", "seq", "direction", "sent_by", "type", "body",
            "provider_message_id", "status", "created_at")
    return [dict(zip(cols, r, strict=True)) for r in rows]


# --- employee reply (I2, the critical path) ----------------------------------


def insert_staff_message(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID,
    staff_id: uuid.UUID, body: str,
) -> tuple[uuid.UUID, int]:
    """Insert the staff reply. `app.trg_messages_seq()` assigns `seq` AND pauses
    the bot (bot_status='paused_human', epoch+1, version+1,
    handoff_reason='staff_replied') in this same transaction - the route must NOT
    call set_bot_status here (G3)."""
    row = conn.execute(
        "INSERT INTO messages (tenant_id, conversation_id, direction, sent_by, "
        "                       staff_id, type, body, status) "
        "VALUES (%s, %s, 'out', 'staff', %s, 'text', %s, 'queued') RETURNING id, seq",
        (tenant_id, conversation_id, staff_id, body),
    ).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING id, seq produced no row")
    return row[0], row[1]


def read_conversation_state(
    conn: psycopg.Connection, *, conversation_id: uuid.UUID,
) -> tuple[int, str] | None:
    """(epoch, bot_status) after the reply insert - the new epoch the trigger
    produced (or the unchanged one if the bot was already paused)."""
    row = conn.execute(
        "SELECT epoch, bot_status FROM conversations WHERE id = %s", (conversation_id,),
    ).fetchone()
    return None if row is None else (row[0], row[1])


# --- inbox_events (I7 / I8) --------------------------------------------------


# H20/G9: inbox_events payloads carry identifiers/status ONLY - never message
# text nor a phone. This closed whitelist is a mechanical guard: a developer
# adding `body`/`text`/`phone` to a payload fails loudly here, not silently in
# a later UI read.
INBOX_EVENT_ALLOWED_KEYS = frozenset({
    "conversation_id", "message_id", "seq", "direction", "sent_by", "type",
    "status", "provider_message_id", "bot_status", "epoch", "version",
    "handoff_reason", "reason",
})


def write_inbox_event(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, event_type: str,
    payload: dict[str, Any],
) -> int:
    """Allocate a tenant seq via app.next_inbox_seq() and write one inbox_events
    row - identifiers/status ONLY, never message text or a phone (H20/G9)."""
    disallowed = set(payload) - INBOX_EVENT_ALLOWED_KEYS
    if disallowed:
        raise ValueError(
            f"inbox_events payload has non-whitelisted keys: {sorted(disallowed)}"
        )
    seq = conn.execute("SELECT app.next_inbox_seq()").fetchone()[0]
    conn.execute(
        "INSERT INTO inbox_events (tenant_id, seq, type, payload) VALUES (%s, %s, %s, %s)",
        (tenant_id, seq, event_type, Jsonb(payload)),
    )
    return seq


def list_inbox_events(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, since_seq: int, limit: int,
) -> tuple[list[dict[str, Any]], int]:
    rows = conn.execute(
        "SELECT seq, type, payload FROM inbox_events "
        "WHERE tenant_id = %s AND seq > %s ORDER BY seq ASC LIMIT %s",
        (tenant_id, since_seq, limit),
    ).fetchall()
    latest = conn.execute(
        "SELECT inbox_seq FROM tenant_counters WHERE tenant_id = %s", (tenant_id,),
    ).fetchone()
    latest_seq = int(latest[0]) if latest is not None else since_seq
    cols = ("seq", "type", "payload")
    return [dict(zip(cols, r, strict=True)) for r in rows], latest_seq


def fetch_latest_inbox_seq(conn: psycopg.Connection, *, tenant_id: uuid.UUID) -> int:
    """The tenant's current monotonic inbox_seq (tenant_counters), independent of
    whether any rows exist. This is the `latest_seq` the WS hello frame reports so
    a client knows its position even when nothing was missed."""
    row = conn.execute(
        "SELECT inbox_seq FROM tenant_counters WHERE tenant_id = %s", (tenant_id,),
    ).fetchone()
    return int(row[0]) if row is not None else 0


def fetch_oldest_inbox_seq(conn: psycopg.Connection, *, tenant_id: uuid.UUID) -> int | None:
    """The smallest seq still retained in inbox_events for this tenant, or None if
    the log is empty. The WS handler compares this to the client's last_seq: when
    last_seq is older than oldest-1, some events were already deleted by retention
    and the client must re-sync via REST (resync_required), never pretend the
    deleted rows were delivered."""
    row = conn.execute(
        "SELECT min(seq) FROM inbox_events WHERE tenant_id = %s", (tenant_id,),
    ).fetchone()
    return None if row is None or row[0] is None else int(row[0])


def delete_old_inbox_events(
    conn: psycopg.Connection, *, cutoff: datetime.datetime, batch: int, max_batches: int,
) -> int:
    """Run one bounded retention sweep via the SECURITY DEFINER function
    app.delete_old_inbox_events (0006_p1_ws.sql). Runs on a system_tx connection
    (sharwa_system is the only grantee) because deletion is cross-tenant. Never
    touches tenant_counters.inbox_seq - the seq stays monotonic forever."""
    row = conn.execute(
        "SELECT app.delete_old_inbox_events(%s, %s, %s)", (cutoff, batch, max_batches),
    ).fetchone()
    return int(row[0]) if row is not None else 0


# --- state transitions (I3) - app.set_bot_status is the ONLY writer ----------


def read_conversation_version(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID,
) -> int | None:
    row = conn.execute(
        "SELECT version FROM conversations WHERE id = %s AND tenant_id = %s",
        (conversation_id, tenant_id),
    ).fetchone()
    return None if row is None else int(row[0])


# --- assignment (I4, optimistic lock on version) -----------------------------


def assign_conversation(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID,
    staff_id: uuid.UUID | None, expected_version: int,
) -> bool:
    """Optimistic-lock assignment. staff_id=None clears the assignment. Returns
    False when the version was stale (the route raises CONFLICT_VERSION)."""
    cur = conn.execute(
        "UPDATE conversations SET assigned_staff_id = %s "
        "WHERE id = %s AND tenant_id = %s AND version = %s",
        (staff_id, conversation_id, tenant_id, expected_version),
    )
    return cur.rowcount == 1


# --- internal notes (I5) - read/write ONLY, never by a send path (S5) --------




# --- staff provisioning (I9, operator CLI only - never from a JWT) -----------


def create_staff(
    dsn: str, *, platform_ref: str, platform_user_id: str, display_name: str, role: str,
) -> uuid.UUID:
    """Operator CLI provisioning (like repos.create_tenant): resolves the tenant
    by platform_ref and inserts a staff_members row with its own short-lived
    connection. No HTTP endpoint - staff creation is an admin operation, never
    triggered by a merchant's own JWT (PROMPT §5.1: never auto-create the row)."""
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "INSERT INTO staff_members (tenant_id, platform_user_id, display_name, role) "
            "SELECT id, %s, %s, %s FROM tenants WHERE platform_ref = %s RETURNING id",
            (platform_user_id, display_name, role, platform_ref),
        ).fetchone()
        conn.commit()
    if row is None:
        raise RuntimeError(
            f"no tenant with platform_ref {platform_ref!r} - create it first (create-tenant)"
        )
    return row[0]

def list_notes(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID,
    limit: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, staff_id, body, created_at FROM internal_notes "
        "WHERE conversation_id = %s AND tenant_id = %s ORDER BY created_at DESC LIMIT %s",
        (conversation_id, tenant_id, limit),
    ).fetchall()
    cols = ("id", "staff_id", "body", "created_at")
    return [dict(zip(cols, r, strict=True)) for r in rows]


def insert_note(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID,
    staff_id: uuid.UUID, body: str,
) -> uuid.UUID:
    row = conn.execute(
        "INSERT INTO internal_notes (tenant_id, conversation_id, staff_id, body) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (tenant_id, conversation_id, staff_id, body),
    ).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING id produced no row")
    return row[0]

