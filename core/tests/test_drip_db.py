"""core/tests/test_drip_db.py - G: the comprehensive drip test (H79/H85).

60 consented customers, one experimental marketing template (injected into the
catalog in THIS TEST ONLY - there is no marketing template in production:
"wired but dark"). No sleep - time is injected by advancing `now` by the gap for
each row. Asserts delivered <= the daily cap, and that reprocessing a reserved
row takes no second slot (H85).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import db as core_db
from app.db import repos_outbox
from app.db import repos_policy
from app.db import testsupport as db_testsupport
from app.policy.types import TemplateMeta
from app.workers import config, policy_gate

pytestmark = pytest.mark.db

BASE = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)
GAP_MIN = 20
CAP = 7
N = 60

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
    d = dict(
        send_policy_marketing_ttl_h=24, send_policy_utility_ttl_h=6,
        send_policy_interaction_window_d=180, send_policy_active_chat_cooldown_s=1800,
        send_policy_quiet_start="22:00", send_policy_quiet_end="09:00",
        send_policy_marketing_per_24h=1, send_policy_marketing_per_7d=2,
        send_policy_utility_per_24h=3,
        send_policy_marketing_gap=(GAP_MIN, 60), send_policy_utility_gap=(8, 20),
        marketing_footer_ar="لإيقاف الرسائل الترويجية أرسل: إيقاف",
        verify_max_chars=4000,
    )
    d.update(kw)
    return SimpleNamespace(**d)


def test_drip_respects_cap_and_reprocess_is_idempotent(monkeypatch):
    monkeypatch.setitem(config.PROACTIVE_TEMPLATES, "marketing_test", _MARKETING)

    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    tid = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"drip-{uuid.uuid4()}", name="Drip Tenant",
    )
    chid = db_testsupport.insert_channel_account(
        dsn, tenant_id=tid, type_="whatsapp_baileys",
        session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
    )
    db_testsupport.seed_number_health(
        dsn, tenant_id=tid, channel_id=chid, daily_cap=CAP, sent_today=0, day="2026-10-03",
    )
    try:
        rows = []
        for i in range(N):
            cid = db_testsupport.insert_customer(dsn, tenant_id=tid, wa_id=f"9677{i:07d}")
            conv = db_testsupport.seed_conversation(
                dsn, tenant_id=tid, channel_id=chid, customer_id=cid, bot_status="active", epoch=0,
            )
            db_testsupport.insert_consent(dsn, tenant_id=tid, customer_id=cid, scope="marketing", granted=True)
            db_testsupport.seed_inbound_message(dsn, tenant_id=tid, conversation_id=conv, body="مرحبا")
            oid = db_testsupport.seed_outbox_row(
                dsn, tenant_id=tid, channel_id=chid, conversation_id=conv,
                origin="automation", message_class="marketing", to_wa_id=f"9677{i:07d}",
                payload={"template": "marketing_test", "text": "عرض خاص: «X» 🛍️"},
            )
            rows.append((oid, cid, conv))

        reserved = 0
        for i, (oid, cid, conv) in enumerate(rows):
            now = BASE + timedelta(seconds=i * GAP_MIN)
            decision = policy_gate.gate(
                _settings(),
                repos_outbox.OutboxRow(
                    id=oid, tenant_id=tid, conversation_id=conv, channel_account_id=chid,
                    idempotency_key=f"k-{oid}", origin="automation", message_class="marketing",
                    expected_epoch=None, to_wa_id=f"9677{i:07d}",
                    payload={"template": "marketing_test", "text": "عرض خاص: «X» 🛍️"},
                    attempts=0, created_at=BASE - timedelta(hours=1),
                ),
                now=now, gap_s=GAP_MIN,
            )
            if decision.send:
                reserved += 1

        assert reserved == CAP  # the number cap, not the 60 customers, binds (H79)

        # H85: reprocessing an already-reserved row takes no second slot.
        with core_db.tenant_tx(tid) as conn:
            before = repos_policy.read_number_health(conn, channel_id=chid)["sent_today"]
        oid, cid, conv = rows[0]
        decision = policy_gate.gate(
            _settings(),
            repos_outbox.OutboxRow(
                id=oid, tenant_id=tid, conversation_id=conv, channel_account_id=chid,
                idempotency_key=f"k-{oid}", origin="automation", message_class="marketing",
                expected_epoch=None, to_wa_id="9677000000",
                payload={"template": "marketing_test", "text": "عرض خاص: «X» 🛍️"},
                attempts=0, created_at=BASE - timedelta(hours=1),
            ),
            now=BASE + timedelta(seconds=N * GAP_MIN), gap_s=GAP_MIN,
        )
        assert decision.send is True
        with core_db.tenant_tx(tid) as conn:
            after = repos_policy.read_number_health(conn, channel_id=chid)["sent_today"]
        assert after == before  # no second slot consumed
    finally:
        db_testsupport.delete_tenant_full(dsn, tid)
