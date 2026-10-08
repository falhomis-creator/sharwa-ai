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
from datetime import datetime, timedelta
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


def fetch_tenant_timezone(dsn: str, tenant_id: uuid.UUID) -> str:
    with psycopg.connect(dsn) as conn:
        return str(conn.execute("SELECT timezone FROM tenants WHERE id = %s", (tenant_id,)).fetchone()[0])


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
            # P3.4 marketing activation + its append-only log (children of tenants).
            "DELETE FROM marketing_activation_log WHERE tenant_id = %s",
            "DELETE FROM marketing_activation WHERE tenant_id = %s",
            # stock_holds references waitlist_entries, so it MUST precede it
            # (F-P2-06: a tenant with both rows would otherwise hit the FK).
            "DELETE FROM stock_holds WHERE tenant_id = %s",
            "DELETE FROM waitlist_entries WHERE tenant_id = %s",
            "DELETE FROM order_lookup_attempts WHERE tenant_id = %s",
            "DELETE FROM address_resolutions WHERE tenant_id = %s",
            "DELETE FROM checkout_sessions WHERE tenant_id = %s",
            "DELETE FROM verifier_blocks WHERE tenant_id = %s",
            "DELETE FROM llm_calls WHERE tenant_id = %s",
            # proactive_ledger references outbox + customers + channel_accounts,
            # so it MUST precede all three (the F-P2-06 lesson, applied forward).
            "DELETE FROM proactive_ledger WHERE tenant_id = %s",
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
            # carts references customers (P3.2), so it MUST precede customers.
            "DELETE FROM carts WHERE tenant_id = %s",
            "DELETE FROM cart_tombstones WHERE tenant_id = %s",
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
        ch = conn.execute(
            "INSERT INTO channel_accounts (tenant_id, type, session_id, engine) "
            "VALUES (%s, 'whatsapp_baileys', 'cleanup-sess', 'ai_core') RETURNING id",
            (tenant_id,),
        ).fetchone()
        assert ch is not None
        conv = conn.execute(
            "INSERT INTO conversations (tenant_id, channel_account_id, customer_id) "
            "VALUES (%s, %s, %s) RETURNING id",
            (tenant_id, ch[0], cust[0]),
        ).fetchone()
        assert conv is not None
        ob = conn.execute(
            "INSERT INTO outbox (tenant_id, conversation_id, channel_account_id, idempotency_key, "
            "origin, message_class, to_wa_id, payload, status) "
            "VALUES (%s, %s, %s, 'cleanup-ob', 'automation', 'utility', 'cleanup-wa', %s, 'pending') RETURNING id",
            (tenant_id, conv[0], ch[0], Jsonb({})),
        ).fetchone()
        assert ob is not None
        conn.execute(
            "INSERT INTO proactive_ledger (tenant_id, channel_account_id, customer_id, outbox_id, message_class, template_id, status) "
            "VALUES (%s, %s, %s, %s, 'utility', 'stock_available', 'reserved')",
            (tenant_id, ch[0], cust[0], ob[0]),
        )
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
        conn.execute(
            "INSERT INTO carts (tenant_id, customer_id, platform_cart_id, status, last_activity_at, snapshot) "
            "VALUES (%s, %s, 'CART-CLEANUP', 'open', now(), '{}'::jsonb)",
            (tenant_id, cust[0]),
        )


def seed_conversation(
    dsn: str, *, tenant_id: uuid.UUID, channel_id: uuid.UUID, customer_id: uuid.UUID,
    bot_status: str = "active", epoch: int = 0,
) -> uuid.UUID:
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO conversations (tenant_id, channel_account_id, customer_id, bot_status, epoch) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (tenant_id, channel_id, customer_id, bot_status, epoch),
        ).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING id produced no row")
    return row[0]


