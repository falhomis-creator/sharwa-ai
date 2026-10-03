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
from psycopg.types.json import Jsonb

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


def delete_tenant_full(dsn: str, tenant_id: uuid.UUID) -> None:
    """Full tenant cleanup in foreign-key order (children before parents).

    F-P2-02: this name was called by test_routes_channels' real_tenant fixture
    but never defined. The channel-lifecycle/idempotency tests create
    channel_accounts and api_idempotency rows - neither has ON DELETE CASCADE to
    tenants - so those (and every other tenant-scoped row) are deleted before the
    tenant itself. Order respects the intra-tenant FKs (messages before
    conversations/staff_members; conversations before channel_accounts/
    customers; campaign_recipients before campaigns; etc.). Tables that are
    ON DELETE CASCADE children of conversations / catalog_products are NOT named
    here - their cascade handles them, keeping this helper clear of H57 (S12)
    and the embeddings isolation (S9)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        for sql in (
            "DELETE FROM messages WHERE tenant_id = %s",
            "DELETE FROM inbox_events WHERE tenant_id = %s",
            "DELETE FROM campaign_recipients WHERE tenant_id = %s",
            "DELETE FROM consents WHERE tenant_id = %s",
            "DELETE FROM suppressions WHERE tenant_id = %s",
            "DELETE FROM waitlist_entries WHERE tenant_id = %s",
            "DELETE FROM order_lookup_attempts WHERE tenant_id = %s",
            "DELETE FROM address_resolutions WHERE tenant_id = %s",
            "DELETE FROM checkout_sessions WHERE tenant_id = %s",
            "DELETE FROM verifier_blocks WHERE tenant_id = %s",
            "DELETE FROM llm_calls WHERE tenant_id = %s",
            "DELETE FROM outbox WHERE tenant_id = %s",
            "DELETE FROM conversations WHERE tenant_id = %s",
            "DELETE FROM campaigns WHERE tenant_id = %s",
            "DELETE FROM number_health WHERE tenant_id = %s",
            "DELETE FROM inbound_events WHERE tenant_id = %s",
            "DELETE FROM catalog_variants WHERE tenant_id = %s",
            "DELETE FROM catalog_products WHERE tenant_id = %s",
            "DELETE FROM kb_chunks WHERE tenant_id = %s",
            "DELETE FROM size_chart_rows WHERE tenant_id = %s",
            "DELETE FROM size_charts WHERE tenant_id = %s",
            "DELETE FROM catalog_sync_cursor WHERE tenant_id = %s",
            "DELETE FROM stock_holds WHERE tenant_id = %s",
            "DELETE FROM stock_levels WHERE tenant_id = %s",
            "DELETE FROM geo_gazetteer WHERE tenant_id = %s",
            "DELETE FROM scheduled_jobs WHERE tenant_id = %s",
            "DELETE FROM tenant_counters WHERE tenant_id = %s",
            "DELETE FROM tenant_budgets WHERE tenant_id = %s",
            "DELETE FROM staff_members WHERE tenant_id = %s",
            "DELETE FROM customers WHERE tenant_id = %s",
            "DELETE FROM channel_accounts WHERE tenant_id = %s",
            "DELETE FROM audit_log WHERE tenant_id = %s",
            "DELETE FROM api_idempotency WHERE tenant_id = %s",
            "DELETE FROM tenants WHERE id = %s",
        ):
            conn.execute(sql, (tenant_id,))


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
    dsn: str, *, tenant_id: uuid.UUID, type_: str, session_id: str,
    status: str = "connected", engine: str = "ai_core",
) -> uuid.UUID:
    """Inserts a channel_accounts row and returns its id (F-P2-02: the callers
    already passed `engine=` and used the returned id, but this helper neither
    accepted `engine` nor returned anything - signature drift between the test
    support and its callers)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO channel_accounts (tenant_id, type, session_id, status, engine) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (str(tenant_id), type_, session_id, status, engine),
        ).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING id produced no row")
    return row[0]


