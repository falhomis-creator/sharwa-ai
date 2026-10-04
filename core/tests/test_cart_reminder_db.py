"""core/tests/test_cart_reminder_db.py - P3.2 Stage E: the cart_reminder
handler's own acceptance (H89/H92) - the DARK path first and formally:

  1. With the PRODUCTION catalog (no cart_reminder template registered) the
     full flow ends cancelled/template_not_registered with ZERO outbox rows -
     and the test FAILS the moment anyone registers a marketing template in
     production (the wired-but-dark invariant, §5.11).
  2. With a template injected IN-TEST ONLY: enqueue via proactive.py exactly
     once (idempotency_key cart:{id}:stage1), cart flips to 'reminded' in the
     same transaction, and a crash-after-effects re-run takes no second slot.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import MISSING, fields
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.db import testsupport as db_testsupport
from app.policy.types import TemplateMeta
from app.workers import config
from app.workers import scheduler as engine

pytestmark = pytest.mark.db

NOW = datetime.now(timezone.utc)

# The marketing cart template - INJECTED IN-TEST ONLY. Production stays dark.
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
    d.update(kw)
    return SimpleNamespace(**d)


@pytest.fixture()
def handler_ctx():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    tid = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"rem-{uuid.uuid4()}", name="Reminder Tenant",
    )
    chid = db_testsupport.insert_channel_account(
        dsn, tenant_id=tid, type_="whatsapp_baileys",
        session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
    )
    cid = db_testsupport.insert_customer(dsn, tenant_id=tid, wa_id="967734000001")
    conv = db_testsupport.seed_conversation(
        dsn, tenant_id=tid, channel_id=chid, customer_id=cid, bot_status="active", epoch=0,
    )
    db_testsupport.insert_cart(
        dsn, tenant_id=tid, customer_id=cid, platform_cart_id="CART-H",
        last_activity_at=db_testsupport.db_now(dsn) - timedelta(hours=30),
        snapshot={"item_count": 2, "total_minor": 5000, "currency": "SAR",
                  "items": [{"title": "قميص"}, {"title": "بنطال"}]},
    )
    job_id = db_testsupport.insert_scheduled_job(
        dsn, tenant_id=tid, kind="cart_reminder", dedupe_key="cart:CART-H:stage1",
        payload={"cart_id": "CART-H"},
    )
    yield dsn, tid, chid, cid, conv, job_id
    db_testsupport.delete_tenant_full(dsn, tid)


# --- §5.11: the official DARK test -------------------------------------------


def test_dark_production_catalog_cancels_with_zero_outbox(handler_ctx):
    dsn, tid, _chid, _cid, _conv, _job_id = handler_ctx
    # The wired-but-dark invariant itself: this FAILS if anyone registers a
    # marketing template in the production catalog.
    assert "cart_reminder" not in config.PROACTIVE_TEMPLATES, (
        "a marketing cart_reminder template must NOT be registered in production "
        "(P3.4 owner approval required)"
    )

    engine.run_due(_settings(), now=NOW)
    job = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "cart:CART-H:stage1")
    assert job["status"] == "cancelled"
    assert job["cancel_reason"] == "template_not_registered"
    cart = db_testsupport.fetch_cart(dsn, tid, "CART-H")
    assert cart["status"] == "open", "nothing happens to the cart in the dark"
    assert db_testsupport.count_outbox_rows(dsn, tenant_id=tid) == 0, \
        "the dark engine must produce ZERO outbox rows"


# --- check-order table (§E) ---------------------------------------------------


def test_no_conversation_cancels_before_template(handler_ctx):
    """No ai_core conversation => Cancel('no_conversation') even with the
    production catalog (the conversation check precedes the template check)."""
    dsn, tid, chid, _cid, _conv, _job_id = handler_ctx
    lonely = db_testsupport.insert_customer(dsn, tenant_id=tid, wa_id="967734000002")
    db_testsupport.insert_cart(
        dsn, tenant_id=tid, customer_id=lonely, platform_cart_id="CART-LONELY",
        last_activity_at=db_testsupport.db_now(dsn) - timedelta(hours=30),
    )
    db_testsupport.insert_scheduled_job(
        dsn, tenant_id=tid, kind="cart_reminder", dedupe_key="cart:CART-LONELY:stage1",
        payload={"cart_id": "CART-LONELY"},
    )
    engine.run_due(_settings(), now=NOW)
    job = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "cart:CART-LONELY:stage1")
    assert job["status"] == "cancelled" and job["cancel_reason"] == "no_conversation"
    assert db_testsupport.count_outbox_by_idempotency_key(dsn, tid, "cart:CART-LONELY:stage1") == 0


def test_not_due_cart_defers_without_attempt(handler_ctx):
    """Activity 23h old => Defer (H88): pending, attempts rolled back, and the
    NEW run_at is the cart's last_activity + delay."""
    dsn, tid, _chid, _cid, _conv, _job_id = handler_ctx
    activity = db_testsupport.db_now(dsn) - timedelta(hours=23)
    db_testsupport.set_cart_last_activity(
        dsn, tenant_id=tid, platform_cart_id="CART-H", last_activity_at=activity,
    )
    engine.run_due(_settings(), now=NOW)
    job = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "cart:CART-H:stage1")
    assert job["status"] == "pending" and job["attempts"] == 0
    assert abs((job["run_at"] - (activity + timedelta(hours=24))).total_seconds()) < 1


