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


def fetch_tenant_row(dsn: str, tenant_id: uuid.UUID) -> tuple[str, str, str, str] | None:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT platform_ref, name, default_currency, status FROM tenants WHERE id = %s",
            (tenant_id,),
        ).fetchone()
    return None if row is None else (row[0], row[1], row[2], row[3])


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
            # stock_holds references waitlist_entries, so it MUST precede it
            # (F-P2-06: a tenant with both rows would otherwise hit the FK).
            "DELETE FROM stock_holds WHERE tenant_id = %s",
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
    """Reset the TENANT-scoped geo rows + address_resolutions. SHARED reference
    rows (tenant_id IS NULL) are left alone - they are the feature's data (H75)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM address_resolutions")
        conn.execute("DELETE FROM geo_gazetteer WHERE tenant_id IS NOT NULL")


def delete_gazetteer_row(dsn: str, gazetteer_id: int) -> None:
    """Delete one geo_gazetteer row by id (cleans a test-seeded SHARED row)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM geo_gazetteer WHERE id = %s", (gazetteer_id,))


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


# --- stock / waitlist (P2.3 §6.1 db tests) -----------------------------------


def clean_stock(dsn: str, *, tenant_id: uuid.UUID) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM stock_holds WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM waitlist_entries WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM stock_levels WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM customers WHERE tenant_id = %s", (tenant_id,))


def insert_customer(dsn: str, *, tenant_id: uuid.UUID, wa_id: str) -> uuid.UUID:
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO customers (tenant_id, wa_id) VALUES (%s, %s) RETURNING id",
            (tenant_id, wa_id),
        ).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING id produced no row")
    return row[0]


def insert_test_waitlist_entry(
    dsn: str, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
    platform_variant_id: str, offset_seconds: int = 0,
) -> uuid.UUID:
    """A waitlist row (no conversation) - the race-test fixture shape."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO waitlist_entries (tenant_id, customer_id, platform_variant_id, created_at) "
            "VALUES (%s, %s, %s, now() - make_interval(secs => %s)) RETURNING id",
            (tenant_id, customer_id, platform_variant_id, offset_seconds),
        ).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING id produced no row")
    return row[0]


def stock_held_count_and_qty(dsn: str, *, tenant_id: uuid.UUID, variant: str) -> tuple[int, int]:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT count(*), COALESCE(sum(qty), 0) FROM stock_holds "
            "WHERE tenant_id = %s AND platform_variant_id = %s AND status = 'held'",
            (tenant_id, variant),
        ).fetchone()
    assert row is not None
    return int(row[0]), int(row[1])


def waitlist_status_counts(dsn: str, *, tenant_id: uuid.UUID, variant: str) -> dict[str, int]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT status, count(*) FROM waitlist_entries "
            "WHERE tenant_id = %s AND platform_variant_id = %s GROUP BY status",
            (tenant_id, variant),
        ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def held_wa_ids(dsn: str, *, tenant_id: uuid.UUID, variant: str) -> list[str]:
    """The wa_id of every held entry, in FIFO order - proves the hold goes to the
    OLDEST waiter (H72)."""
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT c.wa_id FROM stock_holds h "
            "JOIN waitlist_entries w ON w.id = h.waitlist_entry_id "
            "JOIN customers c ON c.id = w.customer_id "
            "WHERE h.tenant_id = %s AND h.platform_variant_id = %s AND h.status = 'held' "
            "ORDER BY w.created_at, w.id",
            (tenant_id, variant),
        ).fetchall()
    return [r[0] for r in rows]


def force_expire_held_holds(dsn: str, *, tenant_id: uuid.UUID, variant: str) -> None:
    """Move every held hold for a variant into the past so app.expire_stock_holds
    sees them as overdue (the expiry-cascade test)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE stock_holds SET expires_at = now() - interval '1 minute' "
            "WHERE tenant_id = %s AND platform_variant_id = %s AND status = 'held'",
            (tenant_id, variant),
        )


