"""core/tests/test_dispatch_burst_db.py - F-P3-16: a policy defer must NOT consume
a send attempt. Probed through the FULL dispatch_cycle (not gate() alone), with a
faked gateway and time fast-forwarded between cycles (no sleep).
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


def _settings(**kw):
    d: dict = {}
    for f in fields(WorkerSettings):
        if f.name.startswith("send_policy_"):
            if f.default is not MISSING:
                d[f.name] = f.default
            elif f.default_factory is not MISSING:
                d[f.name] = f.default_factory()
    d.update(core_dispatch_batch=200, core_dispatch_lease_s=60, core_dispatch_max_attempts=8)
    d.update(kw)
    return SimpleNamespace(**d)


class _FakeClient:
    def __init__(self):
        self.sent: list[str] = []

    def send(self, *, session_id, to, text, client_msg_id, kind):
        self.sent.append(to)
        return SimpleNamespace(status_code=202)


def _seed_burst(dsn, tid, chid, n, utility_cap):
    oids = []
    for i in range(n):
        cid = db_testsupport.insert_customer(dsn, tenant_id=tid, wa_id=f"9677{i:07d}")
        conv = db_testsupport.seed_conversation(
            dsn, tenant_id=tid, channel_id=chid, customer_id=cid, bot_status="active", epoch=0,
        )
        db_testsupport.insert_consent(dsn, tenant_id=tid, customer_id=cid, scope="back_in_stock", granted=True)
        db_testsupport.seed_inbound_message(dsn, tenant_id=tid, conversation_id=conv, body="مرحبا")
        oid = db_testsupport.seed_outbox_row(
            dsn, tenant_id=tid, channel_id=chid, conversation_id=conv,
            origin="automation", message_class="utility", to_wa_id=f"9677{i:07d}",
            payload={"template": "stock_available", "text": "عاد «X» للتوفر!"},
        )
        oids.append(oid)
    db_testsupport.seed_number_health(
        dsn, tenant_id=tid, channel_id=chid,
        daily_cap=50, sent_today=0, utility_daily_cap=utility_cap, utility_sent_today=0,
    )
    return oids


def _drain(dsn, tid, chid, oids, client):
    for _ in range(len(oids) + 10):
        dispatch.dispatch_cycle(_settings(), client)
        db_testsupport.fast_forward_outbox(dsn, tenant_id=tid)
        db_testsupport.fast_forward_number_health(dsn, channel_id=chid)
    return [db_testsupport.fetch_outbox_status(dsn, o) for o in oids]


def _assert_all_sent_and_none_failed(statuses):
    assert statuses.count("failed") == 0, statuses
    assert statuses.count("sent") == len(statuses), statuses


@pytest.fixture()
def burst_ctx():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    tid = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"burst-{uuid.uuid4()}", name="Burst Tenant",
    )
    chid = db_testsupport.insert_channel_account(
        dsn, tenant_id=tid, type_="whatsapp_baileys",
        session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
    )
    yield dsn, tid, chid
    db_testsupport.delete_tenant_full(dsn, tid)


@pytest.mark.parametrize("n", [5, 12, 30])
def test_burst_no_failures_below_cap(burst_ctx, n):
    dsn, tid, chid = burst_ctx
    oids = _seed_burst(dsn, tid, chid, n, utility_cap=100)
    client = _FakeClient()
    statuses = _drain(dsn, tid, chid, oids, client)
    _assert_all_sent_and_none_failed(statuses)
    # sent in claim order (oldest next_attempt_at first).
    assert client.sent == [f"9677{i:07d}" for i in range(n)]


def test_burst_above_cap_defers_never_fails(burst_ctx):
    dsn, tid, chid = burst_ctx
    oids = _seed_burst(dsn, tid, chid, 130, utility_cap=100)
    client = _FakeClient()
    statuses = _drain(dsn, tid, chid, oids, client)
    assert statuses.count("failed") == 0, statuses
    assert statuses.count("sent") == 100, statuses
    assert statuses.count("pending") == 30, statuses  # deferred by cap, not failed