def test_finalized_cart_between_claim_and_run_cancels(handler_ctx):
    """§5.3: a recovered event lands BETWEEN claim and execution - the handler
    re-reads the world and cancels (H92: finality beats execution)."""
    dsn, tid, _chid, _cid, _conv, job_id = handler_ctx
    db_testsupport.set_cart_status(dsn, tenant_id=tid, platform_cart_id="CART-H", status="recovered")
    engine.run_due(_settings(), now=NOW)
    job = db_testsupport.fetch_scheduled_job(dsn, job_id)
    assert job["status"] == "cancelled" and job["cancel_reason"] == "cart_closed"
    assert db_testsupport.count_outbox_by_idempotency_key(dsn, tid, "cart:CART-H:stage1") == 0


# --- the injected-template path (in-TEST only; production stays dark) --------


def test_injected_template_enqueues_once_and_marks_reminded(handler_ctx, monkeypatch):
    monkeypatch.setitem(config.PROACTIVE_TEMPLATES, "cart_reminder", _CART_TEMPLATE)
    dsn, tid, chid, cid, conv, job_id = handler_ctx
    db_testsupport.seed_number_health(
        dsn, tenant_id=tid, channel_id=chid, daily_cap=50, sent_today=0, day="1970-01-01",
    )

    engine.run_due(_settings(), now=NOW)
    job = db_testsupport.fetch_scheduled_job(dsn, job_id)
    assert job["status"] == "done", job

    n = db_testsupport.count_outbox_by_idempotency_key(dsn, tid, "cart:CART-H:stage1")
    assert n == 1, "exactly one outbox row via proactive.py (idempotency key)"
    cart = db_testsupport.fetch_cart(dsn, tid, "CART-H")
    assert cart["status"] == "reminded", "reminded in the SAME transaction (H89)"

    # The single outbox row's shape: automation/marketing with the closed merge.
    row = db_testsupport.fetch_outbox_row_by_idempotency(dsn, tid, "cart:CART-H:stage1")
    assert row is not None
    assert row["origin"] == "automation" and row["message_class"] == "marketing"
    assert row["conversation_id"] == conv
    assert row["payload"]["template"] == "cart_reminder"
    assert "«title»" not in row["payload"]["text"], "merge fields must render"
    assert "قميص" in row["payload"]["text"]


def test_crash_after_effects_takes_no_second_slot(handler_ctx, monkeypatch):
    """§5.5: simulate a worker dying after the effects committed but before
    `complete` - the re-run finds a reminded cart and cancels cleanly; the
    outbox still holds exactly ONE row for the idempotency key."""
    monkeypatch.setitem(config.PROACTIVE_TEMPLATES, "cart_reminder", _CART_TEMPLATE)
    dsn, tid, chid, cid, conv, job_id = handler_ctx
    db_testsupport.seed_number_health(
        dsn, tenant_id=tid, channel_id=chid, daily_cap=50, sent_today=0, day="1970-01-01",
    )

    engine.run_due(_settings(), now=NOW)  # effects + done
    db_testsupport.reset_job_to_pending(dsn, job_id)  # crash before complete
    engine.run_due(_settings(), now=NOW)  # recovery run

    job = db_testsupport.fetch_scheduled_job(dsn, job_id)
    assert job["status"] == "cancelled" and job["cancel_reason"] == "cart_closed"
    assert db_testsupport.count_outbox_by_idempotency_key(dsn, tid, "cart:CART-H:stage1") == 1

