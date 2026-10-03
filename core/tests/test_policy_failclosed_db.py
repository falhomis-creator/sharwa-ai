"""core/tests/test_policy_failclosed_db.py - F-P3-19 coverage: H80 fail-closed.

An exception injected inside the gate defers the row (policy_reason='policy_error'),
sends nothing, and bumps policy_errors_total.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import MISSING, fields
from types import SimpleNamespace

import pytest

from app.db import testsupport as db_testsupport
from app.obs import metrics
from app.workers import dispatch, policy_gate
from app.workers.config import WorkerSettings

pytestmark = pytest.mark.db


def _settings(**kw):
    d: dict = {}
    for f in fields(WorkerSettings):
        if f.name.startswith("send_policy_"):
            if f.default is not MISSING:
                d[f.name] = f.default
            elif f.default_factory is not MISSING:
                d[f.name] = f.default_factory()
    d.update(core_dispatch_batch=20, core_dispatch_lease_s=60, core_dispatch_max_attempts=8)
    d.update(kw)
    return SimpleNamespace(**d)


class _FakeClient:
    def __init__(self):
        self.sent = []

    def send(self, **kw):
        self.sent.append(kw)
        return SimpleNamespace(status_code=202)


def test_gate_exception_defers_and_does_not_send(monkeypatch):
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    tid = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"fail-{uuid.uuid4()}", name="FailClosed Tenant",
    )
    chid = db_testsupport.insert_channel_account(
        dsn, tenant_id=tid, type_="whatsapp_baileys",
        session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
    )
    try:
        cid = db_testsupport.insert_customer(dsn, tenant_id=tid, wa_id="967700000003")
        conv = db_testsupport.seed_conversation(
            dsn, tenant_id=tid, channel_id=chid, customer_id=cid, bot_status="active", epoch=0,
        )
        db_testsupport.seed_number_health(dsn, tenant_id=tid, channel_id=chid, daily_cap=50, sent_today=0)
        db_testsupport.insert_consent(dsn, tenant_id=tid, customer_id=cid, scope="back_in_stock", granted=True)
        db_testsupport.seed_inbound_message(dsn, tenant_id=tid, conversation_id=conv, body="مرحبا")
        oid = db_testsupport.seed_outbox_row(
            dsn, tenant_id=tid, channel_id=chid, conversation_id=conv,
            origin="automation", message_class="utility", to_wa_id="967700000003",
            payload={"template": "stock_available", "text": "عاد «X» للتوفر!"},
        )

        before = metrics.policy_errors_total.labels("gate")._value.get()
        def _boom(_inp):
            raise RuntimeError("injected gate failure")
        monkeypatch.setattr(policy_gate.decision, "decide", _boom)

        client = _FakeClient()
        dispatch.dispatch_cycle(_settings(), client)

        assert db_testsupport.fetch_outbox_status(dsn, oid) == "pending"
        assert db_testsupport.fetch_outbox_policy_reason(dsn, oid) == "policy_error"
        assert client.sent == []
        assert metrics.policy_errors_total.labels("gate")._value.get() > before
    finally:
        db_testsupport.delete_tenant_full(dsn, tid)
