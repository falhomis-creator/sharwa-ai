"""core/app/db/repos.py

All raw SQL used by PRODUCTION code (routes, deps, killswitch sync, the
operator CLI) lives here - and ONLY here (plus core/app/db/migrate.py and
core/app/db/testsupport.py, its test-only sibling). This is what hunt_gate.mjs
rule 8 ("SELECT/INSERT/UPDATE/DELETE في core/ خارج core/app/db/ أو
core/app/repos/") and rule 6 (psycopg import outside core/app/db/) actually
enforce: every caller elsewhere in core/ passes an already-open connection (or,
for the CLI, a DSN) into a named function here and never sees a SQL string or
a psycopg import itself.

Every function here takes an already-open psycopg.Connection (from
tenant_tx()/system_tx()), except create_tenant() which opens its own
short-lived connection - the operator CLI has no long-lived pool.
"""
from __future__ import annotations

import uuid
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

# --- deps.py (authenticate) --------------------------------------------------


def resolve_tenant(conn: psycopg.Connection, platform_ref: str) -> tuple[uuid.UUID, str] | None:
    """Returns (tenant_id, status) for a platform_ref via the SECURITY DEFINER
    app.resolve_tenant() function, or None if no such tenant exists."""
    row = conn.execute(
        "SELECT tenant_id, status FROM app.resolve_tenant(%s)", (platform_ref,)
    ).fetchone()
    if row is None:
        return None
    return row[0], row[1]


# --- routes_health.py (readyz) -----------------------------------------------


def check_alive(conn: psycopg.Connection) -> None:
    """Raises if Postgres cannot answer a trivial query; returns normally
    otherwise. The caller (readyz) only cares whether this raises."""
    conn.execute("SELECT 1")


# --- routes_killswitches.py --------------------------------------------------


def _one_row_dict(cur: psycopg.Cursor[Any]) -> dict[str, object]:
    """A SECURITY DEFINER function called via `SELECT * FROM app.fn(...)`
    always returns exactly one row (it either raises inside Postgres or
    returns the row it wrote) - this gives that guarantee to callers too,
    instead of a bare fetchone() whose `tuple | None` type they would each
    have to narrow themselves."""
    cols = [d.name for d in cur.description] if cur.description else []
    row = cur.fetchone()
    if row is None:
        raise LookupError("expected exactly one row, got none")
    return dict(zip(cols, row, strict=True))


def fetch_kill_switch_state(
    conn: psycopg.Connection, *, scope: str, capability: str, scope_id: str | None,
) -> str | None:
    """The current `state` for one (scope, scope_id, capability) row, or None
    if it doesn't exist yet - used to record the "before" value in audit_log."""
    row = conn.execute(
        "SELECT state FROM kill_switches WHERE scope=%s AND capability=%s "
        "AND scope_id IS NOT DISTINCT FROM %s",
        (scope, capability, scope_id),
    ).fetchone()
    return None if row is None else row[0]


def call_set_kill_switch(
    conn: psycopg.Connection, *, role: str, scope: str, scope_id: str | None,
    capability: str, state: str, reason: str | None, sub: str,
) -> dict[str, object]:
    """Wraps `SELECT * FROM app.set_kill_switch(...)` - the only path
    sharwa_app may use to touch kill_switches (schema.sql GRANTs)."""
    return _one_row_dict(conn.execute(
        "SELECT * FROM app.set_kill_switch(%s, %s, %s, %s, %s, %s, %s)",
        (role, scope, scope_id, capability, state, reason, sub),
    ))


def insert_audit_log(
    conn: psycopg.Connection, *, tenant_id: str | None, actor_sub: str, actor_role: str,
    action: str, target: str, before: str | None, after: str, request_id: str,
) -> None:
    """tenant_id=None means a platform-wide action (0002_p0_api.sql's nullable
    column + system_platform_wide_insert RLS policy)."""
    conn.execute(
        "INSERT INTO audit_log "
        "(tenant_id, actor_sub, actor_role, action, target, before, after, request_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (tenant_id, actor_sub, actor_role, action, target, before, after, request_id),
    )


def call_list_kill_switches(conn: psycopg.Connection) -> list[dict[str, object]]:
    """Wraps `SELECT * FROM app.list_kill_switches(NULL)` - every switch
    visible to the caller's tenant context (global + its own tenant/channel
    rows), as plain dicts keyed by column name."""
    cur = conn.execute("SELECT * FROM app.list_kill_switches(NULL)")
    rows = cur.fetchall()
    cols = [d.name for d in cur.description] if cur.description else []
    return [dict(zip(cols, row, strict=True)) for row in rows]


