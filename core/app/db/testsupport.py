"""core/app/db/testsupport.py

Raw SQL/psycopg used ONLY by the test suite (seeding, cleanup, and
independent-of-the-app-code verification against real Postgres) lives here -
never in a test file itself. hunt_gate.mjs rules 6/8 (psycopg import / raw SQL
outside core/app/db/ or core/app/repos/) apply to test files exactly as they
do to production code (PROMPT_P0_DEEPSEEK_PROMPT.md §3.1 rules 6/8 carry no
"outside test files" qualifier, unlike rules 1/4/5) - so every test fixture
that needs a direct, ops-role connection to seed or independently verify state
calls a named function here instead of importing psycopg or writing a raw SQL
string itself.

Every function here takes a DSN and opens its own short-lived connection
(these are called from test fixtures/assertions, not from request handlers
with an open tenant_tx()/system_tx() connection already in hand) - except the
two *_on_conn() helpers, which take an already-open connection because the
test itself needs to control which connection/transaction they run on (e.g.
"no tenant context set at all", or an already-open tenant_tx()).
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import psycopg

if TYPE_CHECKING:
    from collections.abc import Sequence

# --- generic ------------------------------------------------------------


def exec_sql_autocommit(dsn: str, sql: str) -> None:
    """Runs an arbitrary SQL script (e.g. a migration file's contents, read
    from disk - never a literal string in a test file) with autocommit on.
    Used to simulate "the schema was applied by hand before the migration
    runner existed"."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql)


# --- kill_switches / audit_log -------------------------------------------


def clean_kill_switches_and_audit(dsn: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute("DELETE FROM kill_switches")
        conn.execute("DELETE FROM audit_log")
        conn.commit()


def seed_kill_switch(
    dsn: str, *, scope: str, scope_id: str | None, capability: str, state: str, set_by: str,
) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO kill_switches (scope, scope_id, capability, state, set_by) "
            "VALUES (%s, %s, %s, %s, %s)",
            (scope, scope_id, capability, state, set_by),
        )


def fetch_audit_log_by_tenant(dsn: str, tenant_id: uuid.UUID) -> tuple[str, str] | None:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT action, request_id FROM audit_log WHERE tenant_id = %s", (tenant_id,)
        ).fetchone()
    return None if row is None else (row[0], row[1])


def fetch_audit_log_by_action(dsn: str, action: str) -> tuple[uuid.UUID | None, str, str] | None:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT tenant_id, action, request_id FROM audit_log WHERE action = %s", (action,)
        ).fetchone()
    return None if row is None else (row[0], row[1], row[2])


# --- tenants / channel_accounts ------------------------------------------


def insert_tenant_returning_id(dsn: str, *, platform_ref: str, name: str, currency: str = "SAR") -> uuid.UUID:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "INSERT INTO tenants (platform_ref, name, default_currency) VALUES (%s, %s, %s) RETURNING id",
            (platform_ref, name, currency),
        ).fetchone()
        conn.commit()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING id produced no row")
    tenant_id: uuid.UUID = row[0]
    return tenant_id


def delete_tenant_and_its_audit(dsn: str, tenant_id: uuid.UUID) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM audit_log WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))


def fetch_tenant_row(dsn: str, tenant_id: uuid.UUID) -> tuple[str, str, str, str] | None:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT platform_ref, name, default_currency, status FROM tenants WHERE id = %s",
            (tenant_id,),
        ).fetchone()
    return None if row is None else (row[0], row[1], row[2], row[3])


def delete_tenant(dsn: str, tenant_id: uuid.UUID) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))


def reset_tenants_and_channels(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM channel_accounts")
        conn.execute("DELETE FROM tenants")


def seed_two_tenants(dsn: str, tenant_a: uuid.UUID, tenant_b: uuid.UUID) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO tenants (id, platform_ref, name, default_currency) VALUES "
            "(%s, 'tenant-a', 'Tenant A', 'YER'), (%s, 'tenant-b', 'Tenant B', 'YER')",
            (str(tenant_a), str(tenant_b)),
        )


def insert_channel_account(
    dsn: str, *, tenant_id: uuid.UUID, type_: str, session_id: str, status: str = "connected",
) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO channel_accounts (tenant_id, type, session_id, status) VALUES (%s, %s, %s, %s)",
            (str(tenant_id), type_, session_id, status),
        )


def count_channel_accounts_on_conn(conn: psycopg.Connection) -> int:
    """Runs on a connection the CALLER already has open (either a bare pool
    connection with no tenant context set, or an existing tenant_tx()), so the
    test controls exactly which transaction/context this query executes
    under - that is the entire point of the two isolation tests that use it."""
    row = conn.execute("SELECT count(*) FROM channel_accounts").fetchone()
    assert row is not None
    count: int = row[0]
    return count


def channel_account_exists_by_session(conn: psycopg.Connection, session_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM channel_accounts WHERE session_id = %s", (session_id,)
    ).fetchone()
    return row is not None


# --- schema_migrations / migration internals -----------------------------


def fetch_schema_migrations(dsn: str) -> list[tuple[str, str]]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT version, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


def fetch_schema_migration_checksum(dsn: str, version: str) -> str | None:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE version = %s", (version,)
        ).fetchone()
    return None if row is None else row[0]


def count_tenants(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT count(*) FROM tenants").fetchone()
    assert row is not None
    count: int = row[0]
    return count


def function_exists(dsn: str, proname: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT proname FROM pg_proc WHERE proname = %s", (proname,)
        ).fetchone()
    return row is not None


def count_advisory_lock_holders(dsn: str, *, classid: int, objid: int) -> int:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "AND classid = %s AND objid = %s",
            (classid, objid),
        ).fetchone()
    assert row is not None
    count: int = row[0]
    return count


__all__: Sequence[str] = (
    "channel_account_exists_by_session",
    "clean_kill_switches_and_audit",
    "count_advisory_lock_holders",
    "count_channel_accounts_on_conn",
    "count_tenants",
    "delete_tenant",
    "delete_tenant_and_its_audit",
    "exec_sql_autocommit",
    "fetch_audit_log_by_action",
    "fetch_audit_log_by_tenant",
    "fetch_schema_migration_checksum",
    "fetch_schema_migrations",
    "fetch_tenant_row",
    "function_exists",
    "insert_channel_account",
    "insert_tenant_returning_id",
    "reset_tenants_and_channels",
    "seed_kill_switch",
    "seed_two_tenants",
)
