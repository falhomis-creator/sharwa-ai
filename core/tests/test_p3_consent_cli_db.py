"""core/tests/test_p3_consent_cli_db.py - P3.3 Stage C: the waitlist rejoin
(OQ-P3-12: a STOPped customer who joins the waitlist gets the availability
notice THROUGH THE REAL GATE again) and the consent-history CLI (§4.10: the
output carries identifiers only - no phone, no message text).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import cli
from app import db as core_db
from app.db import repos_consent
from app.db import repos_outbox
from app.db import testsupport as db_testsupport
from app.workers import policy_gate, stock

pytestmark = pytest.mark.db

PNOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def c_ctx():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    platform_ref = f"p33c-{uuid.uuid4()}"
    tid = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=platform_ref, name="P3.3 Stage C Tenant",
    )
    chid = db_testsupport.insert_channel_account(
        dsn, tenant_id=tid, type_="whatsapp_baileys",
        session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
    )
    yield dsn, tid, chid, platform_ref
    db_testsupport.delete_tenant_full(dsn, tid)


# --- §4.9: STOP -> waitlist join -> the notice crosses the gate again ----------


def test_rejoin_after_stop_restores_the_availability_notice(c_ctx):
    dsn, tid, chid, _ref = c_ctx
    wa = f"9677{uuid.uuid4().int % 10**10:010d}"
    cid = db_testsupport.insert_customer(dsn, tenant_id=tid, wa_id=wa)
    conv = db_testsupport.seed_conversation(
        dsn, tenant_id=tid, channel_id=chid, customer_id=cid, bot_status="active", epoch=0,
    )
    # The customer STOPped (the exact ingest-path write).
    with core_db.tenant_tx(tid) as conn:
        repos_consent.record_optout(conn, tenant_id=tid, customer_id=cid, message_id=uuid.uuid4())
    with core_db.tenant_tx(tid) as conn:
        assert repos_outbox.has_suppression(conn, tid, cid, "back_in_stock") is True

    # ...then explicitly joins the waitlist through the REAL coordinator: the
    # conversation carries the last-shown product slot the pure tool reads.
    db_testsupport.exec_sql_autocommit(
        dsn,
        "UPDATE conversations SET slots = slots || "
        "jsonb_build_object('last_shown_product_ids', jsonb_build_array('VAR-REJOIN')) "
        "WHERE id = '%s'" % conv,
    )
    settings = SimpleNamespace(stock_max_waitlist_per_customer=10)
    with core_db.tenant_tx(tid) as conn:
        decision = stock.join_waitlist(
            conn, settings, tenant_id=tid, conversation_id=conv, bodies=("سجّلني",),
        )
    assert decision.kind == "joined", decision.kind

    # The block is lifted (back_in_stock only) and the consent reads granted.
    with core_db.tenant_tx(tid) as conn:
        assert repos_outbox.has_suppression(conn, tid, cid, "back_in_stock") is False
        assert repos_outbox.has_suppression(conn, tid, cid, "marketing") is True

    # The availability notice now crosses the REAL gate: sent, not suppressed.
    db_testsupport.seed_number_health(
        dsn, tenant_id=tid, channel_id=chid, daily_cap=50, sent_today=0,
    )
    db_testsupport.seed_inbound_message(dsn, tenant_id=tid, conversation_id=conv, body="مرحبا",
                                        created_at=PNOW - timedelta(days=2))
    oid = db_testsupport.seed_outbox_row(
        dsn, tenant_id=tid, channel_id=chid, conversation_id=conv,
        origin="automation", message_class="utility", to_wa_id=wa,
        payload={"template": "stock_available", "text": "عاد «X» للتوفر!"},
    )
    gate_settings = SimpleNamespace(
        send_policy_marketing_ttl_h=24, send_policy_utility_ttl_h=6,
        send_policy_interaction_window_d=180, send_policy_active_chat_cooldown_s=1800,
        send_policy_quiet_start="22:00", send_policy_quiet_end="09:00",
        send_policy_marketing_per_24h=1, send_policy_marketing_per_7d=2,
        send_policy_utility_per_24h=3, send_policy_marketing_gap=(20, 60),
        send_policy_utility_gap=(8, 20),
    )
    row = repos_outbox.OutboxRow(
        id=oid, tenant_id=tid, conversation_id=conv, channel_account_id=chid,
        idempotency_key=f"k-{oid}", origin="automation", message_class="utility",
        expected_epoch=None, to_wa_id=wa,
        payload={"template": "stock_available", "text": "عاد «X» للتوفر!"},
        attempts=0, created_at=PNOW,
    )
    decision = policy_gate.gate(gate_settings, row, now=PNOW, gap_s=8)
    assert decision.send is True, "the stock_joined promise must be kept"
    assert db_testsupport.fetch_outbox_status(dsn, oid) != "dropped_policy"


# --- §4.10: the consent-history CLI carries identifiers only --------------------


def test_consent_history_cli_no_phone_no_text(c_ctx, capsys):
    dsn, tid, chid, platform_ref = c_ctx
    wa = "9677999888777"
    stop_text = "إيقاف"
    optin_text = "اشتراك"
    cid = db_testsupport.insert_customer(dsn, tenant_id=tid, wa_id=wa)

    with core_db.tenant_tx(tid) as conn:
        repos_consent.record_optin(conn, tenant_id=tid, customer_id=cid, message_id=uuid.uuid4())
        repos_consent.record_optout(conn, tenant_id=tid, customer_id=cid, message_id=uuid.uuid4())

    rc = cli.main([
        "consent", "history", "--platform-ref", platform_ref,
        "--customer-id", str(cid),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) == 4, "one opt-in + three STOP rows, chronological"
    assert lines[0].startswith("marketing\tTrue\tcustomer_message_optin\t")
    for line in lines[1:]:
        assert line.startswith(("marketing\tFalse", "back_in_stock\tFalse", "review_request\tFalse"))
        assert "customer_message_optout" in line
    # H48/H98, literally: neither the phone number nor any message text appears.
    assert wa not in out
    assert stop_text not in out
    assert optin_text not in out
    # Every evidence is a UUID (identifiers only).
    for line in lines:
        evidence = line.split("\t")[4]
        if evidence:
            uuid.UUID(evidence)


def test_consent_history_cli_unknown_tenant_and_empty_history(c_ctx, capsys):
    dsn, tid, chid, platform_ref = c_ctx
    rc = cli.main([
        "consent", "history", "--platform-ref", "no-such-tenant-ref",
        "--customer-id", str(uuid.uuid4()),
    ])
    assert rc == 1
    assert "unknown tenant_ref" in capsys.readouterr().err

    cid = db_testsupport.insert_customer(dsn, tenant_id=tid, wa_id="9677999888778")
    rc = cli.main([
        "consent", "history", "--platform-ref", platform_ref,
        "--customer-id", str(cid),
    ])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "no consent history"