def fetch_channel_account_row(dsn: str, channel_id: uuid.UUID) -> dict[str, object] | None:
    """Independent-of-app-code verification: the channel_accounts row by id, as
    a dict keyed by column name (F-P2-02: called by test_routes_channels but
    never defined - the name-resolver gap that S16 now closes)."""
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT id, tenant_id, type, session_id, phone_e164, engine, status, created_at "
            "FROM channel_accounts WHERE id = %s", (channel_id,)
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0], "tenant_id": row[1], "type": row[2], "session_id": row[3],
        "phone_e164": row[4], "engine": row[5], "status": row[6], "created_at": row[7],
    }


def insert_idempotency_record(
    dsn: str, *, tenant_id: uuid.UUID, idempotency_key: str, request_hash: str,
    response_status: int, response_body: dict,
) -> None:
    """Seeds an api_idempotency row directly (F-P2-02: called by
    test_routes_channels to prove the conflict branch, but never defined)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO api_idempotency "
            "(tenant_id, idempotency_key, request_hash, response_status, response_body) "
            "VALUES (%s, %s, %s, %s, %s)",
            (tenant_id, idempotency_key, request_hash, response_status, Jsonb(response_body)),
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


# --- geo_gazetteer / address_resolutions (P2.2 §1.2 db tests) ----------------


def clean_geo(dsn: str) -> None:
    """Reset the geo layer so each db test starts from a known state: every
    address_resolutions row and every geo_gazetteer row (shared + tenant-scoped)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM address_resolutions")
        conn.execute("DELETE FROM geo_gazetteer")


def seed_gazetteer_governorate(dsn: str, *, name_ar: str, name_norm: str, wkt: str) -> int:
    """Insert one SHARED (tenant_id NULL) governorate row with a polygon and
    return its id. The ONLY sanctioned way a test seeds shared reference data
    (mirrors scripts/seed_gazetteer.py's role for production)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO geo_gazetteer (tenant_id, level, name_ar, name_norm, geom) "
            "VALUES (NULL, 'governorate', %s, %s, ST_GeomFromText(%s, 4326)) RETURNING id",
            (name_ar, name_norm, wkt),
        ).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING id produced no row")
    return int(row[0])


def seed_tenant_gazetteer(
    dsn: str, *, tenant_id: uuid.UUID, level: str, name_ar: str, name_norm: str, wkt: str,
) -> int:
    """Insert one tenant-scoped gazetteer row (a tenant-learned landmark)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO geo_gazetteer (tenant_id, level, name_ar, name_norm, geom) "
            "VALUES (%s, %s, %s, %s, ST_GeomFromText(%s, 4326)) RETURNING id",
            (tenant_id, level, name_ar, name_norm, wkt),
        ).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING id produced no row")
    return int(row[0])


def count_gazetteer_rows_on_conn(conn: psycopg.Connection) -> int:
    """Number of geo_gazetteer rows VISIBLE on this connection, under the RLS
    policy it currently has set (shared + the current tenant's own rows)."""
    row = conn.execute("SELECT count(*) FROM geo_gazetteer").fetchone()
    assert row is not None
    return int(row[0])


__all__: Sequence[str] = (
    "channel_account_exists_by_session",
    "clean_geo",
    "clean_kill_switches_and_audit",
    "count_advisory_lock_holders",
    "count_channel_accounts_on_conn",
    "count_gazetteer_rows_on_conn",
    "count_tenants",
    "delete_tenant",
    "delete_tenant_and_its_audit",
    "delete_tenant_full",
    "exec_sql_autocommit",
    "fetch_audit_log_by_action",
    "fetch_channel_account_row",
    "fetch_audit_log_by_tenant",
    "fetch_schema_migration_checksum",
    "fetch_schema_migrations",
    "fetch_tenant_row",
    "function_exists",
    "insert_channel_account",
    "insert_idempotency_record",
    "insert_tenant_returning_id",
    "reset_tenants_and_channels",
    "seed_gazetteer_governorate",
    "seed_kill_switch",
    "seed_tenant_gazetteer",
    "seed_two_tenants",
)
