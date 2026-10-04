"""core/tests/test_p3_consent_capture_db.py - P3.3 Stage B: the opt-in/opt-out
CAPTURE itself, driven through the REAL RealtimeWorker commit path
(worker._commit -> _commit_customer_message) on a real DB - the same
transaction that persists the message also persists the consent (H99), with
the message id as the only evidence (H98), STOP before opt-in (H96), and the
opt-in capture restricted to text messages (a media caption never opts in).
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

from app import db as core_db
from app.db import repos_consent
from app.db import repos_outbox
from app.db import repos_policy
from app.db import testsupport as db_testsupport
from app.workers import realtime, schema
from app.workers.config import (
    DEFAULT_OPTIN_AR,
    DEFAULT_OPTIN_EN,
    DEFAULT_OPTOUT_AR,
    DEFAULT_OPTOUT_EN,
)

pytestmark = pytest.mark.db


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        core_optout_phrases_ar=DEFAULT_OPTOUT_AR,
        core_optout_phrases_en=DEFAULT_OPTOUT_EN,
        core_optin_phrases_ar=DEFAULT_OPTIN_AR,
        core_optin_phrases_en=DEFAULT_OPTIN_EN,
        core_ingest_max_body_chars=65536,
    )


@pytest.fixture()
def capture_ctx():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    tid = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"p33b-{uuid.uuid4()}", name="P3.3 Capture Tenant",
    )
    chid = db_testsupport.insert_channel_account(
        dsn, tenant_id=tid, type_="whatsapp_baileys",
        session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
    )
    worker = realtime.RealtimeWorker(_settings())
    yield dsn, tid, chid, worker
    db_testsupport.delete_tenant_full(dsn, tid)


def _entry(wa_id: str, text: str, *, type_: str = "text", pmid: str | None = None) -> schema.WalEntry:
    return schema.WalEntry(
        session_id=f"s-{uuid.uuid4()}",
        provider_message_id=pmid or f"pmid-{uuid.uuid4()}",
        type=type_,
        identity=schema.WalIdentity(wa_id=wa_id),
        text=text,
    )


def _commit(worker, chid, tid, entry) -> str:
    result = worker._commit(entry, schema.SessionResolution(
        channel_account_id=chid, tenant_id=tid, engine="ai_core",
    ))
    return result.outcome


def _history(dsn, tid, cid):
    with core_db.tenant_tx(tid) as conn:
        return repos_consent.read_history(conn, tenant_id=tid, customer_id=cid)


def _customer_id(dsn, tid, wa_id) -> uuid.UUID:
    with core_db.tenant_tx(tid) as conn:
        row = conn.execute(
            "SELECT id FROM customers WHERE tenant_id = %s AND wa_id = %s",
            (tid, wa_id),
        ).fetchone()
    assert row is not None
    return row[0]


# --- §4.2: the capture rides the message's own commit transaction -------------


def test_text_optin_captures_once_with_identifier_evidence_only(capture_ctx):
    dsn, tid, chid, worker = capture_ctx
    wa = f"9677{uuid.uuid4().int % 10**10:010d}"
    pmid = f"pmid-{uuid.uuid4()}"
    assert _commit(worker, chid, tid, _entry(wa, "اشتراك", pmid=pmid)) == "committed"

    cid = _customer_id(dsn, tid, wa)
    rows = _history(dsn, tid, cid)
    assert len(rows) == 1
    scope, granted, source, _created, evidence = rows[0]
    assert (scope, granted, source) == ("marketing", True, "customer_message_optin")
    message_id = uuid.UUID(evidence)  # H98: a UUID, never text or phone
    with core_db.tenant_tx(tid) as conn:
        message = conn.execute(
            "SELECT id FROM messages WHERE tenant_id = %s AND provider_message_id = %s",
            (tid, pmid),
        ).fetchone()
    assert message is not None and message[0] == message_id
    # H98 (full-column sweep): no column of the ledger row carries customer text.
    with core_db.tenant_tx(tid) as conn:
        full = conn.execute(
            "SELECT * FROM consents WHERE tenant_id = %s AND customer_id = %s",
            (tid, cid),
        ).fetchall()
    for row in full:
        for value in row:
            assert "اشتراك" not in str(value)
    # Redelivery of the SAME provider message never reaches the capture path.
    assert _commit(worker, chid, tid, _entry(wa, "اشتراك", pmid=pmid)) == "duplicate"
    assert len(_history(dsn, tid, cid)) == 1
    # A SECOND opt-in while already subscribed is a noop - still one row.
    assert _commit(worker, chid, tid, _entry(wa, "اشترك")) == "committed"
    assert len(_history(dsn, tid, cid)) == 1


def test_stop_via_realtime_writes_history_suppressions_and_cancels_queue(capture_ctx):
    """§4.3: a text STOP => three granted=false rows + three suppressions + the
    pending automation queue cancelled, and order_updates untouched."""
    dsn, tid, chid, worker = capture_ctx
    wa = f"9677{uuid.uuid4().int % 10**10:010d}"
    assert _commit(worker, chid, tid, _entry(wa, "مرحبا")) == "committed"
    cid = _customer_id(dsn, tid, wa)
    conv = db_testsupport.seed_conversation(
        dsn, tenant_id=tid, channel_id=chid, customer_id=cid, bot_status="active", epoch=0,
    )
    oid = db_testsupport.seed_outbox_row(
        dsn, tenant_id=tid, channel_id=chid, conversation_id=conv,
        origin="automation", message_class="utility", to_wa_id=wa,
        payload={"template": "stock_available", "text": "عاد «X» للتوفر!"},
    )
    assert _commit(worker, chid, tid, _entry(wa, "إيقاف")) == "committed"

    rows = _history(dsn, tid, cid)
    assert sorted(r[0] for r in rows) == ["back_in_stock", "marketing", "review_request"]
    assert all(r[1] is False and r[2] == "customer_message_optout" for r in rows)
    with core_db.tenant_tx(tid) as conn:
        for scope in ("marketing", "back_in_stock", "review_request"):
            assert repos_outbox.has_suppression(conn, tid, cid, scope) is True
        assert repos_outbox.has_suppression(conn, tid, cid, "order_updates") is False
    assert db_testsupport.fetch_outbox_status(dsn, oid) == "dropped_policy"


def test_media_caption_never_opts_in_but_still_stops(capture_ctx):
    """§4.5: an image captioned «اشتراك» does NOT subscribe (H96: text
    messages only); the SAME caption «إيقاف» DOES stop (D6: any body text)."""
    dsn, tid, chid, worker = capture_ctx
    wa = f"9677{uuid.uuid4().int % 10**10:010d}"
    image_optin = schema.WalEntry(
        session_id=f"s-{uuid.uuid4()}", provider_message_id=f"pmid-{uuid.uuid4()}",
        type="image", identity=schema.WalIdentity(wa_id=wa), text="اشتراك",
        media=schema.WalMedia(kind="image", status="ok", object_key="obj-1"),
    )
    assert _commit(worker, chid, tid, image_optin) == "committed"
    cid = _customer_id(dsn, tid, wa)
    assert _history(dsn, tid, cid) == [], "a media caption is not a marketing consent"

    image_stop = schema.WalEntry(
        session_id=f"s-{uuid.uuid4()}", provider_message_id=f"pmid-{uuid.uuid4()}",
        type="image", identity=schema.WalIdentity(wa_id=wa), text="إيقاف",
        media=schema.WalMedia(kind="image", status="ok", object_key="obj-2"),
    )
    assert _commit(worker, chid, tid, image_stop) == "committed"
    with core_db.tenant_tx(tid) as conn:
        assert repos_outbox.has_suppression(conn, tid, cid, "marketing") is True


def test_full_cycle_optin_stop_optin_via_realtime(capture_ctx):
    """§4.4 (ingest level): opt-in => eligible; STOP => revoked + suppressed;
    opt-in again => granted + exactly the optout-caused block lifted."""
    dsn, tid, chid, worker = capture_ctx
    wa = f"9677{uuid.uuid4().int % 10**10:010d}"
    assert _commit(worker, chid, tid, _entry(wa, "اشتراك")) == "committed"
    cid = _customer_id(dsn, tid, wa)
    with core_db.tenant_tx(tid) as conn:
        ok1 = repos_policy.read_latest_consent(conn, tenant_id=tid, customer_id=cid, scope="marketing")

    assert _commit(worker, chid, tid, _entry(wa, "إيقاف")) == "committed"
    with core_db.tenant_tx(tid) as conn:
        stopped = repos_policy.read_latest_consent(conn, tenant_id=tid, customer_id=cid, scope="marketing")
        blocked = repos_outbox.has_suppression(conn, tid, cid, "marketing")

    assert _commit(worker, chid, tid, _entry(wa, "اشتراك")) == "committed"
    with core_db.tenant_tx(tid) as conn:
        ok2 = repos_policy.read_latest_consent(conn, tenant_id=tid, customer_id=cid, scope="marketing")
        marketing_now = repos_outbox.has_suppression(conn, tid, cid, "marketing")
        bis_now = repos_outbox.has_suppression(conn, tid, cid, "back_in_stock")
        rr_now = repos_outbox.has_suppression(conn, tid, cid, "review_request")

    assert ok1 is True and stopped is False and blocked is True and ok2 is True
    assert marketing_now is False, "H97: exactly the optout-caused block is lifted"
    assert bis_now is True and rr_now is True, "the other scopes stay blocked"
