"""core/tests/test_p3_consent_e2e_db.py - P3.3 Stage D (§3 D / §4.8): the
source x state matrix on the REAL policy_gate.gate(), with a MARKETING
template INJECTED FOR THE TEST ONLY (monkeypatched into PROACTIVE_TEMPLATES -
production stays dark: the injected entry vanishes with the test). Marketing
is eligible ONLY through an explicit customer_message_optin; STOP suppresses
(and cancels the queue for the injected template); a re-opt-in reopens; a
waitlist join never opens marketing; and the same wa_id in another tenant is
untouched (RLS).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app import db as core_db
from app.db import repos_consent
from app.db import repos_outbox
from app.db import repos_policy
from app.db import testsupport as db_testsupport
from app.policy.types import TemplateMeta
from app.workers import config, policy_gate, realtime, schema
from app.workers.config import (
    DEFAULT_OPTIN_AR,
    DEFAULT_OPTIN_EN,
    DEFAULT_OPTOUT_AR,
    DEFAULT_OPTOUT_EN,
)

pytestmark = pytest.mark.db

PNOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)
TID_TEST_MARKETING = "p33_test_marketing_only"

INJECTED_META = TemplateMeta(
    template_id=TID_TEST_MARKETING, message_class="marketing",
    consent_scope="marketing", capability="marketing",
    quiet_hours=True, footer_required=True,
)
INJECTED_TEXT = "عرض خاص لعملائنا الكرام 🎁"


@pytest.fixture()
def e2e(monkeypatch):
    monkeypatch.setitem(
        config.PROACTIVE_TEMPLATES, TID_TEST_MARKETING,
        (INJECTED_META, INJECTED_TEXT, ()),
    )
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    platform_ref = f"p33d-{uuid.uuid4()}"
    tid = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=platform_ref, name="P3.3 E2E Tenant",
    )
    chid = db_testsupport.insert_channel_account(
        dsn, tenant_id=tid, type_="whatsapp_baileys",
        session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
    )
    yield dsn, tid, chid
    db_testsupport.delete_tenant_full(dsn, tid)


def _customer(dsn, tid) -> tuple[uuid.UUID, str]:
    wa = f"9677{uuid.uuid4().int % 10**10:010d}"
    return db_testsupport.insert_customer(dsn, tenant_id=tid, wa_id=wa), wa


def _gate_settings() -> SimpleNamespace:
    return SimpleNamespace(
        send_policy_marketing_ttl_h=24, send_policy_utility_ttl_h=6,
        send_policy_interaction_window_d=180, send_policy_active_chat_cooldown_s=1800,
        send_policy_quiet_start="22:00", send_policy_quiet_end="09:00",
        send_policy_marketing_per_24h=1, send_policy_marketing_per_7d=2,
        send_policy_utility_per_24h=3, send_policy_marketing_gap=(20, 60),
        send_policy_utility_gap=(8, 20),
    )


def _eligible_world(dsn, tid, chid, cid, conv):
    db_testsupport.seed_number_health(
        dsn, tenant_id=tid, channel_id=chid, daily_cap=50, sent_today=0,
    )
    db_testsupport.seed_inbound_message(dsn, tenant_id=tid, conversation_id=conv, body="مرحبا")


def _gate(dsn, tid, chid, conv, wa):
    oid = db_testsupport.seed_outbox_row(
        dsn, tenant_id=tid, channel_id=chid, conversation_id=conv,
        origin="automation", message_class="marketing", to_wa_id=wa,
        payload={"template": TID_TEST_MARKETING, "text": INJECTED_TEXT},
    )
    row = repos_outbox.OutboxRow(
        id=oid, tenant_id=tid, conversation_id=conv, channel_account_id=chid,
        idempotency_key=f"k-{oid}", origin="automation", message_class="marketing",
        expected_epoch=None, to_wa_id=wa,
        payload={"template": TID_TEST_MARKETING, "text": INJECTED_TEXT},
        attempts=0, created_at=PNOW,
    )
    decision = policy_gate.gate(_gate_settings(), row, now=PNOW, gap_s=20)
    return decision, oid


# --- the source x state matrix (§3 D / §4.8) ------------------------------------


def test_matrix_no_consent_drops_no_consent(e2e):
    dsn, tid, chid = e2e
    cid, wa = _customer(dsn, tid)
    conv = db_testsupport.seed_conversation(
        dsn, tenant_id=tid, channel_id=chid, customer_id=cid, bot_status="active", epoch=0,
    )
    _eligible_world(dsn, tid, chid, cid, conv)
    decision, oid = _gate(dsn, tid, chid, conv, wa)
    assert decision.send is False
    assert db_testsupport.fetch_outbox_status(dsn, oid) == "dropped_policy"
    assert db_testsupport.fetch_outbox_policy_reason(dsn, oid) == "no_consent"


@pytest.mark.parametrize("source", ["import", "checkout_optin"])
def test_matrix_import_and_checkout_grants_do_not_open_marketing(e2e, source):
    """H95: those sources are stored but blocked from marketing until the
    owner's legal decision (OQ-P3-14)."""
    dsn, tid, chid = e2e
    cid, wa = _customer(dsn, tid)
    conv = db_testsupport.seed_conversation(
        dsn, tenant_id=tid, channel_id=chid, customer_id=cid, bot_status="active", epoch=0,
    )
    _eligible_world(dsn, tid, chid, cid, conv)
    db_testsupport.insert_consent(
        dsn, tenant_id=tid, customer_id=cid, scope="marketing", granted=True, source=source,
    )
    decision, oid = _gate(dsn, tid, chid, conv, wa)
    assert decision.send is False
    assert db_testsupport.fetch_outbox_policy_reason(dsn, oid) == "no_consent"


