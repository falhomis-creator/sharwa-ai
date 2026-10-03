"""core/tests/test_policy_stop_db.py - F-P3-17: one customer's STOP cancels only
that customer's queue, not everyone's.
"""
from __future__ import annotations

import os
import uuid

import pytest

from app import db as core_db
from app.db import repos_ingest
from app.db import repos_outbox
from app.db import repos_policy
from app.db import testsupport as db_testsupport
from app.workers import proactive

pytestmark = pytest.mark.db


@pytest.fixture()
def stop_ctx():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    tid = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"stop-{uuid.uuid4()}", name="Stop Tenant",
    )
    chid = db_testsupport.insert_channel_account(
        dsn, tenant_id=tid, type_="whatsapp_baileys",
        session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
    )
    yield dsn, tid, chid
    db_testsupport.delete_tenant_full(dsn, tid)


def _seed_notice(dsn, tid, chid, wa_id):
    cid = db_testsupport.insert_customer(dsn, tenant_id=tid, wa_id=wa_id)
    conv = db_testsupport.seed_conversation(
        dsn, tenant_id=tid, channel_id=chid, customer_id=cid, bot_status="active", epoch=0,
    )
    oid = db_testsupport.seed_outbox_row(
        dsn, tenant_id=tid, channel_id=chid, conversation_id=conv,
        origin="automation", message_class="utility", to_wa_id=wa_id,
        payload={"template": "stock_available", "text": "عاد «X» للتوفر!"},
    )
    return cid, oid


def test_cancel_pending_proactive_scopes_to_one_customer(stop_ctx):
    dsn, tid, chid = stop_ctx
    a_cid, a_oid = _seed_notice(dsn, tid, chid, "967700000001")
    b_cid, b_oid = _seed_notice(dsn, tid, chid, "967700000002")
    with core_db.tenant_tx(tid) as conn:
        cancelled = repos_policy.cancel_pending_proactive(
            conn, tenant_id=tid, customer_id=a_cid,
            template_ids=proactive.template_ids_for_scope("back_in_stock"),
        )
    assert cancelled == 1
    assert db_testsupport.fetch_outbox_status(dsn, a_oid) == "dropped_policy"
    assert db_testsupport.fetch_outbox_status(dsn, b_oid) == "pending"


def test_optout_write_scopes_suppression_and_cancel_to_one_customer(stop_ctx):
    dsn, tid, chid = stop_ctx
    a_cid, a_oid = _seed_notice(dsn, tid, chid, "967700000001")
    b_cid, b_oid = _seed_notice(dsn, tid, chid, "967700000002")
    # The exact sequence realtime.py runs when a customer's message opts out.
    with core_db.tenant_tx(tid) as conn:
        repos_ingest.insert_suppressions(
            conn, tenant_id=tid, customer_id=a_cid,
            scopes=repos_ingest.OPTOUT_SCOPES, reason=repos_ingest.OPTOUT_REASON,
        )
        for scope in repos_ingest.OPTOUT_SCOPES:
            template_ids = proactive.template_ids_for_scope(scope)
            if template_ids:
                repos_policy.cancel_pending_proactive(
                    conn, tenant_id=tid, customer_id=a_cid, template_ids=template_ids,
                )
    assert db_testsupport.fetch_outbox_status(dsn, a_oid) == "dropped_policy"
    assert db_testsupport.fetch_outbox_status(dsn, b_oid) == "pending"
    with core_db.tenant_tx(tid) as conn:
        assert repos_outbox.has_suppression(conn, tid, a_cid, "back_in_stock") is True
        assert repos_outbox.has_suppression(conn, tid, b_cid, "back_in_stock") is False
