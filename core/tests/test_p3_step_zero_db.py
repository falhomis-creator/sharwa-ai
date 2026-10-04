"""core/tests/test_p3_step_zero_db.py - P3.3 STEP ZERO (PROMPT_P3_03 §2):
F-P3-22 (schedule revives a job cancelled for a RECOVERABLE reason) and
F-P3-23 (the four result writers are status-guarded; a cancel in flight wins)
on a real DB, driven in the REAL calling role (sharwa_app through tenant_tx),
seeding state only through testsupport / the sanctioned writers themselves.

F-P3-24 is documentation-only (docs/PLATFORM_CART_CONTRACT.md §9) - no code
changed for it, so no test here.
"""
from __future__ import annotations

import os
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app import db as core_db
from app.db import repos_scheduler
from app.db import testsupport as db_testsupport
from app.obs import metrics
from app.workers.scheduler import JOB_KINDS, JobKind
from app.workers import scheduler as engine

pytestmark = pytest.mark.db

KIND = "cart_reminder"
FINAL_CANCEL_REASONS = ("cart_recovered", "cart_cleared", "cart_closed")


@pytest.fixture()
def zero_ctx():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    tid = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"p33z-{uuid.uuid4()}", name="P3.3 Step-Zero Tenant",
    )
    yield dsn, tid
    db_testsupport.delete_tenant_full(dsn, tid)


def _seed_job(dsn, tid, key: str, *, status: str = "pending", attempts: int = 0,
              run_at=None) -> None:
    db_testsupport.insert_scheduled_job(
        dsn, tenant_id=tid, kind=KIND, dedupe_key=key,
        run_at=run_at, payload={"cart_id": key}, status=status, attempts=attempts,
    )


def _cancel(dsn, tid, key: str, reason: str) -> None:
    with core_db.tenant_tx(tid) as conn:
        cancelled = repos_scheduler.cancel(
            conn, tenant_id=tid, dedupe_key=key, reason=reason,
        )
    assert cancelled is True, "seed: the job must actually be cancelled"


def _schedule(tid, key: str, run_at) -> bool:
    with core_db.tenant_tx(tid) as conn:
        return repos_scheduler.schedule(
            conn, tenant_id=tid, kind=KIND, dedupe_key=key, run_at=run_at,
            payload={"cart_id": key}, max_lateness_s=3600,
        )


def _job(dsn, tid, key: str) -> dict:
    job = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, key)
    assert job is not None, f"job {key} vanished"
    return job


def _count_rows(tid, key: str) -> int:
    with core_db.tenant_tx(tid) as conn:
        return int(conn.execute(
            "SELECT count(*) FROM scheduled_jobs WHERE tenant_id = %s AND dedupe_key = %s",
            (tid, key),
        ).fetchone()[0])


# --- F-P3-22: recoverable cancels are revived by fresh activity --------------


@pytest.mark.parametrize("reason", repos_scheduler.REVIVABLE_CANCEL_REASONS)
def test_fresh_activity_revives_recoverable_cancel(zero_ctx, reason):
    """Each recoverable reason: new event => the SAME job is pending again,
    with the fresh run_at, attempts reset, and the cancel residue cleared."""
    dsn, tid = zero_ctx
    key = f"cart:R:{reason}"
    seeded_run_at = db_testsupport.db_now(dsn) - timedelta(hours=2)
    _seed_job(dsn, tid, key, attempts=2, run_at=seeded_run_at)
    _cancel(dsn, tid, key, reason)

    fresh_run_at = db_testsupport.db_now(dsn) + timedelta(hours=6)
    assert _schedule(tid, key, fresh_run_at) is True  # revived, not duplicated
    assert _count_rows(tid, key) == 1

    job = _job(dsn, tid, key)
    assert job["status"] == "pending"
    assert job["run_at"] == fresh_run_at
    assert job["attempts"] == 0, "a revived job starts its attempts over"
    assert job["cancel_reason"] is None and job["last_error"] is None
    full = db_testsupport.fetch_scheduled_job(dsn, job["id"])
    assert full["finished_at"] is None, "the cancel's finished_at must be wiped"