def seed_inbound_message(
    dsn: str, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID, body: str = "مرحبا",
    age: timedelta | None = None, created_at: datetime | None = None,
) -> uuid.UUID:
    """Insert one inbound messages row (prior-interaction / active-chat signal).
    F-P3-19: `age` defaults to 2 days so the customer is NOT "talking now";
    pass age=timedelta(0) for an active-chat fixture.
    F-P3-35: `created_at`, when passed, is inserted verbatim (deterministic tests
    that inject a fixed clock pass BASE - age); the default stays now() - age."""
    if age is None:
        age = timedelta(days=2)
    if created_at is not None:
        sql = ("INSERT INTO messages (tenant_id, conversation_id, direction, sent_by, type, body, status, created_at) "
               "VALUES (%s, %s, 'in', 'customer', 'text', %s, 'received', %s) RETURNING id")
        params = (tenant_id, conversation_id, body, created_at)
    else:
        sql = ("INSERT INTO messages (tenant_id, conversation_id, direction, sent_by, type, body, status, created_at) "
               "VALUES (%s, %s, 'in', 'customer', 'text', %s, 'received', now() - %s) RETURNING id")
        params = (tenant_id, conversation_id, body, age)
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute(sql, params).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING id produced no row")
    return row[0]


def seed_outbox_row(
    dsn: str, *, tenant_id: uuid.UUID, channel_id: uuid.UUID,
    conversation_id: uuid.UUID | None, origin: str, message_class: str, to_wa_id: str,
    expected_epoch: int | None = None, status: str = "pending", payload: dict | None = None,
    idempotency_key: str | None = None,
) -> uuid.UUID:
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO outbox (tenant_id, conversation_id, channel_account_id, "
            "idempotency_key, origin, message_class, expected_epoch, to_wa_id, payload, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                tenant_id, conversation_id, channel_id,
                idempotency_key or f"dispatch-{uuid.uuid4()}",
                origin, message_class, expected_epoch, to_wa_id, Jsonb(payload or {}), status,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING id produced no row")
    return row[0]


def seed_suppression(
    dsn: str, *, tenant_id: uuid.UUID, customer_id: uuid.UUID, scope: str,
) -> None:
    # F-P3-13: `reason` is NOT NULL on suppressions - the seed must supply it.
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO suppressions (tenant_id, customer_id, scope, reason) "
            "VALUES (%s, %s, %s, 'test')",
            (tenant_id, customer_id, scope),
        )


def seed_number_health(
    dsn: str, *, tenant_id: uuid.UUID, channel_id: uuid.UUID,
    daily_cap: int = 50, sent_today: int = 0, state: str = "healthy",
    utility_daily_cap: int = 100, utility_sent_today: int = 0,
    day: str | None = None,
    next_marketing_at: str | None = "1970-01-01T00:00:00Z",
    next_utility_at: str | None = "1970-01-01T00:00:00Z",
    warmup_started_at: str | None = None,
    state_changed_at: str | None = None,
) -> None:
    """Insert/refresh a number_health row for the send-slot (P3.1) tests.
    `day` defaults to the DB's current_date; inject it to match an injected p_now
    (otherwise the first reservation triggers a day-rollover reset). F-P3-19: the
    spacing windows default to 1970 (not now()) so an injected PAST p_now doesn't
    make every reservation return `spacing`."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO number_health "
            "(channel_account_id, tenant_id, daily_cap, sent_today, state, "
            " utility_daily_cap, utility_sent_today, day, next_marketing_at, next_utility_at, "
            " warmup_started_at, state_changed_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, COALESCE(%s::date, current_date), "
            " %s::timestamptz, %s::timestamptz, %s::timestamptz, COALESCE(%s::timestamptz, now())) "
            "ON CONFLICT (channel_account_id) DO UPDATE SET "
            " daily_cap = EXCLUDED.daily_cap, sent_today = EXCLUDED.sent_today, "
            " state = EXCLUDED.state, utility_daily_cap = EXCLUDED.utility_daily_cap, "
            " utility_sent_today = EXCLUDED.utility_sent_today, day = EXCLUDED.day, "
            " next_marketing_at = EXCLUDED.next_marketing_at, next_utility_at = EXCLUDED.next_utility_at, "
            " warmup_started_at = EXCLUDED.warmup_started_at, state_changed_at = EXCLUDED.state_changed_at",
            (channel_id, tenant_id, daily_cap, sent_today, state, utility_daily_cap, utility_sent_today, day,
             next_marketing_at, next_utility_at, warmup_started_at, state_changed_at),
        )


def insert_consent(
    dsn: str, *, tenant_id: uuid.UUID, customer_id: uuid.UUID, scope: str,
    granted: bool = True, source: str = "customer_message_optin",
) -> None:
    """Insert a consents row (latest-wins read: the seed inserts a fresh row).
    P3.3: the 0016 CHECK constraint closes the source list - the seed default
    is a valid explicit opt-in (which also satisfies the marketing source gate
    in repos_policy.read_latest_consent, H95)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO consents (tenant_id, customer_id, scope, granted, source) "
            "VALUES (%s, %s, %s, %s, %s)",
            (tenant_id, customer_id, scope, granted, source),
        )


