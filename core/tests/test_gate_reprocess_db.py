"""core/tests/test_gate_reprocess_db.py - F-P3-20: re-processing a reserved row
must not count its OWN ledger reservation in the per-customer frequency caps.

The gate runs decision.decide() BEFORE the H85 ledger check, so a self-counting
cap makes a re-processed row drop itself (marketing: frequency_cap_skip - the
ledger row then stays 'reserved' forever with a slot burned) or defer itself
(utility at cap-1: frequency_cap_defer). Time is injected (H84) and every time
column the gate reads is pinned to that injected clock (F-P3-19 lesson). The
marketing template is injected into the catalog in THIS TEST ONLY - production
stays "wired but dark" (no marketing template is ever registered here).
"""
from __future__ import annotations

import os
import uuid
from dataclasses import MISSING, fields
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import db as core_db
from app.db import repos_outbox
from app.db import repos_policy
from app.db import testsupport as db_testsupport
from app.policy.types import TemplateMeta
from app.workers import config, policy_gate
from app.workers.config import WorkerSettings

pytestmark = pytest.mark.db

PNOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)
WA = "967700000007"

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


def _pin_age_before(target: datetime) -> timedelta:
    """F-P3-19: pin an age column to a FIXED instant (relative to the injected
    PNOW), independent of the real clock: age = real_now - target, so the seeded
    created_at lands exactly on `target`."""
    return datetime.now(timezone.utc) - target


@pytest.fixture()
def reprocess_ctx():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    tid = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"rep-{uuid.uuid4()}", name="Reprocess Tenant",
    )
    db_testsupport.enable_marketing(dsn, tenant_id=tid)  # P3.4: this TEST tenant is opted in (H100)
    chid = db_testsupport.insert_channel_account(
        dsn, tenant_id=tid, type_="whatsapp_baileys",
        session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
    )
    cid = db_testsupport.insert_customer(dsn, tenant_id=tid, wa_id=WA)
    conv = db_testsupport.seed_conversation(
        dsn, tenant_id=tid, channel_id=chid, customer_id=cid, bot_status="active", epoch=0,
    )
    yield dsn, tid, chid, cid, conv
    db_testsupport.delete_tenant_full(dsn, tid)


def _seed_eligible(dsn, tid, chid, cid, conv, *, scope):
    db_testsupport.seed_number_health(
        dsn, tenant_id=tid, channel_id=chid, daily_cap=50, sent_today=0,
        utility_daily_cap=100, utility_sent_today=0, day="2026-10-03",
    )
    db_testsupport.insert_consent(dsn, tenant_id=tid, customer_id=cid, scope=scope, granted=True)
    # created_at pinned to PNOW - 2 days: prior interaction yes, active chat no.
    db_testsupport.seed_inbound_message(
        dsn, tenant_id=tid, conversation_id=conv, body="مرحبا",
        age=_pin_age_before(PNOW - timedelta(days=2)),
    )


def _seed_outbox(dsn, tid, chid, conv, *, message_class, template_id):
    return db_testsupport.seed_outbox_row(
        dsn, tenant_id=tid, channel_id=chid, conversation_id=conv,
        origin="automation", message_class=message_class, to_wa_id=WA,
        payload={"template": template_id, "text": "x"},
    )


def _row(oid, tid, chid, conv, *, message_class, template_id):
    return repos_outbox.OutboxRow(
        id=oid, tenant_id=tid, conversation_id=conv, channel_account_id=chid,
        idempotency_key=f"k-{oid}", origin="automation", message_class=message_class,
        expected_epoch=None, to_wa_id=WA, payload={"template": template_id, "text": "x"},
        attempts=0, created_at=PNOW,
    )


def _seed_handed_off(dsn, tid, chid, cid, conv, *, message_class, template_id):
    """One OTHER outbox row whose ledger row is already handed_off, reserved
    inside the injected 24h window - a prior delivery the cap MUST count."""
    oid = _seed_outbox(dsn, tid, chid, conv, message_class=message_class, template_id=template_id)
    with core_db.tenant_tx(tid) as conn:
        repos_policy.insert_proactive_ledger(
            conn, tenant_id=tid, channel_id=chid, customer_id=cid, outbox_id=oid,
            message_class=message_class, template_id=template_id,
            reserved_at=PNOW - timedelta(hours=1),
        )
        repos_policy.mark_ledger_handed_off(conn, outbox_id=oid)
    return oid


