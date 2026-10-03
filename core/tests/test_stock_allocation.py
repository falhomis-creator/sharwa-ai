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