def fetch_conversation_bot_status(dsn: str, conversation_id: uuid.UUID) -> str | None:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT bot_status FROM conversations WHERE id = %s", (conversation_id,)
        ).fetchone()
    return None if row is None else row[0]


def fetch_outbox_status(dsn: str, outbox_id: uuid.UUID) -> str | None:
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT status FROM outbox WHERE id = %s", (outbox_id,)).fetchone()
    return None if row is None else row[0]


def fetch_outbox_policy_reason(dsn: str, outbox_id: uuid.UUID) -> str | None:
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT policy_reason FROM outbox WHERE id = %s", (outbox_id,)).fetchone()
    return None if row is None else row[0]


def fast_forward_outbox(dsn: str, *, tenant_id: uuid.UUID) -> None:
    """F-P3-16 burst test: make every pending row due now (no sleep)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE outbox SET next_attempt_at = '1970-01-01'::timestamptz "
            "WHERE tenant_id = %s AND status = 'pending'",
            (tenant_id,),
        )


def fast_forward_number_health(dsn: str, *, channel_id: uuid.UUID) -> None:
    """F-P3-16 burst test: open the spacing windows (no sleep)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE number_health SET next_utility_at = '1970-01-01'::timestamptz, "
            " next_marketing_at = '1970-01-01'::timestamptz WHERE channel_account_id = %s",
            (channel_id,),
        )


# --- P3.2 scheduler + carts helpers (engine/webhook/e2e tests) ---------------


def db_now(dsn: str) -> datetime:
    """The DATABASE's clock (claim_due_jobs compares run_at to now(), so engine
    tests must compute margins against the DB clock, not the test process's)."""
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT now()").fetchone()
    assert row is not None
    return row[0]


def insert_cart(
    dsn: str, *, tenant_id: uuid.UUID, customer_id: uuid.UUID, platform_cart_id: str,
    status: str = "open", last_activity_at: datetime | None = None,
    snapshot: dict | None = None,
) -> uuid.UUID:
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO carts (tenant_id, customer_id, platform_cart_id, status, "
            "last_activity_at, snapshot) VALUES (%s, %s, %s, %s, COALESCE(%s, now()), %s) "
            "RETURNING id",
            (tenant_id, customer_id, platform_cart_id, status, last_activity_at, Jsonb(snapshot or {})),
        ).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING id produced no row")
    return row[0]


def fetch_cart(dsn: str, tenant_id: uuid.UUID, platform_cart_id: str) -> dict | None:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT id, customer_id, status, last_activity_at, snapshot "
            "FROM carts WHERE tenant_id = %s AND platform_cart_id = %s",
            (tenant_id, platform_cart_id),
        ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "customer_id": row[1], "status": row[2],
            "last_activity_at": row[3], "snapshot": row[4]}


