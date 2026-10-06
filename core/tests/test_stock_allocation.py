"""core/tests/test_stock_allocation.py - the db-marked back-in-stock race suite
(PROMPT P2.3 §6.1). The atomic allocation is app.allocate_stock_holds (0001):
FOR UPDATE on stock_levels + FOR UPDATE SKIP LOCKED on waitlist_entries. These
tests prove no-overselling under 20-way concurrency, FIFO fairness, re-call
idempotence, expiry cascade, tenant isolation, and the application-level dedupe.
"""
from __future__ import annotations

import os

import pytest

from app import db as core_db
from app.db import repos_stock
from app.db import testsupport as db_testsupport

from .conftest import TENANT_A, TENANT_B

pytestmark = pytest.mark.db

VARIANT = "VAR-L-BLACK"


@pytest.fixture()
def stock_tenants():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.delete_tenant_full(dsn, TENANT_A)
    db_testsupport.delete_tenant_full(dsn, TENANT_B)
    db_testsupport.seed_two_tenants(dsn, TENANT_A, TENANT_B)
    yield
    db_testsupport.clean_stock(dsn, tenant_id=TENANT_A)
    db_testsupport.clean_stock(dsn, tenant_id=TENANT_B)
    db_testsupport.delete_tenant_full(dsn, TENANT_A)
    db_testsupport.delete_tenant_full(dsn, TENANT_B)


def _seed_waiters(dsn, *, tenant_id, variant, n, stagger=False):
    for i in range(n):
        customer_id = db_testsupport.insert_customer(dsn, tenant_id=tenant_id, wa_id=f"w{i}")
        db_testsupport.insert_test_waitlist_entry(
            dsn, tenant_id=tenant_id, customer_id=customer_id,
            platform_variant_id=variant, offset_seconds=(n - i) if stagger else 0,
        )


def test_race_20_waiters_one_unit_no_oversell(stock_tenants):
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.clean_stock(dsn, tenant_id=TENANT_A)
    _seed_waiters(dsn, tenant_id=TENANT_A, variant=VARIANT, n=20)

    results = db_testsupport.allocate_holds_race(
        os.environ["CORE_DATABASE_URL"], tenant_id=TENANT_A,
        variant=VARIANT, available=1, ttl_s=30, n=20,
    )
    assert sum(results) == 1, results

    held, qty = db_testsupport.stock_held_count_and_qty(dsn, tenant_id=TENANT_A, variant=VARIANT)
    assert held == 1
    assert qty == 1
    statuses = db_testsupport.waitlist_status_counts(dsn, tenant_id=TENANT_A, variant=VARIANT)
    assert statuses == {"held": 1, "waiting": 19}


def test_race_20_waiters_three_units(stock_tenants):
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.clean_stock(dsn, tenant_id=TENANT_A)
    _seed_waiters(dsn, tenant_id=TENANT_A, variant=VARIANT, n=20)

    results = db_testsupport.allocate_holds_race(
        os.environ["CORE_DATABASE_URL"], tenant_id=TENANT_A,
        variant=VARIANT, available=3, ttl_s=30, n=20,
    )
    assert sum(results) == 3, results

    held, qty = db_testsupport.stock_held_count_and_qty(dsn, tenant_id=TENANT_A, variant=VARIANT)
    assert held == 3
    assert qty == 3
    statuses = db_testsupport.waitlist_status_counts(dsn, tenant_id=TENANT_A, variant=VARIANT)
    assert statuses == {"held": 3, "waiting": 17}


def test_zero_available_no_hold_no_error(stock_tenants):
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.clean_stock(dsn, tenant_id=TENANT_A)
    _seed_waiters(dsn, tenant_id=TENANT_A, variant=VARIANT, n=5)

    with core_db.tenant_tx(TENANT_A) as conn:
        holds = repos_stock.allocate_holds(conn, platform_variant_id=VARIANT, available=0, ttl_s=30)
    assert holds == []
    held, _ = db_testsupport.stock_held_count_and_qty(dsn, tenant_id=TENANT_A, variant=VARIANT)
    assert held == 0


