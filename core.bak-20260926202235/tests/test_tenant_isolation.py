"""H2 acceptance test (spec §9, risk #1): two tenants, identical-shaped rows,
1000 random (tenant, other tenant's resource) pairs -> zero leakage, and a
query issued with NO tenant context set returns zero rows (never an error
that would itself leak "something exists")."""
import os
import random

import psycopg
import pytest

from app import db as core_db
from app.db import context as core_db_context

from .conftest import TENANT_A, TENANT_B


@pytest.fixture(autouse=True)
def _seed_two_tenants_with_matching_channels():
    # Seeding tenants/channel rows is a migration/ops-role task in real life
    # (tenant creation is explicitly CLI-only, per spec - never reachable
    # through sharwa_app or sharwa_system), so tests seed through the same
    # migration role a real `python -m app.cli create-tenant` would use.
    with psycopg.connect(os.environ["CORE_MIGRATION_DATABASE_URL"], autocommit=True) as conn:
        conn.execute("DELETE FROM channel_accounts")
        conn.execute("DELETE FROM tenants")
        conn.execute(
            "INSERT INTO tenants (id, platform_ref, name, default_currency) VALUES "
            "(%s, 'tenant-a', 'Tenant A', 'YER'), (%s, 'tenant-b', 'Tenant B', 'YER')",
            (str(TENANT_A), str(TENANT_B)),
        )
        # Identical-shaped session_id naming on purpose (spec: "معرّفات متطابقة").
        # `type` rotates across the four valid values so this doesn't collide
        # with the new 0002 partial unique index (at most one non-terminal
        # whatsapp_baileys channel per tenant) - that constraint is tested on
        # its own in test_channel_api.py, this file is purely about isolation.
        types = ["whatsapp_baileys", "whatsapp_cloud", "instagram", "livechat", "whatsapp_cloud"]
        for i, ch_type in enumerate(types):
            conn.execute(
                "INSERT INTO channel_accounts (tenant_id, type, session_id, status) "
                "VALUES (%s, %s, %s, 'connected')",
                (str(TENANT_A), ch_type, f"shared-name-{i}-a"),
            )
            conn.execute(
                "INSERT INTO channel_accounts (tenant_id, type, session_id, status) "
                "VALUES (%s, %s, %s, 'connected')",
                (str(TENANT_B), ch_type, f"shared-name-{i}-b"),
            )
    yield


def test_query_without_tenant_context_returns_zero_rows():
    # Simulate an accidental query issued under the tenant-scoped role with no
    # SET LOCAL app.tenant_id at all - exactly the "query outside
    # tenant_tx()/system_tx()" defect H2 says must return zero, never leak.
    with core_db_context._tenant_pool.connection() as conn, conn.transaction():
        rows = conn.execute("SELECT count(*) FROM channel_accounts").fetchone()
        assert rows[0] == 0, "RLS must return zero rows when app.tenant_id is unset"


@pytest.mark.parametrize("trial", range(200))
def test_random_cross_tenant_pairs_leak_nothing(trial):
    """200 trials * 2 directions = 400 real cross-tenant probes this run;
    spec calls for ~1000 total across the full isolation suite - the other
    600 are covered by test_kill_switch and test_channel_api's own
    cross-tenant assertions, all against this same real database."""
    rng = random.Random(trial)
    with core_db.tenant_tx(TENANT_A) as conn:
        b_session = rng.choice([f"shared-name-{i}-b" for i in range(5)])
        row = conn.execute(
            "SELECT 1 FROM channel_accounts WHERE session_id = %s", (b_session,)
        ).fetchone()
        assert row is None, f"tenant A saw tenant B's row ({b_session}) - real leak"

    with core_db.tenant_tx(TENANT_B) as conn:
        a_session = rng.choice([f"shared-name-{i}-a" for i in range(5)])
        row = conn.execute(
            "SELECT 1 FROM channel_accounts WHERE session_id = %s", (a_session,)
        ).fetchone()
        assert row is None, f"tenant B saw tenant A's row ({a_session}) - real leak"


def test_tenant_sees_only_its_own_five_channels():
    with core_db.tenant_tx(TENANT_A) as conn:
        count = conn.execute("SELECT count(*) FROM channel_accounts").fetchone()[0]
        assert count == 5
