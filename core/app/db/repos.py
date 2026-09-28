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


def platform_ref_for_tenant(conn: psycopg.Connection, tenant_id: uuid.UUID) -> str | None:
    """The merchant's platform_ref for one tenant (P1.7 order tracking needs it
    for the platform call). RLS scopes the row to the current tenant."""
    row = conn.execute(
        "SELECT platform_ref FROM tenants WHERE id = %s", (tenant_id,),
    ).fetchone()
    return None if row is None else row[0]


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


# --- routes_channels.py (P0.7 channel lifecycle, restored P1.3b) --------------


class ChannelAlreadyExistsError(Exception):
    """Raised when a tenant already has an ACTIVE whatsapp_baileys channel - the
    partial unique index channel_accounts_one_active_baileys_uq (0002) is the
    final authority; this translates that DB constraint into a typed error."""


def fetch_tenant_name(conn: psycopg.Connection, tenant_id: uuid.UUID) -> str:
    """The authenticated tenant's own display name. RLS already scopes tenants to
    the caller (tenant_self policy); the explicit predicate states the intent."""
    row = conn.execute("SELECT name FROM tenants WHERE id = %s", (tenant_id,)).fetchone()
    if row is None:
        raise RuntimeError("authenticated tenant row missing")
    return row[0]


def list_channel_accounts(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    """Every channel account for this tenant, oldest first, as dicts keyed by
    column name (id, type, engine, status, phone_e164)."""
    cur = conn.execute(
        "SELECT id, type, engine, status, phone_e164 FROM channel_accounts "
        "WHERE tenant_id = %s ORDER BY created_at",
        (tenant_id,),
    )
    cols = [d.name for d in cur.description] if cur.description else []
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def fetch_channel_account(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, channel_id: uuid.UUID,
) -> dict[str, Any] | None:
    """One channel account (id, type, engine, status, phone_e164) or None - the
    route turns None into NOT_FOUND (another tenant's resource is never FORBIDDEN)."""
    cur = conn.execute(
        "SELECT id, type, engine, status, phone_e164 FROM channel_accounts "
        "WHERE tenant_id = %s AND id = %s",
        (tenant_id, channel_id),
    )
    cols = [d.name for d in cur.description] if cur.description else []
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip(cols, row, strict=True))


def fetch_channel_session_id(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, channel_id: uuid.UUID,
) -> str | None:
    """The opaque session_id for one channel (the bridge to the gateway), or None."""
    row = conn.execute(
        "SELECT session_id FROM channel_accounts WHERE tenant_id = %s AND id = %s",
        (tenant_id, channel_id),
    ).fetchone()
    return None if row is None else row[0]


def insert_whatsapp_channel_account(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, session_id: str, engine: str,
) -> dict[str, Any]:
    """Create a whatsapp_baileys channel (status defaults to 'unknown'). Raises
    ChannelAlreadyExistsError when the tenant already has an active baileys
    channel (the partial unique index enforces it at the DB level).

    The transaction aborts in PostgreSQL after `UniqueViolation`; anyone who
    catches `ChannelAlreadyExistsError` and wants to continue work in the SAME
    transaction needs a `SAVEPOINT`. The current path raises `ApiError`
    immediately so it rolls back, which is correct (P1.5b N1)."""
    try:
        cur = conn.execute(
            "INSERT INTO channel_accounts (tenant_id, type, session_id, engine) "
            "VALUES (%s, 'whatsapp_baileys', %s, %s) "
            "RETURNING id, type, engine, status, phone_e164",
            (tenant_id, session_id, engine),
        )
    except psycopg.errors.UniqueViolation as exc:
        raise ChannelAlreadyExistsError(
            "tenant already has an active whatsapp_baileys channel"
        ) from exc
    cols = [d.name for d in cur.description] if cur.description else []
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING id produced no row")
    return dict(zip(cols, row, strict=True))


def update_channel_account_status(
    conn: psycopg.Connection, *, channel_id: uuid.UUID, tenant_id: uuid.UUID,
    status: str, phone_e164: str | None,
) -> None:
    """Record the gateway's mapped status + connected phone for one channel."""
    conn.execute(
        "UPDATE channel_accounts SET status = %s, phone_e164 = %s "
        "WHERE id = %s AND tenant_id = %s",
        (status, phone_e164, channel_id, tenant_id),
    )


def fetch_idempotency_record(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, idempotency_key: str,
) -> dict[str, Any] | None:
    """(request_hash, response_status, response_body) for a key, or None when new."""
    row = conn.execute(
        "SELECT request_hash, response_status, response_body FROM api_idempotency "
        "WHERE tenant_id = %s AND idempotency_key = %s",
        (tenant_id, idempotency_key),
    ).fetchone()
    if row is None:
        return None
    return {"request_hash": row[0], "response_status": row[1], "response_body": row[2]}


def store_idempotency_record(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, idempotency_key: str,
    request_hash: str, response_status: int, response_body: dict[str, Any],
) -> None:
    conn.execute(
        "INSERT INTO api_idempotency "
        "(tenant_id, idempotency_key, request_hash, response_status, response_body) "
        "VALUES (%s, %s, %s, %s, %s)",
        (tenant_id, idempotency_key, request_hash, response_status, Jsonb(response_body)),
    )
