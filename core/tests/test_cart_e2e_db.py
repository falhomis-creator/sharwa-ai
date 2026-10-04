"""core/tests/test_cart_e2e_db.py - P3.2 Stage G: the FULL chain, end to end,
with a faked gateway and NO sleep (time is moved by writing run_at /
last_activity_at, and the webhook's occurred_at is anchored 30h in the past so
the scheduled reminder is immediately due):

  signed HTTP cart.updated -> carts row + scheduled job -> run_due ->
  (injected template + marketing consent + prior interaction + number_health)
  -> ONE outbox row automation/marketing -> dispatch_cycle -> policy gate ->
  SENT.

Two dark variants: WITHOUT consent -> dropped_policy/no_consent (the gate
speaks), and WITHOUT the injected template -> NO outbox row at all (the engine
speaks: cancelled/template_not_registered - §5.11's official e2e dark proof).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import MISSING, fields
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.db import testsupport as db_testsupport
from app.policy.types import TemplateMeta
from app.workers import config
from app.workers import dispatch as dispatch_mod
from app.workers import scheduler as engine

pytestmark = pytest.mark.db

NOW = datetime.now(timezone.utc)
OCCURRED = NOW - timedelta(hours=30)  # reminder due immediately

_CART_TEMPLATE = (
    TemplateMeta(
        template_id="cart_reminder", message_class="marketing",
        consent_scope="marketing", capability="marketing",
        quiet_hours=False, footer_required=True,
    ),
    "تذكير: سلتك فيها «title» (عدد «item_count») 🛒",
    ("title", "item_count"),
)


def _worker_settings_cls():
    from app.workers.config import WorkerSettings
    return WorkerSettings


def _settings(**kw):
    d: dict = {}
    for f in fields(_worker_settings_cls()):
        if f.name.startswith(("scheduler_", "cart_", "carts_", "send_policy_", "verify_")) \
                or f.name == "marketing_footer_ar":
            if f.default is not MISSING:
                d[f.name] = f.default
            elif f.default_factory is not MISSING:
                d[f.name] = f.default_factory()
    d.update(core_dispatch_batch=200, core_dispatch_lease_s=60, core_dispatch_max_attempts=8)
    d.update(kw)
    return SimpleNamespace(**d)


class _FakeGateway:
    def __init__(self):
        self.sent: list[str] = []

    def send(self, *, session_id, to, text, client_msg_id, kind):
        self.sent.append(to)
        return SimpleNamespace(status_code=202)


@pytest.fixture()
def e2e_client(monkeypatch):
    import app.main as main_module
    monkeypatch.setattr(main_module.core_db, "init_pool", lambda *a, **kw: None)
    monkeypatch.setattr(main_module.core_db, "close_pool", lambda: None)
    from app.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def e2e_tenant():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    platform_ref = f"e2e-{uuid.uuid4()}"
    tid = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=platform_ref, name="E2E Tenant",
    )
    chid = db_testsupport.insert_channel_account(
        dsn, tenant_id=tid, type_="whatsapp_baileys",
        session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
    )
    cid = db_testsupport.insert_customer(dsn, tenant_id=tid, wa_id="967735000001")
    conv = db_testsupport.seed_conversation(
        dsn, tenant_id=tid, channel_id=chid, customer_id=cid, bot_status="active", epoch=0,
    )
    yield dsn, platform_ref, tid, chid, cid, conv
    db_testsupport.delete_tenant_full(dsn, tid)


def _post_cart_updated(c, platform_ref: str, cart_id: str, wa_id: str):
    body = json.dumps({
        "tenant_ref": platform_ref,
        "events": [{
            "type": "cart.updated", "cart_id": cart_id,
            "occurred_at": OCCURRED.isoformat(),
            "customer": {"wa_id": wa_id},
            "item_count": 2, "total_minor": 5000, "currency": "SAR",
            "items": [{"title": "قميص"}, {"title": "بنطال"}],
        }],
    }).encode()
    ts = str(int(time.time()))
    sig = hmac.new(os.environ["PLATFORM_WEBHOOK_SECRET"].encode(),
                   f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return c.post("/webhooks/platform/cart", content=body,
                  headers={"X-Platform-Signature": sig, "X-Platform-Timestamp": ts})


def test_full_chain_sent(e2e_client, e2e_tenant, monkeypatch):
    """cart.updated (signed HTTP) -> job -> run_due (injected template +
    consent + prior interaction + health) -> outbox automation/marketing ->
    dispatch_cycle -> gate -> SENT."""
    monkeypatch.setitem(config.PROACTIVE_TEMPLATES, "cart_reminder", _CART_TEMPLATE)
    c = e2e_client
    dsn, platform_ref, tid, chid, cid, conv = e2e_tenant
    db_testsupport.insert_consent(
        dsn, tenant_id=tid, customer_id=cid, scope="marketing", granted=True,
    )
    db_testsupport.seed_inbound_message(dsn, tenant_id=tid, conversation_id=conv, body="مرحبا")
    db_testsupport.seed_number_health(
        dsn, tenant_id=tid, channel_id=chid, daily_cap=50, sent_today=0, day="1970-01-01",
    )

    resp = _post_cart_updated(c, platform_ref, "CART-E2E", "967735000001")
    assert resp.status_code == 200 and resp.json()["counts"] == {"applied": 1}
    job = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "cart:CART-E2E:stage1")
    assert job is not None and job["status"] == "pending"

    engine.run_due(_settings(), now=NOW)  # occurred 30h ago => due immediately
    job = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "cart:CART-E2E:stage1")
    assert job["status"] == "done", job
    row = db_testsupport.fetch_outbox_row_by_idempotency(dsn, tid, "cart:CART-E2E:stage1")
    assert row is not None and row["message_class"] == "marketing" and row["origin"] == "automation"
    assert db_testsupport.fetch_cart(dsn, tid, "CART-E2E")["status"] == "reminded"

    client = _FakeGateway()
    for _ in range(3):
        dispatch_mod.dispatch_cycle(_settings(), client)
        db_testsupport.fast_forward_outbox(dsn, tenant_id=tid)
        db_testsupport.fast_forward_number_health(dsn, channel_id=chid)
    row = db_testsupport.fetch_outbox_row_by_idempotency(dsn, tid, "cart:CART-E2E:stage1")
    assert row["status"] == "sent", row
    assert client.sent == ["967735000001"]


def test_full_chain_without_consent_drops_at_the_gate(e2e_client, e2e_tenant, monkeypatch):
    """No marketing consent: the ENGINE still enqueues (the template is
    injected), but the GATE drops the row - dropped_policy/no_consent. Two
    independent verdicts, exactly as designed (H76)."""
    monkeypatch.setitem(config.PROACTIVE_TEMPLATES, "cart_reminder", _CART_TEMPLATE)
    c = e2e_client
    dsn, platform_ref, tid, chid, cid, conv = e2e_tenant
    db_testsupport.seed_inbound_message(dsn, tenant_id=tid, conversation_id=conv, body="مرحبا")
    db_testsupport.seed_number_health(
        dsn, tenant_id=tid, channel_id=chid, daily_cap=50, sent_today=0, day="1970-01-01",
    )

    resp = _post_cart_updated(c, platform_ref, "CART-E2E-NC", "967735000001")
    assert resp.status_code == 200
    engine.run_due(_settings(), now=NOW)
    row = db_testsupport.fetch_outbox_row_by_idempotency(dsn, tid, "cart:CART-E2E-NC:stage1")
    assert row is not None, "the engine enqueued; the gate decides at send time"

    client = _FakeGateway()
    for _ in range(3):
        dispatch_mod.dispatch_cycle(_settings(), client)
        db_testsupport.fast_forward_outbox(dsn, tenant_id=tid)
    row = db_testsupport.fetch_outbox_row_by_idempotency(dsn, tid, "cart:CART-E2E-NC:stage1")
    assert row["status"] == "dropped_policy"
    assert db_testsupport.fetch_outbox_policy_reason(dsn, row["id"]) == "no_consent"
    assert client.sent == []


def test_full_chain_dark_no_template_no_outbox_at_all(e2e_client, e2e_tenant):
    """§5.11's official e2e dark proof: with the PRODUCTION catalog (no
    cart_reminder template) the chain stops at the engine - cancelled/
    template_not_registered and ZERO outbox rows, consent or not."""
    assert "cart_reminder" not in config.PROACTIVE_TEMPLATES
    c = e2e_client
    dsn, platform_ref, tid, chid, cid, conv = e2e_tenant
    db_testsupport.insert_consent(
        dsn, tenant_id=tid, customer_id=cid, scope="marketing", granted=True,
    )
    db_testsupport.seed_inbound_message(dsn, tenant_id=tid, conversation_id=conv, body="مرحبا")

    resp = _post_cart_updated(c, platform_ref, "CART-E2E-DARK", "967735000001")
    assert resp.status_code == 200
    engine.run_due(_settings(), now=NOW)
    job = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "cart:CART-E2E-DARK:stage1")
    assert job["status"] == "cancelled"
    assert job["cancel_reason"] == "template_not_registered"
    assert db_testsupport.count_outbox_rows(dsn, tenant_id=tid) == 0

    client = _FakeGateway()
    for _ in range(2):
        dispatch_mod.dispatch_cycle(_settings(), client)
    assert client.sent == []