def test_utility_reprocess_same_row_sends_both_times(reprocess_ctx):
    """F-P3-20 (utility): gate the SAME row twice -> both send=True, exactly ONE
    utility slot consumed, the ledger row stays 'reserved' (H85 idempotency)."""
    dsn, tid, chid, cid, conv = reprocess_ctx
    _seed_eligible(dsn, tid, chid, cid, conv, scope="back_in_stock")
    oid = _seed_outbox(dsn, tid, chid, conv, message_class="utility", template_id="stock_available")
    row = _row(oid, tid, chid, conv, message_class="utility", template_id="stock_available")
    d1 = policy_gate.gate(_settings(), row, now=PNOW, gap_s=8)
    d2 = policy_gate.gate(_settings(), row, now=PNOW + timedelta(seconds=60), gap_s=8)
    assert d1.send is True
    assert d2.send is True
    with core_db.tenant_tx(tid) as conn:
        health = repos_policy.read_number_health(conn, channel_id=chid)
        ledger = repos_policy.read_ledger_status(conn, outbox_id=oid)
    assert health["utility_sent_today"] == 1  # exactly one slot, never two
    assert ledger == "reserved"
    assert db_testsupport.fetch_outbox_status(dsn, oid) != "dropped_policy"


def test_marketing_reprocess_same_row_sends_both_times(monkeypatch, reprocess_ctx):
    """F-P3-20 (marketing, per_24h=1): the row's own 'reserved' ledger row must
    not trip the cap. Both calls send=True, ONE marketing slot, ledger stays
    'reserved'. Without the fix the re-process is dropped
    dropped_policy/frequency_cap_skip and the ledger row is stuck forever."""
    monkeypatch.setitem(config.PROACTIVE_TEMPLATES, "marketing_test", _MARKETING)
    dsn, tid, chid, cid, conv = reprocess_ctx
    _seed_eligible(dsn, tid, chid, cid, conv, scope="marketing")
    oid = _seed_outbox(dsn, tid, chid, conv, message_class="marketing", template_id="marketing_test")
    row = _row(oid, tid, chid, conv, message_class="marketing", template_id="marketing_test")
    d1 = policy_gate.gate(_settings(), row, now=PNOW, gap_s=20)
    d2 = policy_gate.gate(_settings(), row, now=PNOW + timedelta(seconds=60), gap_s=20)
    assert d1.send is True
    assert d2.send is True  # without F-P3-20: dropped_policy/frequency_cap_skip (self-count)
    with core_db.tenant_tx(tid) as conn:
        health = repos_policy.read_number_health(conn, channel_id=chid)
        ledger = repos_policy.read_ledger_status(conn, outbox_id=oid)
    assert health["sent_today"] == 1  # the marketing counter, exactly once
    assert ledger == "reserved"
    assert db_testsupport.fetch_outbox_status(dsn, oid) != "dropped_policy"


def test_utility_reprocess_at_cap_minus_one_sends(reprocess_ctx):
    """F-P3-20 (utility, cap-1 of 3): two prior handed_off utility deliveries in
    the 24h window + the reserved row under test. Re-processing it must NOT
    self-count to the cap: send=True, one slot."""
    dsn, tid, chid, cid, conv = reprocess_ctx
    _seed_eligible(dsn, tid, chid, cid, conv, scope="back_in_stock")
    _seed_handed_off(dsn, tid, chid, cid, conv, message_class="utility", template_id="stock_available")
    _seed_handed_off(dsn, tid, chid, cid, conv, message_class="utility", template_id="stock_available")
    oid = _seed_outbox(dsn, tid, chid, conv, message_class="utility", template_id="stock_available")
    row = _row(oid, tid, chid, conv, message_class="utility", template_id="stock_available")
    d1 = policy_gate.gate(_settings(), row, now=PNOW, gap_s=8)
    d2 = policy_gate.gate(_settings(), row, now=PNOW + timedelta(seconds=60), gap_s=8)
    assert d1.send is True  # 2 prior < cap 3 -> reserve
    assert d2.send is True  # without F-P3-20: 2 + own reserved = 3 -> frequency_cap_defer
    with core_db.tenant_tx(tid) as conn:
        health = repos_policy.read_number_health(conn, channel_id=chid)
        ledger = repos_policy.read_ledger_status(conn, outbox_id=oid)
    assert health["utility_sent_today"] == 1
    assert ledger == "reserved"


def test_marketing_other_row_after_cap_still_dropped(monkeypatch, reprocess_ctx):
    """Counter-case: the exclusion is ONLY for the row being gated. A DIFFERENT
    marketing row for the same customer after a handed_off delivery (per_24h=1)
    is still dropped - the cap keeps counting OTHER rows."""
    monkeypatch.setitem(config.PROACTIVE_TEMPLATES, "marketing_test", _MARKETING)
    dsn, tid, chid, cid, conv = reprocess_ctx
    _seed_eligible(dsn, tid, chid, cid, conv, scope="marketing")
    _seed_handed_off(dsn, tid, chid, cid, conv, message_class="marketing", template_id="marketing_test")
    oid = _seed_outbox(dsn, tid, chid, conv, message_class="marketing", template_id="marketing_test")
    row = _row(oid, tid, chid, conv, message_class="marketing", template_id="marketing_test")
    d = policy_gate.gate(_settings(), row, now=PNOW, gap_s=20)
    assert d.send is False
    assert db_testsupport.fetch_outbox_status(dsn, oid) == "dropped_policy"
    assert db_testsupport.fetch_outbox_policy_reason(dsn, oid) == "frequency_cap_skip"
