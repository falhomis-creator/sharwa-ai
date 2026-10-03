"""core/tests/test_policy_gate_db.py - the proactive gate (E) on a real DB.

The gate re-reads the world at send time (H76): consent, suppression, prior
interaction, channel state, health - then decides drop/defer/reserve. Columns
are read by name (H86); time is injected via `now`.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app import db as core_db
from app.db import repos_outbox
from app.db import repos_policy
from app.db import testsupport as db_testsupport
from app.workers import policy_gate

pytestmark = pytest.mark.db

PNOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)


def _settings(**kw):
    d = dict(
        send_policy_marketing_ttl_h=24, send_policy_utility_ttl_h=6,
        send_policy_interaction_window_d=180, send_policy_active_chat_cooldown_s=1800,
        send_policy_quiet_start="22:00", send_policy_quiet_end="09:00",
        send_policy_marketing_per_24h=1, send_policy_marketing_per_7d=2,
        send_policy_utility_per_24h=3,
        send_policy_marketing_gap=(20, 60), send_policy_utility_gap=(8, 20),
    )
    d.update(kw)
    return SimpleNamespace(**d)


@pytest.fixture()
def gated_ctx():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    tid = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"gate-{uuid.uuid4()}", name="Gate Tenant",
    )
    chid = db_testsupport.insert_channel_account(
        dsn, tenant_id=tid, type_="whatsapp_baileys",
        session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
    )
    cid = db_testsupport.insert_customer(dsn, tenant_id=tid, wa_id="967700000002")
    conv = db_testsupport.seed_conversation(
        dsn, tenant_id=tid, channel_id=chid, customer_id=cid, bot_status="active", epoch=0,
    )
    yield dsn, tid, chid, cid, conv
    db_testsupport.delete_tenant_full(dsn, tid)


def _seed_eligible(dsn, tid, chid, cid, conv):
    db_testsupport.seed_number_health(
        dsn, tenant_id=tid, channel_id=chid, daily_cap=50, sent_today=0, day="2026-10-03",
    )
    db_testsupport.insert_consent(dsn, tenant_id=tid, customer_id=cid, scope="back_in_stock", granted=True)
    db_testsupport.seed_inbound_message(dsn, tenant_id=tid, conversation_id=conv, body="مرحبا")


def _row(oid, tid, chid, conv, payload, *, created_at=PNOW):
    return repos_outbox.OutboxRow(
        id=oid, tenant_id=tid, conversation_id=conv, channel_account_id=chid,
        idempotency_key=f"k-{oid}", origin="automation", message_class="utility",
        expected_epoch=None, to_wa_id="967700000002", payload=payload,
        attempts=0, created_at=created_at,
    )


def _seed_notice(dsn, tid, chid, conv):
    return db_testsupport.seed_outbox_row(
        dsn, tenant_id=tid, channel_id=chid, conversation_id=conv,
        origin="automation", message_class="utility", to_wa_id="967700000002",
        payload={"template": "stock_available", "text": "عاد «X» للتوفر!"},
    )


def test_gate_reserves_eligible_stock_notice(gated_ctx):
    dsn, tid, chid, cid, conv = gated_ctx
    _seed_eligible(dsn, tid, chid, cid, conv)
    oid = _seed_notice(dsn, tid, chid, conv)
    decision = policy_gate.gate(
        _settings(), _row(oid, tid, chid, conv, {"template": "stock_available", "text": "عاد «X» للتوفر!"}),
        now=PNOW, gap_s=8,
    )
    assert decision.send is True
    with core_db.tenant_tx(tid) as conn:
        health = repos_policy.read_number_health(conn, channel_id=chid)
    assert health["sent_today"] == 1  # the slot was reserved (H79)


def test_gate_drops_suppressed(gated_ctx):
    dsn, tid, chid, cid, conv = gated_ctx
    _seed_eligible(dsn, tid, chid, cid, conv)
    db_testsupport.seed_suppression(dsn, tenant_id=tid, customer_id=cid, scope="back_in_stock")
    oid = _seed_notice(dsn, tid, chid, conv)
    decision = policy_gate.gate(
        _settings(), _row(oid, tid, chid, conv, {"template": "stock_available", "text": "x"}),
        now=PNOW, gap_s=8,
    )
    assert decision.send is False
    assert db_testsupport.fetch_outbox_status(dsn, oid) == "dropped_policy"


def test_gate_drops_no_consent(gated_ctx):
    dsn, tid, chid, cid, conv = gated_ctx
    db_testsupport.seed_number_health(
        dsn, tenant_id=tid, channel_id=chid, daily_cap=50, sent_today=0, day="2026-10-03",
    )
    db_testsupport.seed_inbound_message(dsn, tenant_id=tid, conversation_id=conv, body="مرحبا")
    oid = _seed_notice(dsn, tid, chid, conv)
    decision = policy_gate.gate(
        _settings(), _row(oid, tid, chid, conv, {"template": "stock_available", "text": "x"}),
        now=PNOW, gap_s=8,
    )
    assert decision.send is False
    assert db_testsupport.fetch_outbox_status(dsn, oid) == "dropped_policy"


def test_gate_defers_no_health_row(gated_ctx):
    dsn, tid, chid, cid, conv = gated_ctx
    db_testsupport.insert_consent(dsn, tenant_id=tid, customer_id=cid, scope="back_in_stock", granted=True)
    db_testsupport.seed_inbound_message(dsn, tenant_id=tid, conversation_id=conv, body="مرحبا")
    oid = _seed_notice(dsn, tid, chid, conv)
    decision = policy_gate.gate(
        _settings(), _row(oid, tid, chid, conv, {"template": "stock_available", "text": "x"}),
        now=PNOW, gap_s=8,
    )
    assert decision.send is False
    assert db_testsupport.fetch_outbox_status(dsn, oid) == "pending"  # deferred, never dropped