@pytest.mark.parametrize("reason", FINAL_CANCEL_REASONS)
def test_final_cancel_is_never_revived(zero_ctx, reason):
    """The terminal cart trio stays cancelled whatever arrives afterwards."""
    dsn, tid = zero_ctx
    key = f"cart:F:{reason}"
    seeded_run_at = db_testsupport.db_now(dsn) - timedelta(hours=2)
    _seed_job(dsn, tid, key, attempts=2, run_at=seeded_run_at)
    _cancel(dsn, tid, key, reason)

    assert _schedule(tid, key, db_testsupport.db_now(dsn) + timedelta(hours=6)) is False
    job = _job(dsn, tid, key)
    assert job["status"] == "cancelled" and job["cancel_reason"] == reason
    assert job["run_at"] == seeded_run_at, "nothing moved for a final cancel"
    assert job["attempts"] == 2


@pytest.mark.parametrize("status", ("failed", "done"))
def test_failed_and_done_jobs_are_never_revived(zero_ctx, status):
    dsn, tid = zero_ctx
    key = f"cart:X:{status}"
    _seed_job(dsn, tid, key, status=status)
    assert _schedule(tid, key, db_testsupport.db_now(dsn) + timedelta(hours=1)) is False
    assert _job(dsn, tid, key)["status"] == status


def test_two_consecutive_calls_produce_exactly_one_row(zero_ctx):
    """H89 no-op semantics survive F-P3-22: a second delivery of the same event
    finds the job pending, returns False, and never duplicates the row."""
    dsn, tid = zero_ctx
    key = "cart:D:stage1"
    first_run_at = db_testsupport.db_now(dsn) + timedelta(hours=3)
    assert _schedule(tid, key, first_run_at) is True
    assert _schedule(tid, key, first_run_at + timedelta(minutes=1)) is False
    assert _count_rows(tid, key) == 1
    job = _job(dsn, tid, key)
    assert job["status"] == "pending" and job["run_at"] == first_run_at


# --- F-P3-23: the result writers are status-guarded ---------------------------


@pytest.mark.parametrize(
    "writer", ("complete", "fail_attempt", "defer", "finish_failed"),
)
def test_result_writer_on_a_cancelled_job_is_a_noop_false(zero_ctx, writer):
    """Each of the four on a CANCELLED job: returns False, the job keeps its
    cancel (H92). The fail_attempt case IS the architect's probe (cancel then
    fail_attempt => cancelled, where the old code resurrected it to pending)."""
    dsn, tid = zero_ctx
    key = f"cart:W:{writer}"
    _seed_job(dsn, tid, key)
    _cancel(dsn, tid, key, "cart_recovered")
    job_id = _job(dsn, tid, key)["id"]

    with core_db.tenant_tx(tid) as conn:
        if writer == "complete":
            applied = repos_scheduler.complete(conn, job_id=job_id)
        elif writer == "fail_attempt":
            applied = repos_scheduler.fail_attempt(
                conn, job_id=job_id, error="boom: late write-back",
                retry_at=db_testsupport.db_now(dsn) + timedelta(seconds=30),
            )
        elif writer == "defer":
            applied = repos_scheduler.defer(
                conn, job_id=job_id,
                run_at=db_testsupport.db_now(dsn) + timedelta(hours=1),
            )
        else:
            applied = repos_scheduler.finish_failed(conn, job_id=job_id, error="kaput")

    assert applied is False
    job = _job(dsn, tid, key)
    assert job["status"] == "cancelled" and job["cancel_reason"] == "cart_recovered"
    assert job["last_error"] is None, "a superseded write must leave no trace"