def insert_scheduled_job(
    dsn: str, *, tenant_id: uuid.UUID, kind: str, dedupe_key: str,
    run_at: datetime | None = None, payload: dict | None = None,
    max_lateness_s: int = 0, status: str = "pending", attempts: int = 0,
    locked_until: datetime | None = None,
) -> uuid.UUID:
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO scheduled_jobs (tenant_id, kind, dedupe_key, run_at, payload, "
            "max_lateness_s, status, attempts, locked_until) "
            "VALUES (%s, %s, %s, COALESCE(%s, now() - interval '1 hour'), %s, %s, %s, %s, %s) "
            "RETURNING id",
            (tenant_id, kind, dedupe_key, run_at, Jsonb(payload or {}), max_lateness_s,
             status, attempts, locked_until),
        ).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING id produced no row")
    return row[0]


def fetch_scheduled_job(dsn: str, job_id: uuid.UUID) -> dict | None:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT id, tenant_id, kind, dedupe_key, run_at, status, attempts, "
            "max_lateness_s, cancel_reason, last_error, finished_at "
            "FROM scheduled_jobs WHERE id = %s",
            (job_id,),
        ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "tenant_id": row[1], "kind": row[2], "dedupe_key": row[3],
            "run_at": row[4], "status": row[5], "attempts": row[6],
            "max_lateness_s": row[7], "cancel_reason": row[8], "last_error": row[9],
            "finished_at": row[10]}


def fetch_scheduled_job_by_key(dsn: str, tenant_id: uuid.UUID, dedupe_key: str) -> dict | None:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT id, kind, run_at, status, attempts, cancel_reason, last_error "
            "FROM scheduled_jobs WHERE tenant_id = %s AND dedupe_key = %s",
            (tenant_id, dedupe_key),
        ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "kind": row[1], "run_at": row[2], "status": row[3],
            "attempts": row[4], "cancel_reason": row[5], "last_error": row[6]}


def fast_forward_jobs(dsn: str, *, tenant_id: uuid.UUID) -> None:
    """P3.2 engine tests: make every pending job due now (no sleep)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE scheduled_jobs SET run_at = '1970-01-01'::timestamptz, locked_until = NULL "
            "WHERE tenant_id = %s AND status = 'pending'",
            (tenant_id,),
        )


def age_proactive_ledger(dsn: str, *, tenant_id: uuid.UUID, reserved_at: datetime) -> None:
    """F-P3-31 (P3.4 step zero): move one tenant's ledger reservations to an
    ABSOLUTE injected timestamp (deterministic against the gate's injected
    `now` - no sleep, and no now()/clock_timestamp() arithmetic needed here;
    where a relative age IS needed, clock_timestamp() is the rule, never
    now()=transaction-start)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE proactive_ledger SET reserved_at = %s WHERE tenant_id = %s",
            (reserved_at, tenant_id),
        )


def enable_marketing(dsn: str, *, tenant_id: uuid.UUID, cap: int = 5) -> None:
    """P3.4 TEST SEEDING (S29-a exempts testsupport): enable a TEST tenant's
    marketing by raw SQL, so db fixtures can reach the gate's marketing branch.
    This is the ONLY non-CLI enable and it exists in test code alone (H100)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO marketing_activation "
            "(tenant_id, enabled, canary_cap_per_day, enabled_by, enabled_at) "
            "VALUES (%s, true, %s, 'test', clock_timestamp()) "
            "ON CONFLICT (tenant_id) DO UPDATE SET enabled = true, "
            "canary_cap_per_day = EXCLUDED.canary_cap_per_day",
            (tenant_id, cap),
        )


def age_inbound_messages(dsn: str, *, conversation_id: uuid.UUID, age: timedelta | None = None,
                         as_of: datetime | None = None) -> None:
    """Move a conversation's inbound messages `age` (default 2 days) into the
    past (clock_timestamp-based, no sleep). A test that ingests a REAL message
    (e.g. STOP through RealtimeWorker._commit) stamps it at the DB's now(); the
    gate under an INJECTED older `now` would then see 'the customer is talking
    right now' (active_chat). Aging the message restores 'not talking now'.
    F-P3-35b: `as_of`, when passed, ages from it (created_at = as_of - age) so a
    deterministic test injects the same clock it passes to the gate; the default
    stays clock_timestamp() - age."""
    if age is None:
        age = timedelta(days=2)
    if as_of is not None:
        sql = ("UPDATE messages SET created_at = %s - %s "
               "WHERE conversation_id = %s AND direction = 'in'")
        params = (as_of, age, conversation_id)
    else:
        sql = ("UPDATE messages SET created_at = clock_timestamp() - %s "
               "WHERE conversation_id = %s AND direction = 'in'")
        params = (age, conversation_id)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql, params)


def clear_marketing_activation(dsn: str, *, tenant_id: uuid.UUID) -> None:
    """TEST SEEDING (S29-a exempt): back to 'no activation row' = the production
    default state (H100), for the default-off tests whose fixture opted in."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM marketing_activation WHERE tenant_id = %s", (tenant_id,))


