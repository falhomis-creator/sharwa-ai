"""core/tests/test_dispatch_db.py - F-P1-12: `dispatch_cycle` sends a real row of
every origin against a REAL database with REAL roles. This is the wire test whose
absence let the three dispatcher wiring defects (outbox_stats grant, claim_outbox
signature, by-position columns) ship un-noticed for two phases.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import MISSING, fields
from types import SimpleNamespace

import pytest

from app.db import testsupport as db_testsupport
from app.workers import dispatch
from app.workers.config import WorkerSettings

pytestmark = pytest.mark.db


def _send_policy_defaults() -> dict:
    """F-P3-19: the automation path runs through the policy gate, which reads every
    `send_policy_*` field - derive the defaults from the REAL WorkerSettings, not a
    thin hand-written stub that drifts."""
    out: dict = {}
    for f in fields(WorkerSettings):
        if not f.name.startswith("send_policy_"):
            continue
        if f.default is not MISSING:
            out[f.name] = f.default
        elif f.default_factory is not MISSING:  # pragma: no cover - warm-up ladder
            out[f.name] = f.default_factory()
    return out


def _settings(**kw) -> SimpleNamespace:
    d = dict(core_dispatch_batch=20, core_dispatch_lease_s=60, core_dispatch_max_attempts=8)
    d.update(_send_policy_defaults())
    d.update(kw)
    return SimpleNamespace(**d)


class _FakeClient:
    """A gateway client with a scripted status code; records every send."""

    def __init__(self, status_code: int = 202) -> None:
        self.status_code = status_code
        self.sent: list[tuple[str, str, str, str, str]] = []

    def send(self, *, session_id, to, text, client_msg_id, kind):
        self.sent.append((session_id, to, text, client_msg_id, kind))
        return SimpleNamespace(status_code=self.status_code)


@pytest.fixture()
def tenant_ctx():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    tenant_id = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"dispatch-{uuid.uuid4()}", name="Dispatch Tenant",
    )
    channel_id = db_testsupport.insert_channel_account(
        dsn, tenant_id=tenant_id, type_="whatsapp_baileys",
        session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
    )
    customer_id = db_testsupport.insert_customer(dsn, tenant_id=tenant_id, wa_id="967700000001")
    yield dsn, tenant_id, channel_id, customer_id
    db_testsupport.delete_tenant_full(dsn, tenant_id)


def test_human_service_sent(tenant_ctx):
    dsn, tid, chid, cid = tenant_ctx
    conv = db_testsupport.seed_conversation(dsn, tenant_id=tid, channel_id=chid, customer_id=cid)
    oid = db_testsupport.seed_outbox_row(
        dsn, tenant_id=tid, channel_id=chid, conversation_id=conv,
        origin="human", message_class="service", to_wa_id="967700000001", payload={"text": "hi"},
    )
    client = _FakeClient(202)
    dispatch.dispatch_cycle(_settings(), client)
    assert db_testsupport.fetch_outbox_status(dsn, oid) == "sent"
    assert len(client.sent) == 1


def test_bot_service_paused_human_matching_epoch_sent(tenant_ctx):
    # F-P1-09 on a real database: a paused_human conversation with a MATCHING
    # epoch still delivers its handoff notice.
    dsn, tid, chid, cid = tenant_ctx
    conv = db_testsupport.seed_conversation(
        dsn, tenant_id=tid, channel_id=chid, customer_id=cid, bot_status="paused_human", epoch=3,
    )
    oid = db_testsupport.seed_outbox_row(
        dsn, tenant_id=tid, channel_id=chid, conversation_id=conv,
        origin="bot", message_class="service", to_wa_id="967700000001",
        expected_epoch=3, payload={"text": "handoff"},
    )
    dispatch.dispatch_cycle(_settings(), _FakeClient(202))
    assert db_testsupport.fetch_outbox_status(dsn, oid) == "sent"


def test_bot_stale_epoch_dropped(tenant_ctx):
    dsn, tid, chid, cid = tenant_ctx
    conv = db_testsupport.seed_conversation(
        dsn, tenant_id=tid, channel_id=chid, customer_id=cid, bot_status="active", epoch=5,
    )
    oid = db_testsupport.seed_outbox_row(
        dsn, tenant_id=tid, channel_id=chid, conversation_id=conv,
        origin="bot", message_class="service", to_wa_id="967700000001",
        expected_epoch=2, payload={"text": "stale"},
    )
    dispatch.dispatch_cycle(_settings(), _FakeClient(202))
    assert db_testsupport.fetch_outbox_status(dsn, oid) == "dropped_stale"


def test_marketing_suppressed_dropped_policy(tenant_ctx):
    dsn, tid, chid, cid = tenant_ctx
    conv = db_testsupport.seed_conversation(dsn, tenant_id=tid, channel_id=chid, customer_id=cid)
    db_testsupport.seed_suppression(dsn, tenant_id=tid, customer_id=cid, scope="marketing")
    oid = db_testsupport.seed_outbox_row(
        dsn, tenant_id=tid, channel_id=chid, conversation_id=conv,
        origin="automation", message_class="marketing", to_wa_id="967700000001", payload={"text": "sale"},
    )
    dispatch.dispatch_cycle(_settings(), _FakeClient(202))
    assert db_testsupport.fetch_outbox_status(dsn, oid) == "dropped_policy"


def test_automation_failure_does_not_pause_conversation(tenant_ctx):
    # F-P3-07: an automation row past the attempts cap is failed, but a customer
    # who is chatting right now must NOT be paused.
    dsn, tid, chid, cid = tenant_ctx
    conv = db_testsupport.seed_conversation(
        dsn, tenant_id=tid, channel_id=chid, customer_id=cid, bot_status="active", epoch=1,
    )
    oid = db_testsupport.seed_outbox_row(
        dsn, tenant_id=tid, channel_id=chid, conversation_id=conv,
        origin="automation", message_class="utility", to_wa_id="967700000001", payload={"text": "notice"},
    )
    dispatch.dispatch_cycle(_settings(core_dispatch_max_attempts=0), _FakeClient(202))
    assert db_testsupport.fetch_outbox_status(dsn, oid) == "failed"
    # The conversation must still be active (not paused by an automation failure).
    assert db_testsupport.fetch_conversation_bot_status(dsn, conv) == "active"


def test_dispatch_sends_real_row_of_every_origin(tenant_ctx):
    # F-P1-12 acceptance (P3.1 §1.2-4): dispatch_cycle sends a REAL row of each
    # origin (bot / human / automation) - status='sent' for each. The gateway
    # client is faked; the database is real. The automation row goes through the
    # policy gate, so it needs a template + consent + prior interaction + health.
    dsn, tid, chid, cid = tenant_ctx
    conv = db_testsupport.seed_conversation(
        dsn, tenant_id=tid, channel_id=chid, customer_id=cid, bot_status="active", epoch=0,
    )
    db_testsupport.seed_number_health(dsn, tenant_id=tid, channel_id=chid, daily_cap=50, sent_today=0)
    db_testsupport.insert_consent(dsn, tenant_id=tid, customer_id=cid, scope="back_in_stock", granted=True)
    db_testsupport.seed_inbound_message(dsn, tenant_id=tid, conversation_id=conv, body="مرحبا")
    rows = {
        "bot": db_testsupport.seed_outbox_row(
            dsn, tenant_id=tid, channel_id=chid, conversation_id=conv,
            origin="bot", message_class="service", to_wa_id="967700000001",
            expected_epoch=0, payload={"text": "bot reply"},
        ),
        "human": db_testsupport.seed_outbox_row(
            dsn, tenant_id=tid, channel_id=chid, conversation_id=conv,
            origin="human", message_class="service", to_wa_id="967700000001", payload={"text": "staff reply"},
        ),
        "automation": db_testsupport.seed_outbox_row(
            dsn, tenant_id=tid, channel_id=chid, conversation_id=conv,
            origin="automation", message_class="utility", to_wa_id="967700000001",
            payload={"template": "stock_available", "text": "عاد «X» للتوفر!"},
        ),
    }
    dispatch.dispatch_cycle(_settings(), _FakeClient(202))
    for origin, oid in rows.items():
        assert db_testsupport.fetch_outbox_status(dsn, oid) == "sent", f"{origin} row not sent"