def test_result_writers_normal_processing_path_unchanged(zero_ctx):
    """The same four writers on the job the engine is HOLDING ('processing'):
    all return True and the transitions are exactly the pre-F-P3-23 ones."""
    dsn, tid = zero_ctx
    now = db_testsupport.db_now(dsn)

    # complete
    _seed_job(dsn, tid, "cart:NP:complete", status="processing", attempts=1)
    jid = _job(dsn, tid, "cart:NP:complete")["id"]
    with core_db.tenant_tx(tid) as conn:
        assert repos_scheduler.complete(conn, job_id=jid) is True
    row = db_testsupport.fetch_scheduled_job(dsn, jid)
    assert row["status"] == "done" and row["finished_at"] is not None

    # fail_attempt: attempt stays consumed, backoff run_at, error recorded
    _seed_job(dsn, tid, "cart:NP:fail", status="processing", attempts=1)
    jid = _job(dsn, tid, "cart:NP:fail")["id"]
    retry_at = now + timedelta(seconds=90)
    with core_db.tenant_tx(tid) as conn:
        assert repos_scheduler.fail_attempt(
            conn, job_id=jid, error="boom", retry_at=retry_at,
        ) is True
    row = db_testsupport.fetch_scheduled_job(dsn, jid)
    assert row["status"] == "pending" and row["last_error"] == "boom"
    assert row["run_at"] == retry_at

    # defer: NOT an attempt (H88) - the claim's increment rolls back
    _seed_job(dsn, tid, "cart:NP:defer", status="processing", attempts=2)
    jid = _job(dsn, tid, "cart:NP:defer")["id"]
    defer_run_at = now + timedelta(minutes=5)
    with core_db.tenant_tx(tid) as conn:
        assert repos_scheduler.defer(conn, job_id=jid, run_at=defer_run_at) is True
    row = db_testsupport.fetch_scheduled_job(dsn, jid)
    assert row["status"] == "pending" and row["attempts"] == 1
    assert row["run_at"] == defer_run_at

    # finish_failed
    _seed_job(dsn, tid, "cart:NP:ff", status="processing", attempts=3)
    jid = _job(dsn, tid, "cart:NP:ff")["id"]
    with core_db.tenant_tx(tid) as conn:
        assert repos_scheduler.finish_failed(conn, job_id=jid, error="max") is True
    row = db_testsupport.fetch_scheduled_job(dsn, jid)
    assert row["status"] == "failed" and row["finished_at"] is not None


# --- F-P3-23 (engine): superseded is counted, never raised ---------------------


def _cancel_midflight_then_done(settings, conn, *, tenant_id, payload, now):
    """A handler that finalizes the underlying world itself (the H92 race the
    audit described: the webhook's cancel commits while the engine flies)."""
    repos_scheduler.cancel(
        conn, tenant_id=tenant_id, dedupe_key=str(payload["key"]), reason="cart_recovered",
    )
    return None  # Done verdict -> the engine will try to `complete`


def test_engine_counts_superseded_and_does_not_raise(zero_ctx, monkeypatch):
    monkeypatch.setitem(JOB_KINDS, "stepzero_midflight", JobKind(
        handler=_cancel_midflight_then_done,
    ))
    dsn, tid = zero_ctx
    kind = "stepzero_midflight"
    before = metrics.scheduler_outcomes_total.labels(kind, "superseded")._value.get()

    db_testsupport.insert_scheduled_job(
        dsn, tenant_id=tid, kind=kind, dedupe_key="zero:midflight",
        payload={"key": "zero:midflight"},
    )
    engine.run_due(SimpleNamespace(
        scheduler_batch=10, scheduler_lease_s=120,
        scheduler_max_attempts=3, scheduler_backoff_base_s=1, scheduler_backoff_cap_s=2,
    ), now=db_testsupport.db_now(dsn))

    job = _job(dsn, tid, "zero:midflight")
    assert job["status"] == "cancelled" and job["cancel_reason"] == "cart_recovered"
    assert metrics.scheduler_outcomes_total.labels(kind, "superseded")._value.get() > before