def test_fairness_oldest_first(stock_tenants):
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.clean_stock(dsn, tenant_id=TENANT_A)
    _seed_waiters(dsn, tenant_id=TENANT_A, variant=VARIANT, n=20, stagger=True)

    with core_db.tenant_tx(TENANT_A) as conn:
        holds = repos_stock.allocate_holds(conn, platform_variant_id=VARIANT, available=1, ttl_s=30)
    assert len(holds) == 1
    held = db_testsupport.held_wa_ids(dsn, tenant_id=TENANT_A, variant=VARIANT)
    assert held == ["w0"]  # H72: the OLDEST waiter is promoted


def test_reallocation_does_not_oversell(stock_tenants):
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.clean_stock(dsn, tenant_id=TENANT_A)
    _seed_waiters(dsn, tenant_id=TENANT_A, variant=VARIANT, n=3)

    with core_db.tenant_tx(TENANT_A) as conn:
        first = repos_stock.allocate_holds(conn, platform_variant_id=VARIANT, available=1, ttl_s=30)
        second = repos_stock.allocate_holds(conn, platform_variant_id=VARIANT, available=1, ttl_s=30)
    assert len(first) == 1
    assert second == []  # H70: the live hold is subtracted; no re-call oversell
    held, qty = db_testsupport.stock_held_count_and_qty(dsn, tenant_id=TENANT_A, variant=VARIANT)
    assert (held, qty) == (1, 1)


def test_expiry_promotes_next(stock_tenants):
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.clean_stock(dsn, tenant_id=TENANT_A)
    _seed_waiters(dsn, tenant_id=TENANT_A, variant=VARIANT, n=3, stagger=True)

    with core_db.tenant_tx(TENANT_A) as conn:
        first = repos_stock.allocate_holds(conn, platform_variant_id=VARIANT, available=1, ttl_s=30)
    assert len(first) == 1

    db_testsupport.force_expire_held_holds(dsn, tenant_id=TENANT_A, variant=VARIANT)
    with core_db.tenant_tx(TENANT_A) as conn:
        expired = repos_stock.expire_holds(conn, platform_variant_id=VARIANT)
        promoted = repos_stock.allocate_holds(conn, platform_variant_id=VARIANT, available=1, ttl_s=30)
    assert expired == 1
    assert len(promoted) == 1  # the next waiter is promoted, not a double count
    held, qty = db_testsupport.stock_held_count_and_qty(dsn, tenant_id=TENANT_A, variant=VARIANT)
    assert (held, qty) == (1, 1)
    held_wa = db_testsupport.held_wa_ids(dsn, tenant_id=TENANT_A, variant=VARIANT)
    assert held_wa == ["w1"]


def test_tenant_isolation(stock_tenants):
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.clean_stock(dsn, tenant_id=TENANT_A)
    db_testsupport.clean_stock(dsn, tenant_id=TENANT_B)
    _seed_waiters(dsn, tenant_id=TENANT_B, variant=VARIANT, n=5)
    _seed_waiters(dsn, tenant_id=TENANT_A, variant=VARIANT, n=2)

    with core_db.tenant_tx(TENANT_A) as conn:
        holds = repos_stock.allocate_holds(conn, platform_variant_id=VARIANT, available=2, ttl_s=30)
    assert len(holds) == 2  # only tenant A's own waiters are promoted
    b_held, _ = db_testsupport.stock_held_count_and_qty(dsn, tenant_id=TENANT_B, variant=VARIANT)
    assert b_held == 0  # H2: another tenant's waiters are never touched


def test_duplicate_customer_prevented(stock_tenants):
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.clean_stock(dsn, tenant_id=TENANT_A)
    customer_id = db_testsupport.insert_customer(dsn, tenant_id=TENANT_A, wa_id="dup")

    with core_db.tenant_tx(TENANT_A) as conn:
        repos_stock.insert_waitlist_entry(
            conn, tenant_id=TENANT_A, customer_id=customer_id,
            conversation_id=None, platform_variant_id=VARIANT,
        )
        # Application-level dedupe: a second LIVE row for the same customer on the
        # same variant is detected before any write (P2.3 §3 item 2).
        assert repos_stock.has_active_waitlist(
            conn, tenant_id=TENANT_A, customer_id=customer_id, platform_variant_id=VARIANT,
        )


