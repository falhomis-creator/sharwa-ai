"""core/app/db/repos_consent.py - the SINGLE consent/suppression writer (H95).

Every SQL string that INSERTs into consents or INSERTs/DELETEs suppressions
lives here and ONLY here - the S27-a static gate enforces exactly that
(app/db/testsupport.py is the declared test-seeding exception, like its other
raw test SQL). The only callers of the WRITING functions are
app/workers/realtime.py (the message-ingest capture, H99) and
app/workers/stock.py (the waitlist join, H50/OQ-P3-12) - plus tests (S27-b).
read_history is a reader and also serves the operator CLI.

H94: consents is append-only - this module never UPDATEs or DELETEs a consents
row; revoking a consent is a NEW row (granted=false) and the current state is
the latest row (latest-wins, enforced by the 0016 privileges).
H97: lifting a suppression is a DELETE scoped to ONE scope whose cause is
exactly 'customer_message_optout' - an operator block is never lifted by a
customer word.
H98: `evidence` carries identifiers only (the message/entry UUID) - never
customer text or a phone number; no function here accepts either.
"""
from __future__ import annotations

import uuid
from typing import Any

import psycopg

# H95: the closed source list - must equal the 0016 CHECK constraint verbatim
# (S27-c compares the two). 'checkout_optin' and 'import' are allowed by the
# constraint but BLOCKED from marketing eligibility by
# repos_policy.read_latest_consent until the owner's legal decision (OQ-P3-14).
CONSENT_SOURCES = (
    "customer_message_optin",
    "customer_message_optout",
    "waitlist_join",
    "checkout_optin",
    "import",
    "operator",
)

# The scopes a customer's STOP blocks (moved from repos_ingest - S27-a):
# marketing fails closed, service fails open (H4); order_updates is
# deliberately NEVER suppressed.
OPTOUT_SCOPES = ("marketing", "back_in_stock", "review_request")
OPTOUT_REASON = "customer_message_optout"


def write_consent(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
    scope: str, granted: bool, source: str, evidence: str | None,
) -> None:
    """Append one consents row (latest-wins). The low-level appender the
    record_* functions build on (H94: append-only, no UPDATE ever).
    F-P3-27: created_at is written EXPLICITLY as clock_timestamp() - the
    table's DEFAULT now() is the TRANSACTION START time, so two rows from
    one transaction would tie and the id DESC tiebreaker (a random UUID)
    could flip latest-wins. clock_timestamp() is strictly monotonic within a
    transaction, which makes the order deterministic."""
    conn.execute(
        "INSERT INTO consents (tenant_id, customer_id, scope, granted, source, evidence, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, clock_timestamp())",
        (tenant_id, customer_id, scope, granted, source, evidence),
    )


def insert_suppressions(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
    scopes: tuple[str, ...], reason: str,
) -> int:
    """Write suppression rows (idempotent ON CONFLICT DO NOTHING) - moved here
    from repos_ingest (H95: every suppressions write belongs to the one
    consent writer). order_updates is deliberately never suppressed (service
    fails open, marketing fails closed - H4). Returns NEW rows written.
    F-P3-30 NOTE: the conflict target is the WHOLE row identity
    (tenant, customer, scope), and the reason is NOT updated on conflict - a
    later writer with a different reason is silently swallowed. A future
    operator-block writer MUST use DO UPDATE SET reason here (or its own
    writer), otherwise an earlier STOP block swallows the operator reason and
    a later customer opt-in then lifts a block the operator wanted to keep."""
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


def _latest(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
    scope: str,
) -> tuple[bool, str] | None:
    """The latest (granted, source) row for one scope - None when never asked."""
    row = conn.execute(
        "SELECT granted, source FROM consents "
        "WHERE tenant_id = %s AND customer_id = %s AND scope = %s "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (tenant_id, customer_id, scope),
    ).fetchone()
    return None if row is None else (bool(row[0]), str(row[1]))


def _lift_optout_suppression(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
    scope: str,
) -> bool:
    """H97: lift ONE scope's suppression and ONLY when the app itself wrote it
    for a customer STOP (reason='customer_message_optout'). An operator block
    (or any other cause) is never lifted by a customer word. True when a row
    was actually deleted."""
    cur = conn.execute(
        "DELETE FROM suppressions "
        "WHERE tenant_id = %s AND customer_id = %s AND scope = %s AND reason = %s",
        (tenant_id, customer_id, scope, OPTOUT_REASON),
    )
    return cur.rowcount > 0