def fetch_marketing_activation(dsn: str, tenant_id: uuid.UUID) -> dict | None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT enabled, canary_cap_per_day FROM marketing_activation WHERE tenant_id = %s",
            (tenant_id,),
        ).fetchone()
    return None if row is None else {"enabled": bool(row[0]), "canary_cap_per_day": int(row[1])}


def count_marketing_log(dsn: str, tenant_id: uuid.UUID) -> int:
    with psycopg.connect(dsn, autocommit=True) as conn:
        return int(conn.execute(
            "SELECT count(*) FROM marketing_activation_log WHERE tenant_id = %s", (tenant_id,),
        ).fetchone()[0])


def fetch_ledger_rows(dsn: str, tenant_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
    """[(outbox_id, status)] of the tenant's proactive ledger."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        return [(r[0], r[1]) for r in conn.execute(
            "SELECT outbox_id, status FROM proactive_ledger WHERE tenant_id = %s", (tenant_id,),
        ).fetchall()]


def fetch_number_health_marketing_counters(dsn: str, channel_id: uuid.UUID) -> int:
    """number_health.sent_today (the marketing daily counter) - the H85 slot count."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        return int(conn.execute(
            "SELECT sent_today FROM number_health WHERE channel_account_id = %s", (channel_id,),
        ).fetchone()[0])


def probe_marketing_log_update(dsn: str, tenant_id: uuid.UUID) -> None:
    """P3.4 H100 negative probe (S29-a exempts testsupport, like S24-a): run
    the forbidden UPDATE against the append-only activation log. Called with
    the sharwa_app DSN (CORE_DATABASE_URL) by the db test, which then asserts
    psycopg InsufficientPrivilege - the privilege check fires before RLS, so
    no tenant context is needed."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE marketing_activation_log SET actor = 'tampered' WHERE tenant_id = %s",
            (tenant_id,),
        )


def probe_marketing_log_delete(dsn: str, tenant_id: uuid.UUID) -> None:
    """P3.4 H100 negative probe (S29-a exempts testsupport): the forbidden
    DELETE against the append-only activation log, as sharwa_app
    (CORE_DATABASE_URL) - the db test asserts InsufficientPrivilege."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "DELETE FROM marketing_activation_log WHERE tenant_id = %s",
            (tenant_id,),
        )


def expire_job_lease(dsn: str, job_id: uuid.UUID) -> None:
    """Simulate a dead worker: the job is 'processing' but its lease is past."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE scheduled_jobs SET locked_until = now() - interval '1 hour' WHERE id = %s",
            (job_id,),
        )


def reset_job_to_pending(dsn: str, job_id: uuid.UUID) -> None:
    """Simulate a crash after the handler's effects but before `complete`."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE scheduled_jobs SET status = 'pending', locked_until = NULL WHERE id = %s",
            (job_id,),
        )


def count_outbox_by_idempotency_key(dsn: str, tenant_id: uuid.UUID, idempotency_key: str) -> int:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT count(*) FROM outbox WHERE tenant_id = %s AND idempotency_key = %s",
            (tenant_id, idempotency_key),
        ).fetchone()
    assert row is not None
    return int(row[0])


