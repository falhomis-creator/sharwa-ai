"""P4 Task 18b-2a (F-P4-07): product memory on real PostgreSQL. The turn writes
the shown PRODUCT ids into conversations.slots; the waitlist coordinator resolves
the product to its single variant; both under RLS only."""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

from app import db as core_db
from app.db import repos_stock, repos_summary
from app.db import testsupport as db_testsupport
from app.workers import stock

pytestmark = pytest.mark.db

TENANT_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture()
def ctx():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.delete_tenant_full(dsn, TENANT_A)
    db_testsupport.delete_tenant_full(dsn, TENANT_B)
    db_testsupport.seed_two_tenants(dsn, TENANT_A, TENANT_B)
    chid = db_testsupport.insert_channel_account(
        dsn, tenant_id=TENANT_A, type_="whatsapp_baileys", session_id=f"sess-{uuid.uuid4()}",
    )
    wa = f"9677{uuid.uuid4().int % 10**10:010d}"
    cid = db_testsupport.insert_customer(dsn, tenant_id=TENANT_A, wa_id=wa)
    conv = db_testsupport.seed_conversation(dsn, tenant_id=TENANT_A, channel_id=chid, customer_id=cid)
    yield dsn, conv
    db_testsupport.delete_tenant_full(dsn, TENANT_A)
    db_testsupport.delete_tenant_full(dsn, TENANT_B)


def _slots(tenant: uuid.UUID, conv: uuid.UUID) -> dict:
    with core_db.tenant_tx(tenant) as conn:
        return repos_stock.read_conversation_slots(conn, conversation_id=conv)


def test_record_shown_products_merges_and_caps_at_three(ctx):
    dsn, conv = ctx
    db_testsupport.exec_sql_autocommit(  # conv is a uuid this test created, not input
        dsn, f"UPDATE conversations SET slots = '{{\"summary_seq\": 7}}'::jsonb WHERE id = '{conv}'",  # noqa: S608
    )
    with core_db.tenant_tx(TENANT_A) as conn:
        repos_summary.record_shown_products(
            conn, tenant_id=TENANT_A, conversation_id=conv, platform_product_ids=["P1", "P2", "P3", "P4"],
        )
    assert _slots(TENANT_A, conv) == {"summary_seq": 7, "last_shown_product_ids": ["P1", "P2", "P3"]}
    with core_db.tenant_tx(TENANT_A) as conn:  # a later list REPLACES the memory
        repos_summary.record_shown_products(
            conn, tenant_id=TENANT_A, conversation_id=conv, platform_product_ids=["P9"],
        )
    assert _slots(TENANT_A, conv)["last_shown_product_ids"] == ["P9"]


def test_another_tenant_cannot_write_the_memory(ctx):
    _dsn, conv = ctx
    with core_db.tenant_tx(TENANT_B) as conn:  # RLS: B sees no such conversation
        repos_summary.record_shown_products(
            conn, tenant_id=TENANT_B, conversation_id=conv, platform_product_ids=["EVIL"],
        )
    assert "last_shown_product_ids" not in _slots(TENANT_A, conv)


def test_variant_ids_for_product_under_rls(ctx):
    dsn, _conv = ctx
    db_testsupport.seed_catalog_product(dsn, tenant_id=TENANT_A, platform_product_id="P-A", category=None)
    for v in ("V-2", "V-1"):
        db_testsupport.seed_catalog_variant(
            dsn, tenant_id=TENANT_A, platform_product_id="P-A", platform_variant_id=v,
        )
    with core_db.tenant_tx(TENANT_A) as conn:
        found = repos_stock.variant_ids_for_product(conn, tenant_id=TENANT_A, platform_product_id="P-A")
        missing = repos_stock.variant_ids_for_product(conn, tenant_id=TENANT_A, platform_product_id="NOPE")
    assert (found, missing) == (["V-1", "V-2"], [])
    with core_db.tenant_tx(TENANT_B) as conn:
        assert repos_stock.variant_ids_for_product(conn, tenant_id=TENANT_B, platform_product_id="P-A") == []
    db_testsupport.exec_sql_autocommit(
        dsn, "UPDATE catalog_products SET active = false WHERE platform_product_id = 'P-A'",
    )
    with core_db.tenant_tx(TENANT_A) as conn:
        assert repos_stock.variant_ids_for_product(conn, tenant_id=TENANT_A, platform_product_id="P-A") == []


def _join(conv: uuid.UUID):
    with core_db.tenant_tx(TENANT_A) as conn:
        return stock.join_waitlist(
            conn, SimpleNamespace(stock_max_waitlist_per_customer=10),
            tenant_id=TENANT_A, conversation_id=conv, bodies=("سجّلني",),
        )


def test_shown_single_variant_product_joins_the_waitlist_end_to_end(ctx):
    dsn, conv = ctx
    db_testsupport.seed_catalog_product(dsn, tenant_id=TENANT_A, platform_product_id="P-ONE", category=None)
    db_testsupport.seed_catalog_variant(
        dsn, tenant_id=TENANT_A, platform_product_id="P-ONE", platform_variant_id="V-ONE",
    )
    with core_db.tenant_tx(TENANT_A) as conn:
        repos_summary.record_shown_products(
            conn, tenant_id=TENANT_A, conversation_id=conv, platform_product_ids=["P-ONE"],
        )
    d = _join(conv)
    assert (d.kind, d.platform_variant_id) == ("joined", "V-ONE")


def test_shown_multi_variant_product_does_not_join(ctx):
    dsn, conv = ctx
    db_testsupport.seed_catalog_product(dsn, tenant_id=TENANT_A, platform_product_id="P-TWO", category=None)
    for v in ("V-S", "V-M"):
        db_testsupport.seed_catalog_variant(
            dsn, tenant_id=TENANT_A, platform_product_id="P-TWO", platform_variant_id=v,
        )
    with core_db.tenant_tx(TENANT_A) as conn:
        repos_summary.record_shown_products(
            conn, tenant_id=TENANT_A, conversation_id=conv, platform_product_ids=["P-TWO"],
        )
    assert _join(conv).kind == "no_variant"
    assert db_testsupport.count_rows_for_tenant(dsn, table="waitlist_entries", tenant_id=TENANT_A) == 0