def allocate_holds_race(
    app_dsn: str, *, tenant_id: uuid.UUID, variant: str,
    available: int, ttl_s: int, n: int,
) -> list[int]:
    """n concurrent callers on a threading.Barrier, each on its OWN app-role
    connection, calling app.allocate_stock_holds at the same instant. Returns
    the per-caller number of created holds (PROMPT P2.3 §6.1)."""
    import threading

    barrier = threading.Barrier(n)
    results = [0] * n

    def worker(i: int) -> None:
        with psycopg.connect(app_dsn, autocommit=True) as conn:
            conn.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
            barrier.wait()
            rows = conn.execute(
                "SELECT * FROM app.allocate_stock_holds(%s, %s, make_interval(secs => %s))",
                (variant, available, ttl_s),
            ).fetchall()
            results[i] = len(rows)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def seed_tenant_with_rows(dsn: str, *, tenant_id: uuid.UUID) -> None:
    """Seed one tenant with rows in every table the §1.1 cleanup test covers:
    customers, waitlist_entries, stock_holds, address_resolutions,
    order_lookup_attempts, verifier_blocks and a tenant-scoped geo_gazetteer row.
    delete_tenant_full must then remove every one of them without raising."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        cust = conn.execute(
            "INSERT INTO customers (tenant_id, wa_id) VALUES (%s, 'cleanup-wa') RETURNING id",
            (tenant_id,),
        ).fetchone()
        assert cust is not None
        wl = conn.execute(
            "INSERT INTO waitlist_entries (tenant_id, customer_id, platform_variant_id) "
            "VALUES (%s, %s, 'VAR-CLEANUP') RETURNING id",
            (tenant_id, cust[0]),
        ).fetchone()
        assert wl is not None
        conn.execute(
            "INSERT INTO stock_holds (tenant_id, waitlist_entry_id, platform_variant_id, qty, status, expires_at) "
            "VALUES (%s, %s, 'VAR-CLEANUP', 1, 'held', now() + interval '1 hour')",
            (tenant_id, wl[0]),
        )
        conn.execute(
            "INSERT INTO address_resolutions (tenant_id, input, candidates, decision, confidence, source) "
            "VALUES (%s, '{}', '[]', 'ask_for_pin', 0.0, 'geocoder')",
            (tenant_id,),
        )
        conn.execute(
            "INSERT INTO order_lookup_attempts (tenant_id, customer_id, path, order_ref_hash, outcome) "
            "VALUES (%s, %s, 'other_number', 'hash-cleanup', 'denied')",
            (tenant_id, cust[0]),
        )
        conn.execute(
            "INSERT INTO verifier_blocks (tenant_id, reason) VALUES (%s, 'cleanup-test')",
            (tenant_id,),
        )
        conn.execute(
            "INSERT INTO geo_gazetteer (tenant_id, level, name_ar, name_norm, geom) "
            "VALUES (%s, 'landmark', 'مطعم', 'مطعم', ST_GeomFromText('POINT(44.2 15.3)', 4326))",
            (tenant_id,),
        )


def count_rows_for_tenant(dsn: str, *, table: str, tenant_id: uuid.UUID) -> int:
    """Count `tenant_id = %s` rows on one of the six cleanup-test tables (a
    CLOSED allowlist - the same whitelist pattern as INBOX_EVENT_ALLOWED_KEYS)."""
    allowed = frozenset({
        "waitlist_entries", "stock_holds", "address_resolutions",
        "order_lookup_attempts", "verifier_blocks", "geo_gazetteer",
    })
    if table not in allowed:
        raise ValueError(f"table {table!r} not in the cleanup-test allowlist")
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            f"SELECT count(*) FROM {table} WHERE tenant_id = %s", (tenant_id,)
        ).fetchone()
    assert row is not None
    return int(row[0])


__all__: Sequence[str] = (
    "allocate_holds_race",
    "channel_account_exists_by_session",
    "clean_geo",
    "clean_kill_switches_and_audit",
    "clean_stock",
    "count_advisory_lock_holders",
    "count_channel_accounts_on_conn",
    "count_gazetteer_rows_on_conn",
    "count_tenants",
    "force_expire_held_holds",
    "held_wa_ids",
    "insert_customer",
    "insert_test_waitlist_entry",
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
    "count_rows_for_tenant",
    "delete_gazetteer_row",
    "seed_gazetteer_governorate",
    "seed_kill_switch",
    "seed_tenant_gazetteer",
    "seed_tenant_with_rows",
    "seed_two_tenants",
    "stock_held_count_and_qty",
    "waitlist_status_counts",
)
