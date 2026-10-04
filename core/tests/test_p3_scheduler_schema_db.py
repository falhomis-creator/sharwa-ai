"""core/tests/test_p3_scheduler_schema_db.py - P3.2 Stage B: the 0015 migration's
shape and its role boundaries on a real DB (H86: every SQL path in its REAL
calling role - sharwa_app through tenant_tx, sharwa_system through system_tx).

carts RLS is EXPLICIT (the F-P2-06 lesson: the 0001 loop only covered tables
that existed then); purge_old_carts is the one sanctioned cross-tenant delete
(sharwa_system only); scheduler_job_stats is the system-role read surface
(no payloads, H48).
"""
from __future__ import annotations

import os
import uuid
from datetime import timedelta

import psycopg
import pytest

from app import db as core_db
from app.db import testsupport as db_testsupport

pytestmark = pytest.mark.db


@pytest.fixture()
def schema_tenant():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    tid = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"p32schema-{uuid.uuid4()}", name="P3.2 Schema Tenant",
    )
    yield dsn, tid
    db_testsupport.delete_tenant_full(dsn, tid)


def _table_exists(dsn: str, table: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = %s",
            (table,),
        ).fetchone()
    return row is not None


def test_carts_table_exists_with_rls_and_policy(schema_tenant):
    dsn, _tid = schema_tenant
    assert _table_exists(dsn, "carts")
    with psycopg.connect(dsn) as conn:
        rls = conn.execute(
            "SELECT rowsecurity FROM pg_tables WHERE schemaname = 'public' AND tablename = 'carts'",
        ).fetchone()
        assert rls is not None and rls[0] is True, "carts must have RLS enabled explicitly"
        policy = conn.execute(
            "SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'carts' "
            "AND policyname = 'tenant_isolation'",
        ).fetchone()
        assert policy is not None, "carts must carry the tenant_isolation policy (F-P2-06)"


def test_scheduled_jobs_engine_columns_exist(schema_tenant):
    dsn, _tid = schema_tenant
    with psycopg.connect(dsn) as conn:
        cols = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT column_name, column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'scheduled_jobs' "
                "AND column_name IN ('last_error','finished_at','cancel_reason','max_lateness_s')",
            ).fetchall()
        }
    assert set(cols) == {"last_error", "finished_at", "cancel_reason", "max_lateness_s"}
    assert cols["max_lateness_s"] is not None and "0" in cols["max_lateness_s"]


def test_carts_rls_isolates_tenants(schema_tenant):
    dsn, tid = schema_tenant
    other = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"p32schema-b-{uuid.uuid4()}", name="P3.2 Schema Tenant B",
    )
    try:
        cust_a = db_testsupport.insert_customer(dsn, tenant_id=tid, wa_id="967732000001")
        cust_b = db_testsupport.insert_customer(dsn, tenant_id=other, wa_id="967732000001")
        db_testsupport.insert_cart(
            dsn, tenant_id=tid, customer_id=cust_a, platform_cart_id="CART-A",
        )
        db_testsupport.insert_cart(
            dsn, tenant_id=other, customer_id=cust_b, platform_cart_id="CART-B",
        )
        with core_db.tenant_tx(tid) as conn:
            seen = conn.execute("SELECT platform_cart_id FROM carts").fetchall()
        assert [r[0] for r in seen] == ["CART-A"], "tenant A must not see tenant B's carts"
        with core_db.tenant_tx(other) as conn:
            seen_b = conn.execute("SELECT platform_cart_id FROM carts").fetchall()
        assert [r[0] for r in seen_b] == ["CART-B"]
    finally:
        db_testsupport.delete_tenant_full(dsn, other)


def test_system_role_cannot_read_carts_directly(schema_tenant):
    """H91: sharwa_system has NO direct grant on carts - the only cross-tenant
    surface is app.purge_old_carts (the sanctioned delete path)."""
    dsn, tid = schema_tenant
    cust = db_testsupport.insert_customer(dsn, tenant_id=tid, wa_id="967732000002")
    db_testsupport.insert_cart(
        dsn, tenant_id=tid, customer_id=cust, platform_cart_id="CART-SYS",
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with core_db.system_tx() as conn:
            conn.execute("SELECT count(*) FROM carts").fetchone()


def test_purge_old_carts_deletes_old_final_keeps_open(schema_tenant):
    """The F-P3-10 rule: a function nobody called in its real role is presumed
    broken. Called here as sharwa_system (system_tx), with pinned times."""
    dsn, tid = schema_tenant
    now = db_testsupport.db_now(dsn)
    cust = db_testsupport.insert_customer(dsn, tenant_id=tid, wa_id="967732000003")
    db_testsupport.insert_cart(  # old + final -> purged
        dsn, tenant_id=tid, customer_id=cust, platform_cart_id="CART-OLD-FINAL",
        status="recovered", last_activity_at=now - timedelta(days=30),
    )
    db_testsupport.insert_cart(  # old + open -> KEPT (only final carts purge)
        dsn, tenant_id=tid, customer_id=cust, platform_cart_id="CART-OLD-OPEN",
        status="open", last_activity_at=now - timedelta(days=30),
    )
    db_testsupport.insert_cart(  # fresh + final -> kept
        dsn, tenant_id=tid, customer_id=cust, platform_cart_id="CART-NEW-FINAL",
        status="reminded", last_activity_at=now - timedelta(days=1),
    )
    with core_db.system_tx() as conn:
        deleted = conn.execute(
            "SELECT app.purge_old_carts(interval '14 days')"
        ).fetchone()
    assert deleted is not None and int(deleted[0]) == 1
    remaining = {
        key: (db_testsupport.fetch_cart(dsn, tid, key) or {}).get("status")
        for key in ("CART-OLD-FINAL", "CART-OLD-OPEN", "CART-NEW-FINAL")
    }
    assert remaining == {
        "CART-OLD-FINAL": None,  # purged
        "CART-OLD-OPEN": "open",
        "CART-NEW-FINAL": "reminded",
    }


def test_scheduler_job_stats_callable_as_system_role(schema_tenant):
    dsn, tid = schema_tenant
    db_testsupport.insert_scheduled_job(
        dsn, tenant_id=tid, kind="cart_reminder", dedupe_key="cart:X:stage1",
        payload={"cart_id": "X"}, max_lateness_s=3600,
    )
    with core_db.system_tx() as conn:
        rows = conn.execute(
            "SELECT v_kind, v_status, v_jobs, v_oldest_due_s FROM app.scheduler_job_stats() "
            "WHERE v_kind = 'cart_reminder'"
        ).fetchall()
    assert rows, "scheduler_job_stats must return the seeded pending job"
    by_status = {r[1]: r for r in rows}
    assert int(by_status["pending"][2]) == 1
    assert float(by_status["pending"][3]) >= 0.0