def set_cart_last_activity(
    dsn: str, *, tenant_id: uuid.UUID, platform_cart_id: str, last_activity_at: datetime,
) -> None:
    """Engine tests: age/refresh a cart's activity clock (no sleep)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE carts SET last_activity_at = %s "
            "WHERE tenant_id = %s AND platform_cart_id = %s",
            (last_activity_at, tenant_id, platform_cart_id),
        )


def set_cart_status(
    dsn: str, *, tenant_id: uuid.UUID, platform_cart_id: str, status: str,
) -> None:
    """Simulate a finalizing event landing between claim and execution (H92)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE carts SET status = %s, updated_at = now() "
            "WHERE tenant_id = %s AND platform_cart_id = %s",
            (status, tenant_id, platform_cart_id),
        )


def insert_cart_tombstone(
    dsn: str, *, tenant_id: uuid.UUID, platform_cart_id: str, status: str,
) -> None:
    """F-P3-24: simulate a terminal event that committed for a cart while a
    concurrent cart.updated was opening it (the reminder must still not send)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO cart_tombstones (tenant_id, platform_cart_id, status, occurred_at) "
            "VALUES (%s, %s, %s, now())",
            (tenant_id, platform_cart_id, status),
        )


def fetch_cart_tombstone(
    dsn: str, tenant_id: uuid.UUID, platform_cart_id: str,
) -> str | None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT status FROM cart_tombstones WHERE tenant_id = %s AND platform_cart_id = %s",
            (tenant_id, platform_cart_id),
        ).fetchone()
    return row[0] if row is not None else None


def count_outbox_rows(dsn: str, *, tenant_id: uuid.UUID) -> int:
    """The dark-test assertion: how many outbox rows exist for one tenant."""
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT count(*) FROM outbox WHERE tenant_id = %s", (tenant_id,),
        ).fetchone()
    assert row is not None
    return int(row[0])


def fetch_outbox_row_by_idempotency(
    dsn: str, tenant_id: uuid.UUID, idempotency_key: str,
) -> dict | None:
    """The handler-path assertion: the one outbox row a reminder produced."""
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT id, conversation_id, origin, message_class, to_wa_id, payload, status "
            "FROM outbox WHERE tenant_id = %s AND idempotency_key = %s",
            (tenant_id, idempotency_key),
        ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "conversation_id": row[1], "origin": row[2],
            "message_class": row[3], "to_wa_id": row[4], "payload": row[5],
            "status": row[6]}


def count_rows_for_tenant(dsn: str, *, table: str, tenant_id: uuid.UUID) -> int:
    """Count `tenant_id = %s` rows on one of the six cleanup-test tables (a
    CLOSED allowlist - the same whitelist pattern as INBOX_EVENT_ALLOWED_KEYS)."""
    allowed = frozenset({
        "waitlist_entries", "stock_holds", "address_resolutions",
        "order_lookup_attempts", "verifier_blocks", "geo_gazetteer", "proactive_ledger",
        "carts",
    })
    if table not in allowed:
        raise ValueError(f"table {table!r} not in the cleanup-test allowlist")
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            f"SELECT count(*) FROM {table} WHERE tenant_id = %s", (tenant_id,)
        ).fetchone()
    assert row is not None
    return int(row[0])


def seed_catalog_product(
    dsn: str, *, tenant_id: uuid.UUID, platform_product_id: str, category: str | None,
) -> None:
    """P4 Task 10: one catalog product (the category link a category-scoped
    size chart is found through)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO catalog_products (tenant_id, platform_product_id, title, category, source_version) "
            "VALUES (%s, %s, 'size test product', %s, 1)",
            (tenant_id, platform_product_id, category),
        )