def record_optin(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
    message_id: uuid.UUID,
) -> str:
    """A customer's explicit opt-in word (H96): append `marketing granted`
    with source customer_message_optin, evidence = the message UUID (H98),
    then lift the marketing suppression H97 allows (only a
    customer_message_optout one). Closed-vocabulary result: 'noop' (already
    granted by an explicit message opt-in - a checkout/import grant does NOT
    count: the explicit word still upgrades the source), 'granted', or
    'granted_and_lifted'."""
    latest = _latest(conn, tenant_id=tenant_id, customer_id=customer_id, scope="marketing")
    if latest is not None and latest[0] and latest[1] == "customer_message_optin":
        return "noop"
    write_consent(
        conn, tenant_id=tenant_id, customer_id=customer_id, scope="marketing",
        granted=True, source="customer_message_optin", evidence=str(message_id),
    )
    lifted = _lift_optout_suppression(
        conn, tenant_id=tenant_id, customer_id=customer_id, scope="marketing",
    )
    return "granted_and_lifted" if lifted else "granted"


def record_optout(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
    message_id: uuid.UUID,
) -> str:
    """A customer's STOP: append granted=false for the three scopes (source
    customer_message_optout, evidence = the message UUID, H98) AND write the
    suppressions exactly as the ingest path always did (H4: order_updates is
    never touched).
    F-P3-29 (H99: no noise): the STOP is a NOOP when the customer's LATEST
    consent row (any scope, latest-wins order) is already a
    customer_message_optout revocation - i.e. no consent event of any kind
    happened since the previous STOP, so nothing new is said and nothing new
    is written. Anything else (an opt-in, a waitlist join, an operator write)
    in between makes the new STOP speak again and it appends all three
    scopes. Returns 'revoked' when at least one row was written, 'noop' when
    none was; the suppression block is re-asserted (ON CONFLICT DO NOTHING)
    in BOTH cases and the queue cancel is the caller's unchanged behavior."""
    row = conn.execute(
        "SELECT granted, source FROM consents "
        "WHERE tenant_id = %s AND customer_id = %s "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (tenant_id, customer_id),
    ).fetchone()
    already_stopped = row is not None and not bool(row[0]) and str(row[1]) == OPTOUT_REASON
    if not already_stopped:
        for scope in OPTOUT_SCOPES:
            write_consent(
                conn, tenant_id=tenant_id, customer_id=customer_id, scope=scope,
                granted=False, source=OPTOUT_REASON, evidence=str(message_id),
            )
    insert_suppressions(
        conn, tenant_id=tenant_id, customer_id=customer_id,
        scopes=OPTOUT_SCOPES, reason=OPTOUT_REASON,
    )
    return "noop" if already_stopped else "revoked"


def record_waitlist_join(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
    entry_id: uuid.UUID,
) -> str:
    """A waitlist join (OQ-P3-12, owner-approved default): the join is an
    explicit act, so it appends `back_in_stock granted` (source waitlist_join,
    evidence = the entry UUID, H98) and lifts the back_in_stock suppression
    when the app itself wrote it for a STOP (H97) - otherwise the
    stock_joined promise would be a lie. It grants back_in_stock ONLY; a
    marketing suppression is untouched. 'noop' | 'granted' |
    'granted_and_lifted'."""
    latest = _latest(conn, tenant_id=tenant_id, customer_id=customer_id, scope="back_in_stock")
    if latest is not None and latest[0] and latest[1] == "waitlist_join":
        return "noop"
    write_consent(
        conn, tenant_id=tenant_id, customer_id=customer_id, scope="back_in_stock",
        granted=True, source="waitlist_join", evidence=str(entry_id),
    )
    lifted = _lift_optout_suppression(
        conn, tenant_id=tenant_id, customer_id=customer_id, scope="back_in_stock",
    )
    return "granted_and_lifted" if lifted else "granted"


def read_history(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
) -> list[tuple[str, bool, str, Any, str | None]]:
    """One customer's full consent history, chronological (H94: it is the
    audit trail itself). Columns: (scope, granted, source, created_at,
    evidence) - identifiers only, never text or phone (H98/H48)."""
    rows = conn.execute(
        "SELECT scope, granted, source, created_at, evidence FROM consents "
        "WHERE tenant_id = %s AND customer_id = %s "
        "ORDER BY created_at ASC, id ASC",
        (tenant_id, customer_id),
    ).fetchall()
    return [(r[0], bool(r[1]), r[2], r[3], r[4]) for r in rows]


def consent_history(
    dsn: str, *, platform_ref: str, customer_id: uuid.UUID,
) -> list[tuple[str, bool, str, Any, str | None]] | None:
    """Operator-CLI reader (migration role, DSN-based): resolve the tenant by
    platform_ref then read one customer's chronological history. None when
    the tenant is unknown. READ-ONLY - no CLI command ever writes a consent
    (H94: the ledger is appended by the capture paths only). Identifiers
    only (H48/H98): no phone, no message text, ever."""
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT id FROM tenants WHERE platform_ref = %s", (platform_ref,),
        ).fetchone()
        if row is None:
            return None
        return read_history(conn, tenant_id=row[0], customer_id=customer_id)