def test_matrix_waitlist_join_never_opens_marketing(e2e):
    """Scope separation (§3 D): a back_in_stock consent from a join is real
    for ITS scope and irrelevant to marketing."""
    dsn, tid, chid = e2e
    cid, wa = _customer(dsn, tid)
    conv = db_testsupport.seed_conversation(
        dsn, tenant_id=tid, channel_id=chid, customer_id=cid, bot_status="active", epoch=0,
    )
    _eligible_world(dsn, tid, chid, cid, conv)
    with core_db.tenant_tx(tid) as conn:
        repos_consent.record_waitlist_join(conn, tenant_id=tid, customer_id=cid, entry_id=uuid.uuid4())
    decision, oid = _gate(dsn, tid, chid, conv, wa)
    assert decision.send is False
    assert db_testsupport.fetch_outbox_policy_reason(dsn, oid) == "no_consent"


def test_matrix_explicit_optin_sends_then_stop_suppresses_then_reoptin_sends(e2e):
    dsn, tid, chid = e2e
    cid, wa = _customer(dsn, tid)
    conv = db_testsupport.seed_conversation(
        dsn, tenant_id=tid, channel_id=chid, customer_id=cid, bot_status="active", epoch=0,
    )
    _eligible_world(dsn, tid, chid, cid, conv)

    # «اشتراك» (the capture path itself is proven in Stage B):
    with core_db.tenant_tx(tid) as conn:
        repos_consent.record_optin(conn, tenant_id=tid, customer_id=cid, message_id=uuid.uuid4())
    decision, oid = _gate(dsn, tid, chid, conv, wa)
    assert decision.send is True
    # F-P3-30.3: the gate RESERVES (H85) - "sending" is the dispatcher's job,
    # so the outbox row itself stays pending.
    with core_db.tenant_tx(tid) as conn:
        ledger = repos_policy.read_ledger_status(conn, outbox_id=oid)
    assert ledger == "reserved"
    assert db_testsupport.fetch_outbox_status(dsn, oid) == "pending"

    # «إيقاف» through the REAL ingest commit: suppressed at the gate AND the
    # pending queue for the injected marketing template is cancelled.
    worker = realtime.RealtimeWorker(SimpleNamespace(
        core_optout_phrases_ar=DEFAULT_OPTOUT_AR, core_optout_phrases_en=DEFAULT_OPTOUT_EN,
        core_optin_phrases_ar=DEFAULT_OPTIN_AR, core_optin_phrases_en=DEFAULT_OPTIN_EN,
        core_ingest_max_body_chars=65536,
    ))
    qoid = db_testsupport.seed_outbox_row(
        dsn, tenant_id=tid, channel_id=chid, conversation_id=conv,
        origin="automation", message_class="marketing", to_wa_id=wa,
        payload={"template": TID_TEST_MARKETING, "text": INJECTED_TEXT},
    )
    result = worker._commit(
        schema.WalEntry(
            session_id=f"s-{uuid.uuid4()}", provider_message_id=f"pmid-{uuid.uuid4()}",
            type="text", identity=schema.WalIdentity(wa_id=wa), text="إيقاف",
        ),
        schema.SessionResolution(channel_account_id=chid, tenant_id=tid, engine="ai_core"),
    )
    assert result.outcome == "committed"
    assert db_testsupport.fetch_outbox_status(dsn, qoid) == "dropped_policy"

    decision2, oid2 = _gate(dsn, tid, chid, conv, wa)
    assert decision2.send is False
    assert db_testsupport.fetch_outbox_policy_reason(dsn, oid2) == "suppressed"

    # «اشتراك» again: the exact optout-caused block is lifted => sent.
    with core_db.tenant_tx(tid) as conn:
        repos_consent.record_optin(conn, tenant_id=tid, customer_id=cid, message_id=uuid.uuid4())
    decision3, oid3 = _gate(dsn, tid, chid, conv, wa)
    assert decision3.send is True
    with core_db.tenant_tx(tid) as conn:
        ledger3 = repos_policy.read_ledger_status(conn, outbox_id=oid3)
    assert ledger3 == "reserved"  # H85
    assert db_testsupport.fetch_outbox_status(dsn, oid3) == "pending"


def test_matrix_rls_same_wa_id_other_tenant_unaffected(e2e):
    """The same wa_id in ANOTHER tenant is a different customer (RLS): a STOP
    in tenant A never touches tenant B's eligibility."""
    dsn, tid, chid = e2e
    cid_a, wa = _customer(dsn, tid)
    other = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"p33d-b-{uuid.uuid4()}", name="P3.3 E2E Tenant B",
    )
    try:
        chid_b = db_testsupport.insert_channel_account(
            dsn, tenant_id=other, type_="whatsapp_baileys",
            session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
        )
        cid_b = db_testsupport.insert_customer(dsn, tenant_id=other, wa_id=wa)
        conv_b = db_testsupport.seed_conversation(
            dsn, tenant_id=other, channel_id=chid_b, customer_id=cid_b,
            bot_status="active", epoch=0,
        )
        with core_db.tenant_tx(tid) as conn:
            repos_consent.record_optout(conn, tenant_id=tid, customer_id=cid_a, message_id=uuid.uuid4())
        with core_db.tenant_tx(other) as conn:
            assert repos_outbox.has_suppression(conn, other, cid_b, "marketing") is False
        with core_db.tenant_tx(other) as conn:
            repos_consent.record_optin(conn, tenant_id=other, customer_id=cid_b, message_id=uuid.uuid4())
        _eligible_world(dsn, other, chid_b, cid_b, conv_b)
        decision, _oid = _gate(dsn, other, chid_b, conv_b, wa)
        assert decision.send is True
    finally:
        db_testsupport.delete_tenant_full(dsn, other)