def seed_catalog_variant(
    dsn: str, *, tenant_id: uuid.UUID, platform_product_id: str, platform_variant_id: str,
) -> None:
    """P4 Task 18b-2a: one variant under an already-seeded catalog product."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO catalog_variants (tenant_id, product_id, platform_variant_id, source_version) "
            "SELECT %s, id, %s, 1 FROM catalog_products "
            "WHERE tenant_id = %s AND platform_product_id = %s",
            (tenant_id, platform_variant_id, tenant_id, platform_product_id),
        )


def seed_size_chart(
    dsn: str, *, tenant_id: uuid.UUID, scope_type: str, scope_ref: str,
    rows: list[dict[str, object]], fit_type: str = "regular", stretch_pct: str = "0",
) -> uuid.UUID:
    """P4 Task 10: one size chart and its rows. Each row dict carries
    size_label, sort_order and optional numrange LITERALS ('[165,175]') for
    height_cm / weight_kg / chest_cm / waist_cm / hips_cm (absent => NULL)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        head = conn.execute(
            "INSERT INTO size_charts (tenant_id, name, scope_type, scope_ref, fit_type, stretch_pct) "
            "VALUES (%s, 'size test chart', %s, %s, %s, %s::numeric) RETURNING id",
            (tenant_id, scope_type, scope_ref, fit_type, stretch_pct),
        ).fetchone()
        if head is None:
            raise RuntimeError("INSERT ... RETURNING id produced no row")
        chart_id: uuid.UUID = head[0]
        for row in rows:
            conn.execute(
                "INSERT INTO size_chart_rows (tenant_id, chart_id, size_label, sort_order, "
                "height_cm, weight_kg, chest_cm, waist_cm, hips_cm) "
                "VALUES (%s, %s, %s, %s, "
                "%s::numrange, %s::numrange, %s::numrange, %s::numrange, %s::numrange)",
                (tenant_id, chart_id, row["size_label"], row["sort_order"],
                 row.get("height_cm"), row.get("weight_kg"), row.get("chest_cm"),
                 row.get("waist_cm"), row.get("hips_cm")),
            )
    return chart_id


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
    "insert_consent",
    "insert_test_waitlist_entry",
    "seed_number_health",
    "delete_tenant_full",
    "exec_sql_autocommit",
    "fetch_audit_log_by_action",
    "fetch_channel_account_row",
    "fetch_audit_log_by_tenant",
    "fetch_conversation_bot_status",
    "fetch_outbox_status",
    "fetch_outbox_policy_reason",
    "fast_forward_outbox",
    "fast_forward_number_health",
    "db_now",
    "insert_cart",
    "fetch_cart",
    "insert_scheduled_job",
    "fetch_scheduled_job",
    "fetch_scheduled_job_by_key",
    "fast_forward_jobs",
    "age_proactive_ledger",
    "enable_marketing",
    "clear_marketing_activation",
    "age_inbound_messages",
    "fetch_tenant_timezone",
    "fetch_marketing_activation",
    "count_marketing_log",
    "fetch_ledger_rows",
    "fetch_number_health_marketing_counters",
    "probe_marketing_log_update",
    "probe_marketing_log_delete",
    "expire_job_lease",
    "reset_job_to_pending",
    "count_outbox_by_idempotency_key",
    "set_cart_last_activity",
    "set_cart_status",
    "count_outbox_rows",
    "fetch_outbox_row_by_idempotency",
    "fetch_schema_migration_checksum",
    "fetch_schema_migrations",
    "fetch_tenant_row",
    "seed_conversation",
    "seed_inbound_message",
    "seed_outbox_row",
    "seed_suppression",
    "function_exists",
    "insert_channel_account",
    "insert_idempotency_record",
    "insert_tenant_returning_id",
    "count_rows_for_tenant",
    "seed_catalog_product",
    "seed_catalog_variant",
    "seed_size_chart",
    "delete_gazetteer_row",
    "seed_gazetteer_governorate",
    "seed_kill_switch",
    "seed_tenant_gazetteer",
    "seed_tenant_with_rows",
    "seed_two_tenants",
    "stock_held_count_and_qty",
    "waitlist_status_counts",
)