# --- killswitch/redis_sync.py (rebuild_from_postgres_once) -------------------


def fetch_all_kill_switches_raw(
    conn: psycopg.Connection,
) -> list[tuple[str, str | None, str, str]]:
    """Every (scope, scope_id, capability, state) row - sharwa_system has
    SELECT on kill_switches per schema.sql. Used once at startup and every 60s
    to rebuild the redis-cache copy from Postgres (the source of truth)."""
    return conn.execute(
        "SELECT scope, scope_id, capability, state FROM kill_switches"
    ).fetchall()


# --- routes_channels.py (P0.7 channel-lifecycle routes) ----------------------
#
# channel_accounts is the DB side of the P0.7 channel-lifecycle surface (spec:
# "GET /v1/me", "GET /v1/channels", "POST /v1/channels/whatsapp", "GET
# /v1/channels/{id}", "GET /v1/channels/{id}/qr", "POST /v1/channels/{id}/
# reconnect"). session_id is deliberately never returned to any caller here -
# it is an internal, opaque correlation key between this row and the gateway's
# own session record (spec: "session_id عشوائي غير قابل للتخمين"), not
# something the console/merchant ever needs to see.


def fetch_tenant_name(conn: psycopg.Connection, tenant_id: uuid.UUID) -> str:
    """GET /v1/me's "اسم المتجر" - runs under tenant_tx(), so the `tenant_self`
    RLS policy on tenants (0001_baseline.sql) already restricts this to the
    caller's own row; the WHERE clause is defense-in-depth, not the boundary."""
    row = conn.execute(
        "SELECT name FROM tenants WHERE id = %s", (str(tenant_id),)
    ).fetchone()
    if row is None:
        # Unreachable in practice: authenticate() already proved this tenant_id
        # exists (via app.resolve_tenant) earlier in the very same request.
        raise LookupError(f"tenant {tenant_id} not found")
    name: str = row[0]
    return name


class ChannelAlreadyExistsError(Exception):
    """Raised by insert_whatsapp_channel_account() when this tenant already
    has a non-terminal whatsapp_baileys channel (the
    channel_accounts_one_active_baileys_uq partial unique index,
    0002_p0_api.sql) - the ONLY psycopg exception this module ever translates
    for a caller, so that routes_channels.py never needs to import psycopg
    itself (hunt_gate.mjs rule 6 / .importlinter: psycopg stays inside
    core/app/db/ only). Any OTHER UniqueViolation here (an astronomically
    unlikely session_id collision) is deliberately NOT caught - it propagates
    to FastAPI's own unhandled_exception_handler as INTERNAL (H3: never
    silently retried or hidden)."""


_CHANNEL_ACCOUNT_COLUMNS = "id, type, phone_e164, engine, status, created_at"


def _channel_account_row_to_dict(row: tuple[Any, ...]) -> dict[str, object]:
    return {
        "id": row[0], "type": row[1], "phone_e164": row[2],
        "engine": row[3], "status": row[4], "created_at": row[5],
    }


def insert_whatsapp_channel_account(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, session_id: str, engine: str,
) -> dict[str, object]:
    """INSERT ... RETURNING for a new whatsapp_baileys channel. Tenant-scoped
    RLS's WITH CHECK (tenant_isolation, 0001_baseline.sql) forces tenant_id to
    match app.current_tenant() regardless of what is passed here - tenant_id is
    still passed explicitly (never implicit) per H2. Raises
    ChannelAlreadyExistsError when this tenant already has a non-terminal
    whatsapp_baileys channel (channel_accounts_one_active_baileys_uq,
    0002_p0_api.sql). A UniqueViolation on any OTHER constraint (the generated
    session_id colliding - session_id UNIQUE, 0001_baseline.sql - is
    astronomically unlikely) is deliberately left to propagate unchanged, per
    ChannelAlreadyExistsError's own docstring above."""
    try:
        row = conn.execute(
            f"INSERT INTO channel_accounts (tenant_id, type, session_id, engine) "  # noqa: S608
            f"VALUES (%s, 'whatsapp_baileys', %s, %s) RETURNING {_CHANNEL_ACCOUNT_COLUMNS}",
            (str(tenant_id), session_id, engine),
        ).fetchone()
    except psycopg.errors.UniqueViolation as exc:
        if exc.diag.constraint_name == "channel_accounts_one_active_baileys_uq":
            raise ChannelAlreadyExistsError from exc
        raise
    if row is None:
        raise RuntimeError("INSERT ... RETURNING produced no row")
    return _channel_account_row_to_dict(row)