def test_race_repeated_10x_fifo_and_no_double_hold(stock_tenants):
    """10 consecutive 20-way races (available < waiters). Every single round the
    active holds equal the available units EXACTLY, go to the OLDEST waiters
    (H72 FIFO, under real concurrency), and no waiter ever holds twice."""
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    available = 3
    for _round in range(10):
        db_testsupport.clean_stock(dsn, tenant_id=TENANT_A)
        _seed_waiters(dsn, tenant_id=TENANT_A, variant=VARIANT, n=20, stagger=True)

        results = db_testsupport.allocate_holds_race(
            os.environ["CORE_DATABASE_URL"], tenant_id=TENANT_A,
            variant=VARIANT, available=available, ttl_s=30, n=20,
        )
        assert sum(results) == available, (results, _round)

        held, qty = db_testsupport.stock_held_count_and_qty(dsn, tenant_id=TENANT_A, variant=VARIANT)
        assert (held, qty) == (available, available), (_round, held, qty)

        winners = db_testsupport.held_wa_ids(dsn, tenant_id=TENANT_A, variant=VARIANT)
        assert winners == [f"w{i}" for i in range(available)], (winners, _round)  # FIFO under concurrency
        assert len(set(winners)) == len(winners), (winners, _round)  # no waiter holds twice

        statuses = db_testsupport.waitlist_status_counts(dsn, tenant_id=TENANT_A, variant=VARIANT)
        assert statuses == {"held": available, "waiting": 20 - available}, (statuses, _round)


def test_notification_idempotent_per_hold(stock_tenants):
    """A hold's notification is written EXACTLY once. Re-running the sweep path
    (_expire_allocate_notify) yields no new hold (allocation idempotency), and
    re-calling _insert_notice with the SAME idempotency key is rejected by the
    DB UNIQUE constraint on outbox.idempotency_key (0001:247) - not by app code
    alone (Constitution §2)."""
    import psycopg
    from psycopg import errors
    from types import SimpleNamespace

    from app.workers import stock as stock_mod
    from app.workers.verify_rules import BlocklistSet

    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.clean_stock(dsn, tenant_id=TENANT_A)

    channel_id = db_testsupport.insert_channel_account(
        dsn, tenant_id=TENANT_A, type_="whatsapp_baileys", session_id="p23-idem-ch",
    )
    customer_id = db_testsupport.insert_customer(dsn, tenant_id=TENANT_A, wa_id="p23-idem-wa")

    with psycopg.connect(dsn, autocommit=True) as conn:
        conv = conn.execute(
            "INSERT INTO conversations (tenant_id, channel_account_id, customer_id) "
            "VALUES (%s, %s, %s) RETURNING id",
            (TENANT_A, channel_id, customer_id),
        ).fetchone()[0]
        entry = conn.execute(
            "INSERT INTO waitlist_entries "
            "(tenant_id, customer_id, conversation_id, platform_variant_id) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (TENANT_A, customer_id, conv, VARIANT),
        ).fetchone()[0]

    settings = SimpleNamespace(
        verify_max_chars=4000, marketing_footer_ar="footer", stock_hold_ttl_s=3600,
    )
    rules = BlocklistSet(profanity=(), competitor=(), disclosure=())
    key = f"stock:{entry}:stock_available"

    def count_outbox() -> int:
        with psycopg.connect(dsn) as c:
            return int(c.execute(
                "SELECT count(*) FROM outbox WHERE idempotency_key = %s", (key,),
            ).fetchone()[0])

    # 1) first allocation + notification.
    with psycopg.connect(dsn) as conn:
        conn.execute("SELECT set_config('app.tenant_id', %s, false)", (str(TENANT_A),))
        holds = repos_stock.allocate_holds(
            conn, platform_variant_id=VARIANT, available=1, ttl_s=3600,
        )
        assert len(holds) == 1
        target = repos_stock.fetch_notify_target(conn, waitlist_entry_id=entry)
        assert target is not None
        stock_mod._insert_notice(
            conn, settings, rules, tenant_id=TENANT_A, entry_id=entry,
            target=target, template_id="stock_available", merge={"title": "T"},
        )
        conn.commit()
    assert count_outbox() == 1

    # 2) re-run the SAME sweep path: hold still active => 0 new holds => 0 new notices.
    with psycopg.connect(dsn) as conn:
        conn.execute("SELECT set_config('app.tenant_id', %s, false)", (str(TENANT_A),))
        stock_mod._expire_allocate_notify(
            conn, settings, rules, tenant_id=TENANT_A, variant=VARIANT, available=1,
        )
        conn.commit()
    assert count_outbox() == 1

    # 3) direct _insert_notice again with the SAME key => DB UNIQUE rejects (savepoint-isolated).
    with psycopg.connect(dsn) as conn:
        conn.execute("SELECT set_config('app.tenant_id', %s, false)", (str(TENANT_A),))
        target = repos_stock.fetch_notify_target(conn, waitlist_entry_id=entry)
        assert target is not None
        conn.execute("SAVEPOINT before_dup")
        try:
            stock_mod._insert_notice(
                conn, settings, rules, tenant_id=TENANT_A, entry_id=entry,
                target=target, template_id="stock_available", merge={"title": "T"},
            )
            dup_rejected = False
        except errors.UniqueViolation:
            dup_rejected = True
        conn.execute("ROLLBACK TO SAVEPOINT before_dup")
        assert dup_rejected
        conn.rollback()
    assert count_outbox() == 1

    # Cleanup: the stock_tenants fixture's clean_stock deletes customers but NOT
    # conversations; our conversation references the customer, so remove our rows
    # (in FK order) first to keep teardown clean.
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM outbox WHERE tenant_id = %s", (TENANT_A,))
        conn.execute("DELETE FROM stock_holds WHERE tenant_id = %s", (TENANT_A,))
        conn.execute("DELETE FROM waitlist_entries WHERE tenant_id = %s", (TENANT_A,))
        conn.execute("DELETE FROM conversations WHERE tenant_id = %s", (TENANT_A,))


