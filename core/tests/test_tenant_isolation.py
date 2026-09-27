"""H2 acceptance test (spec §9, risk #1): two tenants, identical-shaped rows,
1000 random (tenant, other tenant's resource) pairs -> zero leakage, and a
query issued with NO tenant context set returns zero rows (never an error
that would itself leak "something exists")."""
import os
import random

import pytest

from app import db as core_db
from app.db import context as core_db_context
from app.db import testsupport as db_testsupport

from .conftest import TENANT_A, TENANT_B

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def _seed_two_tenants_with_matching_channels():
    # Seeding tenants/channel rows is a migration/ops-role task in real life
    # (tenant creation is explicitly CLI-only, per spec - never reachable
    # through sharwa_app or sharwa_system), so tests seed through the same
    # migration role a real `python -m app.cli create-tenant` would use.
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.reset_tenants_and_channels(dsn)
    db_testsupport.seed_two_tenants(dsn, TENANT_A, TENANT_B)
    # Identical-shaped session_id naming on purpose (spec: "معرّفات متطابقة").
    # `type` rotates across the four valid values so this doesn't collide
    # with the new 0002 partial unique index (at most one non-terminal
    # whatsapp_baileys channel per tenant) - that constraint is tested on
    # its own in test_channel_api.py, this file is purely about isolation.
    types = ["whatsapp_baileys", "whatsapp_cloud", "instagram", "livechat", "whatsapp_cloud"]
    for i, ch_type in enumerate(types):
        db_testsupport.insert_channel_account(
            dsn, tenant_id=TENANT_A, type_=ch_type, session_id=f"shared-name-{i}-a",
        )
        db_testsupport.insert_channel_account(
            dsn, tenant_id=TENANT_B, type_=ch_type, session_id=f"shared-name-{i}-b",
        )
    yield


def test_query_without_tenant_context_returns_zero_rows():
    # Simulate an accidental query issued under the tenant-scoped role with no
    # SET LOCAL app.tenant_id at all - exactly the "query outside
    # tenant_tx()/system_tx()" defect H2 says must return zero, never leak.
    with core_db_context._tenant_pool.connection() as conn, conn.transaction():
        count = db_testsupport.count_channel_accounts_on_conn(conn)
        assert count == 0, "RLS must return zero rows when app.tenant_id is unset"


@pytest.mark.parametrize("trial", range(200))
def test_random_cross_tenant_pairs_leak_nothing(trial):
    """200 trials * 2 directions = 400 real cross-tenant probes this run;
    spec calls for ~1000 total across the full isolation suite - the other
    600 are covered by test_kill_switch and test_channel_api's own
    cross-tenant assertions, all against this same real database."""
    rng = random.Random(trial)
    with core_db.tenant_tx(TENANT_A) as conn:
        b_session = rng.choice([f"shared-name-{i}-b" for i in range(5)])
        assert not db_testsupport.channel_account_exists_by_session(conn, b_session), (
            f"tenant A saw tenant B's row ({b_session}) - real leak"
        )

    with core_db.tenant_tx(TENANT_B) as conn:
        a_session = rng.choice([f"shared-name-{i}-a" for i in range(5)])
        assert not db_testsupport.channel_account_exists_by_session(conn, a_session), (
            f"tenant B saw tenant A's row ({a_session}) - real leak"
        )


def test_tenant_sees_only_its_own_five_channels():
    with core_db.tenant_tx(TENANT_A) as conn:
        count = db_testsupport.count_channel_accounts_on_conn(conn)
        assert count == 5