def list_channel_accounts(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[dict[str, object]]:
    """GET /v1/channels. The explicit WHERE is defense-in-depth on top of RLS's
    tenant_isolation policy (0001_baseline.sql's generic per-table loop already
    covers channel_accounts, which has a tenant_id column)."""
    cur = conn.execute(
        f"SELECT {_CHANNEL_ACCOUNT_COLUMNS} FROM channel_accounts "  # noqa: S608 - _CHANNEL_ACCOUNT_COLUMNS
        "WHERE tenant_id = %s ORDER BY created_at",                  # is a fixed module constant, not input
        (str(tenant_id),),
    )
    return [_channel_account_row_to_dict(row) for row in cur.fetchall()]


def fetch_channel_account(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, channel_id: uuid.UUID,
) -> dict[str, object] | None:
    """GET /v1/channels/{id} and friends. Returns None both when the id truly
    does not exist AND when it belongs to another tenant (RLS already hides
    the second case; the explicit tenant_id in the WHERE is defense-in-depth) -
    the caller turns None into NOT_FOUND either way (spec: "قناة/مورد متجر آخر
    ⇒ NOT_FOUND (لا FORBIDDEN)" - existence of another tenant's channel is
    never disclosed by a different status code)."""
    row = conn.execute(
        f"SELECT {_CHANNEL_ACCOUNT_COLUMNS} FROM channel_accounts "  # noqa: S608 - fixed module constant
        "WHERE id = %s AND tenant_id = %s",
        (str(channel_id), str(tenant_id)),
    ).fetchone()
    return None if row is None else _channel_account_row_to_dict(row)


def fetch_channel_session_id(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, channel_id: uuid.UUID,
) -> str | None:
    """The one place session_id is read back out of the DB at all - to make
    the gateway call (GatewayClient.session_health/session_qr/
    session_reconnect), never to hand it to an HTTP response body."""
    row = conn.execute(
        "SELECT session_id FROM channel_accounts WHERE id = %s AND tenant_id = %s",
        (str(channel_id), str(tenant_id)),
    ).fetchone()
    return None if row is None else str(row[0])


def update_channel_account_status(
    conn: psycopg.Connection, *, channel_id: uuid.UUID, tenant_id: uuid.UUID,
    status: str, phone_e164: str | None,
) -> None:
    """The only columns 0002_p0_api.sql grants sharwa_app UPDATE on
    (GRANT UPDATE (status, phone_e164) ON channel_accounts) - never
    tenant_id/type/session_id/engine, which stay identity-immutable once set."""
    conn.execute(
        "UPDATE channel_accounts SET status = %s, phone_e164 = %s "
        "WHERE id = %s AND tenant_id = %s",
        (status, phone_e164, str(channel_id), str(tenant_id)),
    )


# --- api_idempotency (POST /v1/channels/whatsapp's Idempotency-Key) ---------


def fetch_idempotency_record(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, idempotency_key: str,
) -> dict[str, object] | None:
    row = conn.execute(
        "SELECT request_hash, response_status, response_body FROM api_idempotency "
        "WHERE tenant_id = %s AND idempotency_key = %s",
        (str(tenant_id), idempotency_key),
    ).fetchone()
    if row is None:
        return None
    return {"request_hash": row[0], "response_status": row[1], "response_body": row[2]}


def store_idempotency_record(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, idempotency_key: str,
    request_hash: str, response_status: int, response_body: dict[str, object],
) -> None:
    conn.execute(
        "INSERT INTO api_idempotency "
        "(tenant_id, idempotency_key, request_hash, response_status, response_body) "
        "VALUES (%s, %s, %s, %s, %s)",
        (str(tenant_id), idempotency_key, request_hash, response_status, Jsonb(response_body)),
    )


# --- cli.py (create-tenant) --------------------------------------------------


def create_tenant(
    dsn: str, *, platform_ref: str, name: str, currency: str, timezone: str,
) -> uuid.UUID:
    """Opens its own short-lived connection (the CLI has no long-lived pool -
    it is a one-shot operator command, not a running service)."""
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "INSERT INTO tenants (platform_ref, name, default_currency, timezone) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (platform_ref, name, currency, timezone),
        ).fetchone()
        conn.commit()
    if row is None:
        # Unreachable in practice - a successful INSERT ... RETURNING always
        # yields exactly one row - but this narrows fetchone()'s `tuple | None`
        # for mypy --strict rather than asserting it away.
        raise RuntimeError("INSERT ... RETURNING id produced no row")
    tenant_id: uuid.UUID = row[0]
    return tenant_id
