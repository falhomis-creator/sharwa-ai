"""core/tests/test_policy_cli_db.py - F-P3-19 coverage: policy status/reinstate
(H81/H48). No phone / no customer text may surface.
"""
from __future__ import annotations

import os
import uuid

import pytest

from app.db import repos_policy
from app.db import testsupport as db_testsupport

pytestmark = pytest.mark.db

_BANNED_KEYS = ("to_wa_id", "wa_id", "phone", "phone_e164", "text", "body")


@pytest.fixture()
def cli_ctx():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    tid = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"cli-{uuid.uuid4()}", name="CLI Tenant",
    )
    chid = db_testsupport.insert_channel_account(
        dsn, tenant_id=tid, type_="whatsapp_baileys",
        session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
    )
    yield dsn, tid, chid
    db_testsupport.delete_tenant_full(dsn, tid)


def test_policy_status_exposes_health_only(cli_ctx):
    dsn, tid, chid = cli_ctx
    db_testsupport.seed_number_health(dsn, tenant_id=tid, channel_id=chid, state="paused")
    row = repos_policy.policy_status(dsn, channel_id=chid)
    assert row is not None
    assert row["state"] == "paused"
    for key in row:
        assert key not in _BANNED_KEYS, f"status leaks {key!r} (H48)"


def test_policy_reinstate_exits_paused_and_records_reason(cli_ctx):
    dsn, tid, chid = cli_ctx
    db_testsupport.seed_number_health(dsn, tenant_id=tid, channel_id=chid, state="paused")
    tenant_id = repos_policy.policy_reinstate(dsn, channel_id=chid, reason="owner cleared it")
    assert tenant_id == tid
    row = repos_policy.policy_status(dsn, channel_id=chid)
    assert row["state"] == "healthy"
    assert row["state_reason"] == "reinstated: owner cleared it"
    assert row["warmup_started_at"] is None  # warm-up restarts from degree 0
    audit = db_testsupport.fetch_audit_log_by_action(dsn, "policy.reinstate")
    assert audit is not None and audit[1] == "policy.reinstate"