def test_duplicate_live_row_rejected_by_db_constraint(stock_tenants):
    """The DB partial unique index itself rejects a duplicate LIVE row, even when
    the caller bypasses the repo's read-before-write dedupe (Constitution §2: the
    guarantee is a REAL constraint, not an app-code check). Two raw INSERTs for
    the same (tenant, customer, variant) => the second raises UniqueViolation."""
    import psycopg
    from psycopg import errors

    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.clean_stock(dsn, tenant_id=TENANT_A)
    customer_id = db_testsupport.insert_customer(dsn, tenant_id=TENANT_A, wa_id="dup-raw")

    conn = psycopg.connect(dsn)
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO waitlist_entries (tenant_id, customer_id, platform_variant_id) "
                "VALUES (%s, %s, %s)",
                (TENANT_A, customer_id, VARIANT),
            )
            # Nested transaction => SAVEPOINT: the failing second INSERT rolls back
            # to the savepoint, so the outer transaction commits the FIRST row only.
            with pytest.raises(errors.UniqueViolation):
                with conn.transaction():
                    conn.execute(
                        "INSERT INTO waitlist_entries (tenant_id, customer_id, platform_variant_id) "
                        "VALUES (%s, %s, %s)",
                        (TENANT_A, customer_id, VARIANT),
                    )
    finally:
        conn.close()

    statuses = db_testsupport.waitlist_status_counts(dsn, tenant_id=TENANT_A, variant=VARIANT)
    assert statuses == {"waiting": 1}


