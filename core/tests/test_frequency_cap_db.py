"""core/tests/test_frequency_cap_db.py - F-P3-19 coverage: per-customer frequency
caps on a real DB (marketing 1/24h & 2/7d, utility 3/24h). The marketing template
is injected into the catalog in THIS TEST ONLY (no marketing template in prod).
"""
from __future__ import annotations

import os
import uuid
from dataclasses import MISSING, fields
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.db import repos_outbox
from app.db import testsupport as db_testsupport
from app.policy.types import TemplateMeta
from app.workers import config, policy_gate
from app.workers.config import WorkerSettings

pytestmark = pytest.mark.db

BASE = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)

_MARKETING = (
    TemplateMeta(
        template_id="marketing_test", message_class="marketing",
        consent_scope="marketing", capability="marketing",
        quiet_hours=False, footer_required=True,
    ),
    "عرض خاص: «title» 🛍️",
    ("title",),
)


def _settings(**kw):
    d: dict = {}
    for f in fields(WorkerSettings):
        if f.name.startswith("send_policy_"):
            if f.default is not MISSING:
                d[f.name] = f.default
            elif f.default_factory is not MISSING:
                d[f.name] = f.default_factory()
    d.update(kw)
    return SimpleNamespace(**d)


def _row(oid, tid, chid, conv, template_id, message_class, wa, now):
    return repos_outbox.OutboxRow(
        id=oid, tenant_id=tid, conversation_id=conv, channel_account_id=chid,
        idempotency_key=f"k-{oid}", origin="automation", message_class=message_class,
        expected_epoch=None, to_wa_id=wa, payload={"template": template_id, "text": "x"},
        attempts=0, created_at=now - timedelta(hours=1),
    )


def test_marketing_24h_cap_one_per_customer(monkeypatch):
    monkeypatch.setitem(config.PROACTIVE_TEMPLATES, "marketing_test", _MARKETING)
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    tid = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"freq-{uuid.uuid4()}", name="Freq Tenant",
    )
    db_testsupport.enable_marketing(dsn, tenant_id=tid)  # P3.4: this TEST tenant is opted in (H100)
    chid = db_testsupport.insert_channel_account(
        dsn, tenant_id=tid, type_="whatsapp_baileys",
        session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
    )
    try:
        cid = db_testsupport.insert_customer(dsn, tenant_id=tid, wa_id="967700000004")
        conv = db_testsupport.seed_conversation(
            dsn, tenant_id=tid, channel_id=chid, customer_id=cid, bot_status="active", epoch=0,
        )
        db_testsupport.seed_number_health(dsn, tenant_id=tid, channel_id=chid, daily_cap=50, sent_today=0)
        db_testsupport.insert_consent(dsn, tenant_id=tid, customer_id=cid, scope="marketing", granted=True)
        db_testsupport.seed_inbound_message(dsn, tenant_id=tid, conversation_id=conv, body="مرحبا",
                                           created_at=BASE - timedelta(days=2))
        oid1 = db_testsupport.seed_outbox_row(
            dsn, tenant_id=tid, channel_id=chid, conversation_id=conv,
            origin="automation", message_class="marketing", to_wa_id="967700000004",
            payload={"template": "marketing_test", "text": "x"},
        )
        oid2 = db_testsupport.seed_outbox_row(
            dsn, tenant_id=tid, channel_id=chid, conversation_id=conv,
            origin="automation", message_class="marketing", to_wa_id="967700000004",
            payload={"template": "marketing_test", "text": "x"},
        )
        d1 = policy_gate.gate(
            _settings(), _row(oid1, tid, chid, conv, "marketing_test", "marketing", "967700000004", BASE),
            now=BASE, gap_s=0,
        )
        d2 = policy_gate.gate(
            _settings(), _row(oid2, tid, chid, conv, "marketing_test", "marketing", "967700000004", BASE),
            now=BASE, gap_s=0,
        )
        assert d1.send is True
        assert d2.send is False
        assert db_testsupport.fetch_outbox_status(dsn, oid2) == "dropped_policy"  # frequency_cap_skip
    finally:
        db_testsupport.delete_tenant_full(dsn, tid)


def test_utility_24h_cap_three_per_customer():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    tid = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"freq-{uuid.uuid4()}", name="Freq Utility Tenant",
    )
    chid = db_testsupport.insert_channel_account(
        dsn, tenant_id=tid, type_="whatsapp_baileys",
        session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
    )
    try:
        cid = db_testsupport.insert_customer(dsn, tenant_id=tid, wa_id="967700000005")
        conv = db_testsupport.seed_conversation(
            dsn, tenant_id=tid, channel_id=chid, customer_id=cid, bot_status="active", epoch=0,
        )
        db_testsupport.seed_number_health(
            dsn, tenant_id=tid, channel_id=chid, daily_cap=50, sent_today=0,
            utility_daily_cap=100, utility_sent_today=0,
        )
        db_testsupport.insert_consent(dsn, tenant_id=tid, customer_id=cid, scope="back_in_stock", granted=True)
        db_testsupport.seed_inbound_message(dsn, tenant_id=tid, conversation_id=conv, body="مرحبا",
                                           created_at=BASE - timedelta(days=2))
        decisions = []
        for i in range(4):
            oid = db_testsupport.seed_outbox_row(
                dsn, tenant_id=tid, channel_id=chid, conversation_id=conv,
                origin="automation", message_class="utility", to_wa_id="967700000005",
                payload={"template": "stock_available", "text": "x"},
            )
            decisions.append(policy_gate.gate(
                _settings(), _row(oid, tid, chid, conv, "stock_available", "utility", "967700000005", BASE),
                now=BASE + timedelta(seconds=i), gap_s=1,
            ).send)
        assert decisions == [True, True, True, False]  # 4th hits frequency_cap_defer
    finally:
        db_testsupport.delete_tenant_full(dsn, tid)
