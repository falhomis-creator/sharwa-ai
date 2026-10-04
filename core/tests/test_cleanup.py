"""core/tests/test_cleanup.py - F-P2-06: delete_tenant_full is the single cleanup
authority. It must remove a tenant's rows from EVERY tenant-scoped table without
raising (the exact defect that took the db suite from 212 to 29 passed)."""
from __future__ import annotations

import os
import uuid

import pytest

from app.db import testsupport as db_testsupport

pytestmark = pytest.mark.db

_TABLES = (
    "waitlist_entries", "stock_holds", "address_resolutions",
    "order_lookup_attempts", "verifier_blocks", "geo_gazetteer", "proactive_ledger",
    "carts",
)


def test_delete_tenant_full_cleans_every_table_without_raising():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    tenant_id = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"cleanup-{uuid.uuid4()}", name="Cleanup Tenant",
    )
    try:
        db_testsupport.seed_tenant_with_rows(dsn, tenant_id=tenant_id)
        # Must not raise (F-P2-06: a partial cleanup hits the geo_gazetteer FK),
        # and must leave zero rows in every seeded table.
        db_testsupport.delete_tenant_full(dsn, tenant_id)
        for table in _TABLES:
            assert db_testsupport.count_rows_for_tenant(dsn, table=table, tenant_id=tenant_id) == 0
        assert db_testsupport.fetch_tenant_row(dsn, tenant_id) is None
    finally:
        # Idempotent safety net: cleaning an already-clean tenant must not raise.
        db_testsupport.delete_tenant_full(dsn, tenant_id)
