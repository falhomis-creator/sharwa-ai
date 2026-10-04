"""core/tests/test_scheduler_engine_db.py - P3.2 Stage C: the engine (H87-H93)
on a real DB, driven through scheduler.run_due with an injected clock and NO
sleep (time is moved by writing run_at/last_activity_at, like the F-P3-16
burst tests). claim_due_jobs compares run_at to the DB clock, so every margin
is computed against db_now(); the handler verdicts use the injected now.

The marketing template is NEVER registered here: with the production catalog
the cart_reminder handler Cancels('template_not_registered') - the engine runs
dark by design, and these tests assert exactly that shadow.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import MISSING, fields
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import db as core_db
from app.db import repos_scheduler
from app.db import testsupport as db_testsupport
from app.workers import scheduler as engine
from app.workers.scheduler import JOB_KINDS, JobKind

pytestmark = pytest.mark.db

NOW = datetime.now(timezone.utc)


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
def engine_ctx():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    tid = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"eng-{uuid.uuid4()}", name="Engine Tenant",
    )
    chid = db_testsupport.insert_channel_account(
        dsn, tenant_id=tid, type_="whatsapp_baileys",
        session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
    )
    yield dsn, tid, chid
    db_testsupport.delete_tenant_full(dsn, tid)


def _seed_cart_job(
    dsn, tid, chid, *, cart_id: str, seq: int, activity_age_h: float = 30,
    run_at: datetime | None = None, max_lateness_s: int = 0, kind: str = "cart_reminder",
    with_conversation: bool = True,
):
    """One customer (+conversation on the ai_core channel) + OPEN cart whose
    activity is `activity_age_h` old + one scheduled job. `seq` keeps wa_ids
    unique per seed (process-stable, unlike hash()). activity_age_h > 24 =>
    the reminder is due."""
    wa = f"9677{seq % 10**7:07d}"
    cid = db_testsupport.insert_customer(dsn, tenant_id=tid, wa_id=wa)
    if with_conversation:
        db_testsupport.seed_conversation(
            dsn, tenant_id=tid, channel_id=chid, customer_id=cid, bot_status="active", epoch=0,
        )
    db_testsupport.insert_cart(
        dsn, tenant_id=tid, customer_id=cid, platform_cart_id=cart_id,
        last_activity_at=db_testsupport.db_now(dsn) - timedelta(hours=activity_age_h),
        snapshot={"item_count": 2, "total_minor": 5000, "currency": "SAR",
                  "items": [{"title": "قميص"}, {"title": "بنطال"}]},
    )
    dedupe_key = f"cart:{cart_id}:stage1"
    db_testsupport.insert_scheduled_job(
        dsn, tenant_id=tid, kind=kind, dedupe_key=dedupe_key,
        run_at=run_at, payload={"cart_id": cart_id}, max_lateness_s=max_lateness_s,
    )
    return cid, dedupe_key


# --- §5.1: precision - a future job never runs early -------------------------


def test_future_job_not_executed_until_due(engine_ctx):
    dsn, tid, chid = engine_ctx
    future = db_testsupport.db_now(dsn) + timedelta(seconds=30)
    _cid, key = _seed_cart_job(
        dsn, tid, chid, cart_id="CART-PRECISION", seq=1, run_at=future,
    )

    engine.run_due(_settings(), now=NOW)
    job = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, key)
    assert job["status"] == "pending" and job["attempts"] == 0, "future job must not run early"

    db_testsupport.fast_forward_jobs(dsn, tenant_id=tid)  # due now
    engine.run_due(_settings(), now=NOW)
    job = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, key)
    assert job["status"] == "cancelled"
    assert job["cancel_reason"] == "marketing_disabled"  # P3.4: template registered, tenant not enabled (H100)


# --- §5.4a: H88 - twelve consecutive defers never fail the job ---------------


def test_twelve_defers_consume_no_attempt_then_finish(engine_ctx):
    dsn, tid, chid = engine_ctx
    # Activity 23h old => due in ~1h => the handler defers every cycle.
    _cid, key = _seed_cart_job(
        dsn, tid, chid, cart_id="CART-DEFER", seq=2, activity_age_h=23,
        run_at=db_testsupport.db_now(dsn),
    )
    for _ in range(12):
        engine.run_due(_settings(), now=NOW)
        db_testsupport.fast_forward_jobs(dsn, tenant_id=tid)
    job = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, key)
    assert job["status"] == "pending", job
    assert job["attempts"] == 0, "a defer must roll the claim's attempt back (H88)"

    # Age the cart past the delay: the SAME job now completes (dark) cleanly.
    db_testsupport.set_cart_last_activity(
        dsn, tenant_id=tid, platform_cart_id="CART-DEFER",
        last_activity_at=db_testsupport.db_now(dsn) - timedelta(hours=30),
    )
    engine.run_due(_settings(), now=NOW)
    job = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, key)
    assert job["status"] == "cancelled" and job["attempts"] == 1, job


# --- §5.4b: a 130-job burst with batch=50 finishes, never fails ---------------


def test_batch_130_with_limit_50_all_finish_none_failed(engine_ctx):
    dsn, tid, chid = engine_ctx
    for i in range(130):
        _seed_cart_job(dsn, tid, chid, cart_id=f"CART-BURST-{i}", seq=100 + i)
    settings = _settings(scheduler_batch=50)
    for _ in range(10):
        engine.run_due(settings, now=NOW)
        db_testsupport.fast_forward_jobs(dsn, tenant_id=tid)
        with core_db.system_tx() as conn:
            rows = repos_scheduler.stats(conn)
        pending = sum(j for _k, _s, j, _o in rows if _k == "cart_reminder" and _s == "pending")
        if pending == 0:
            break
    with core_db.system_tx() as conn:
        rows = repos_scheduler.stats(conn)
    by_status = {s: j for k, s, j, _o in rows if k == "cart_reminder"}
    assert by_status.get("failed", 0) == 0, rows
    assert by_status.get("pending", 0) == 0, rows
    assert by_status.get("cancelled", 0) == 130, rows  # dark: template_not_registered


# --- §5.4c: a REAL exception consumes attempts and fails at the cap -----------


def _boom(settings, conn, *, tenant_id, payload, now):
    raise RuntimeError("boom: handler is broken")


def test_real_exception_consumes_attempts_and_fails(engine_ctx, monkeypatch):
    monkeypatch.setitem(JOB_KINDS, "test_poison", JobKind(
        handler=_boom, max_attempts=3, backoff_base_s=1, backoff_cap_s=2,
    ))
    dsn, tid, chid = engine_ctx
    db_testsupport.insert_scheduled_job(
        dsn, tenant_id=tid, kind="test_poison", dedupe_key="poison:1",
        payload={"secret": "NEVER-LOG-THIS-PAYLOAD"},
    )
    for cycle in range(3):
        engine.run_due(_settings(), now=NOW)
        job = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "poison:1")
        if cycle < 2:
            assert job["status"] == "pending" and job["attempts"] == cycle + 1, job
            assert "boom" in (job["last_error"] or ""), job
            db_testsupport.fast_forward_jobs(dsn, tenant_id=tid)  # backoff bypassed in-test
        else:
            assert job["status"] == "failed" and job["attempts"] == 3, job


# --- §5.6: one poisoned job never blocks the batch ---------------------------


def test_poison_does_not_block_the_batch(engine_ctx, monkeypatch, caplog):
    monkeypatch.setitem(JOB_KINDS, "test_poison2", JobKind(
        handler=_boom, max_attempts=5, backoff_base_s=60, backoff_cap_s=3600,
    ))
    dsn, tid, chid = engine_ctx
    db_testsupport.insert_scheduled_job(
        dsn, tenant_id=tid, kind="test_poison2", dedupe_key="poison:2",
        payload={"secret": "NEVER-LOG-THIS-PAYLOAD"},
    )
    healthy_keys = []
    for i in range(10):
        _cid, key = _seed_cart_job(dsn, tid, chid, cart_id=f"CART-HEALTHY-{i}", seq=300 + i)
        healthy_keys.append(key)
    with caplog.at_level("ERROR"):
        engine.run_due(_settings(scheduler_batch=50), now=NOW)
    poison = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "poison:2")
    assert poison["status"] == "pending" and "boom" in (poison["last_error"] or "")
    for key in healthy_keys:
        job = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, key)
        assert job["status"] == "cancelled", f"{key} was starved by the poisoned job"
    assert "NEVER-LOG-THIS-PAYLOAD" not in caplog.text, "payload leaked into logs (H48/H93)"


# --- §5.7: unknown kind => failed, never executed ----------------------------


def test_unknown_kind_fails_without_executing(engine_ctx):
    dsn, tid, chid = engine_ctx
    db_testsupport.insert_scheduled_job(
        dsn, tenant_id=tid, kind="mystery_kind", dedupe_key="mystery:1", payload={},
    )
    engine.run_due(_settings(), now=NOW)
    job = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "mystery:1")
    assert job["status"] == "failed"
    assert (job["last_error"] or "").startswith("unknown_kind:")


# --- §5.8: H92 - later than max_lateness => cancelled/too_late, no outbox -----


def test_too_late_job_cancelled_without_outbox(engine_ctx):
    dsn, tid, chid = engine_ctx
    stale = db_testsupport.db_now(dsn) - timedelta(hours=2)
    _cid, key = _seed_cart_job(
        dsn, tid, chid, cart_id="CART-LATE", seq=5, run_at=stale, max_lateness_s=3600,
    )
    engine.run_due(_settings(), now=NOW)
    job = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, key)
    assert job["status"] == "cancelled" and job["cancel_reason"] == "too_late", job
    assert db_testsupport.count_outbox_by_idempotency_key(dsn, tid, "cart:CART-LATE:stage1") == 0


# --- §5.5: resume after a dead worker (expired lease) executes once -----------


def test_expired_lease_job_executes_once(engine_ctx):
    dsn, tid, chid = engine_ctx
    # Seed the EXACT post-crash state: processing, lease expired, 1 attempt
    # already consumed by the dead worker.
    _cid, key = _seed_cart_job(dsn, tid, chid, cart_id="CART-RESUME", seq=6)
    job = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, key)
    db_testsupport.insert_scheduled_job(
        dsn, tenant_id=tid, kind="cart_reminder", dedupe_key="cart:CART-RESUME2:stage1",
        payload={"cart_id": "CART-RESUME2"}, status="processing", attempts=1,
        locked_until=db_testsupport.db_now(dsn) - timedelta(hours=1),
    )
    db_testsupport.insert_cart(
        dsn, tenant_id=tid, customer_id=_cid, platform_cart_id="CART-RESUME2",
        last_activity_at=db_testsupport.db_now(dsn) - timedelta(hours=30),
    )

    engine.run_due(_settings(), now=NOW)
    recovered = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "cart:CART-RESUME2:stage1")
    assert recovered["status"] == "cancelled"  # dark completion - executed exactly once
    assert recovered["attempts"] == 2  # the dead claim + the recovering claim
    engine.run_due(_settings(), now=NOW)  # nothing left to claim
    recovered = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "cart:CART-RESUME2:stage1")
    assert recovered["status"] == "cancelled" and recovered["attempts"] == 2


# --- §5.9: tenant fairness - 500 jobs cannot starve 1 ------------------------


def test_tenant_fairness_500_vs_1(engine_ctx):
    dsn, tid, chid = engine_ctx
    other = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"eng-b-{uuid.uuid4()}", name="Engine Tenant B",
    )
    try:
        now_db = db_testsupport.db_now(dsn)
        for i in range(500):
            _seed_cart_job(
                dsn, tid, chid, cart_id=f"CART-A-{i}", seq=1000 + i,
                run_at=now_db - timedelta(seconds=10),
            )
        b_chid = db_testsupport.insert_channel_account(
            dsn, tenant_id=other, type_="whatsapp_baileys",
            session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
        )
        _seed_cart_job(
            dsn, other, b_chid, cart_id="CART-B-SOLO", seq=9999,
            run_at=now_db - timedelta(hours=1),  # older => claimed FIRST globally
        )
        settings = _settings(scheduler_batch=50)
        cycles = 0
        solo = None
        for _ in range(20):
            cycles += 1
            engine.run_due(settings, now=NOW)
            db_testsupport.fast_forward_jobs(dsn, tenant_id=tid)
            db_testsupport.fast_forward_jobs(dsn, tenant_id=other)
            solo = db_testsupport.fetch_scheduled_job_by_key(dsn, other, "cart:CART-B-SOLO:stage1")
            if solo["status"] != "pending":
                break
        assert solo["status"] == "cancelled", f"single due job starved for {cycles} cycles"
        assert cycles <= 3, f"fairness: the lone job needed {cycles} cycles"
        # Drain tenant A: everything finishes, nothing fails.
        for _ in range(15):
            engine.run_due(settings, now=NOW)
            db_testsupport.fast_forward_jobs(dsn, tenant_id=tid)
            with core_db.system_tx() as conn:
                rows = repos_scheduler.stats(conn)
            pending_a = sum(j for k, s, j, _o in rows if k == "cart_reminder" and s == "pending")
            if pending_a == 0:
                break
        with core_db.system_tx() as conn:
            rows = repos_scheduler.stats(conn)
        failed = sum(j for k, s, j, _o in rows if k == "cart_reminder" and s == "failed")
        assert failed == 0, rows
    finally:
        db_testsupport.delete_tenant_full(dsn, other)


# --- §5.14 (writers): full-path health of every repos_scheduler writer --------


def test_writer_semantics_in_real_role(engine_ctx):
    dsn, tid, chid = engine_ctx
    payload = {"cart_id": "CART-W"}
    with core_db.tenant_tx(tid) as conn:
        assert repos_scheduler.schedule(
            conn, tenant_id=tid, kind="cart_reminder", dedupe_key="cart:CART-W:stage1",
            run_at=NOW, payload=payload, max_lateness_s=60,
        ) is True
        assert repos_scheduler.schedule(  # duplicate event => no second job
            conn, tenant_id=tid, kind="cart_reminder", dedupe_key="cart:CART-W:stage1",
            run_at=NOW, payload=payload, max_lateness_s=60,
        ) is False
        assert repos_scheduler.reschedule(
            conn, tenant_id=tid, dedupe_key="cart:CART-W:stage1",
            run_at=NOW + timedelta(hours=1),
        ) is True
        assert repos_scheduler.cancel(
            conn, tenant_id=tid, dedupe_key="cart:CART-W:stage1", reason="cart_recovered",
        ) is True
        assert repos_scheduler.cancel(  # already terminal => no-op
            conn, tenant_id=tid, dedupe_key="cart:CART-W:stage1", reason="cart_recovered",
        ) is False
        assert repos_scheduler.reschedule(  # terminal job is never moved
            conn, tenant_id=tid, dedupe_key="cart:CART-W:stage1", run_at=NOW,
        ) is False
    job = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "cart:CART-W:stage1")
    assert job["status"] == "cancelled" and job["cancel_reason"] == "cart_recovered"