def test_sweep_once_allocates_and_notifies_with_fake_commerce(stock_tenants):
    """sweep_once with an in-memory commerce port (no network, no WhatsApp): the
    available units are allocated to the OLDEST waiters and one notification per
    hold is written to the outbox - nothing else."""
    import datetime
    import psycopg
    from types import SimpleNamespace

    from app.workers import stock as stock_mod
    from app.workers.verify_rules import BlocklistSet

    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.clean_stock(dsn, tenant_id=TENANT_A)

    channel_id = db_testsupport.insert_channel_account(
        dsn, tenant_id=TENANT_A, type_="whatsapp_baileys", session_id="p23-sweep-ch",
    )
    for i in (0, 1):  # w0 (oldest) then w1
        customer_id = db_testsupport.insert_customer(dsn, tenant_id=TENANT_A, wa_id=f"w{i}")
        with psycopg.connect(dsn, autocommit=True) as conn:
            conv = conn.execute(
                "INSERT INTO conversations (tenant_id, channel_account_id, customer_id) "
                "VALUES (%s, %s, %s) RETURNING id",
                (TENANT_A, channel_id, customer_id),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO waitlist_entries "
                "(tenant_id, customer_id, conversation_id, platform_variant_id, created_at) "
                "VALUES (%s, %s, %s, %s, now() - make_interval(secs => %s))",
                (TENANT_A, customer_id, conv, VARIANT, 2 - i),
            )

    class _FakeCommerce:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def get_stock_observation(self, *, tenant_ref, platform_variant_id):
            self.calls.append((tenant_ref, platform_variant_id))
            return {
                "available": 2,
                "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }

    commerce = _FakeCommerce()
    settings = SimpleNamespace(
        stock_max_variants_per_cycle=100, stock_observation_max_age_s=300,
        stock_hold_ttl_s=3600, verify_max_chars=4000, marketing_footer_ar="footer",
    )
    rules = BlocklistSet(profanity=(), competitor=(), disclosure=())

    stock_mod.sweep_once(commerce, settings, rules)

    assert commerce.calls  # the fake port was actually consulted
    held, qty = db_testsupport.stock_held_count_and_qty(dsn, tenant_id=TENANT_A, variant=VARIANT)
    assert (held, qty) == (2, 2)  # available == holds, exactly
    winners = db_testsupport.held_wa_ids(dsn, tenant_id=TENANT_A, variant=VARIANT)
    assert winners == ["w0", "w1"]  # oldest first

    with psycopg.connect(dsn) as conn:
        notifs = int(conn.execute(
            "SELECT count(*) FROM outbox WHERE tenant_id = %s AND origin = 'automation'",
            (TENANT_A,),
        ).fetchone()[0])
    assert notifs == 2  # one notification per hold

    # cleanup (FK order) so the fixture's clean_stock can delete customers
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM outbox WHERE tenant_id = %s", (TENANT_A,))
        conn.execute("DELETE FROM stock_holds WHERE tenant_id = %s", (TENANT_A,))
        conn.execute("DELETE FROM waitlist_entries WHERE tenant_id = %s", (TENANT_A,))
        conn.execute("DELETE FROM conversations WHERE tenant_id = %s", (TENANT_A,))


def test_sweep_once_with_none_commerce_allocates_nothing(stock_tenants):
    """Documents the current deployment idleness: sweep_once assumes a configured
    commerce port. Passing None fails fast (AttributeError, before any write) so
    nothing is allocated or notified; the actual guard is the thread-level
    `if self.commerce_port is not None` in RealtimeWorker._run_stock_sweep
    (realtime.py:857), which is why the worker idles when COMMERCE_BASE_URL is
    unset."""
    import psycopg
    from types import SimpleNamespace

    from app.workers import stock as stock_mod

    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.clean_stock(dsn, tenant_id=TENANT_A)
    customer_id = db_testsupport.insert_customer(dsn, tenant_id=TENANT_A, wa_id="w0")
    db_testsupport.insert_test_waitlist_entry(
        dsn, tenant_id=TENANT_A, customer_id=customer_id, platform_variant_id=VARIANT,
    )

    settings = SimpleNamespace(stock_max_variants_per_cycle=100)

    with pytest.raises(AttributeError):
        stock_mod.sweep_once(None, settings, rules=None)

    held, _ = db_testsupport.stock_held_count_and_qty(dsn, tenant_id=TENANT_A, variant=VARIANT)
    assert held == 0
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM outbox WHERE tenant_id = %s", (TENANT_A,)).fetchone()[0] == 0


