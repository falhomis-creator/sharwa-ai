"""core/app/workers/scheduler.py - the scheduled-job engine (H87-H93).

One cycle (run_due): claim a global batch by run_at (app.claim_due_jobs, the
frozen SECURITY DEFINER claimer, as sharwa_system) -> for EACH job its OWN
tenant transaction re-reads the world through the kind's handler and applies
the verdict (Done / Defer / Cancel) - or isolates the exception (H93: one
poisoned job never blocks the batch; it retries with exponential backoff until
the cap, then fails with an alert-worthy metric).

H88: claim_due_jobs increments `attempts` on every claim; a Defer (or a
not-yet-due re-check) rolls that increment back - only REAL handler exceptions
consume an attempt. H90: JOB_KINDS is a closed registry - an unknown kind fails
the job without executing it. H92: a job later than its max_lateness_s over
its run_at is cancelled (too_late), never executed. F-P3-23: the result
writers are status-guarded - a job cancelled mid-flight keeps its cancel (H92
beats the engine's write-back) and the outcome is counted "superseded", never
raised. The clock is injectable (H84) and no `sleep` appears in this logic.
"""
from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.db import repos_scheduler
from app.db.context import system_tx, tenant_tx
from app.obs import logging as obs_logging
from app.obs import metrics
from app.workers import cart_reminder
from app.workers.config import WorkerSettings
from app.workers.job_results import Cancel, Defer

_log = obs_logging.get_logger("scheduler")


@dataclass(frozen=True)
class JobKind:
    """H90: every kind declares its handler and MAY override the global knobs;
    None falls back to the §4 settings (env-tunable, never hardcoded)."""

    handler: Callable[..., Any]
    max_attempts: int | None = None
    backoff_base_s: int | None = None
    backoff_cap_s: int | None = None


JOB_KINDS: dict[str, JobKind] = {
    # The closed registry (H90). Adding a kind = this entry + its handler
    # module + tests; S25 (static gate) enforces key <-> handler bijection.
    "cart_reminder": JobKind(handler=cart_reminder.handle_cart_reminder),
}


def _effective(spec: JobKind, settings: WorkerSettings) -> tuple[int, int, int]:
    max_attempts = spec.max_attempts if spec.max_attempts is not None else settings.scheduler_max_attempts
    base = spec.backoff_base_s if spec.backoff_base_s is not None else settings.scheduler_backoff_base_s
    cap = spec.backoff_cap_s if spec.backoff_cap_s is not None else settings.scheduler_backoff_cap_s
    return max_attempts, base, cap


def run_due(settings: WorkerSettings, *, now: datetime | None = None) -> int:
    """One engine cycle. Returns the number of jobs claimed this cycle."""
    now = now or datetime.now(timezone.utc)
    with system_tx() as conn:
        jobs = repos_scheduler.claim(
            conn, limit=settings.scheduler_batch, lease_s=settings.scheduler_lease_s,
        )
    for job in jobs:
        metrics.scheduler_claimed_total.labels(str(job["kind"])).inc()
        try:
            _run_one(settings, job, now)
        except Exception as exc:  # noqa: BLE001 - H93: the engine itself must survive
            obs_logging.log_event(
                _log, event="scheduler.cycle_job_crash", component="scheduler",
                level=logging.ERROR, job_id=str(job["id"]), error=str(exc)[:300],
            )
    _refresh_stats()
    metrics.scheduler_last_success_timestamp_seconds.set(_time.time())
    return len(jobs)


def _run_one(settings: WorkerSettings, job: dict[str, Any], now: datetime) -> None:
    kind = str(job["kind"])
    job_id = job["id"]
    tenant_id = job["tenant_id"]

    spec = JOB_KINDS.get(kind)
    if spec is None:
        # H90: unknown kind => failed + metric, NEVER executed.
        with tenant_tx(tenant_id) as conn:
            finished = repos_scheduler.finish_failed(
                conn, job_id=job_id, error=f"unknown_kind:{kind}",
            )
        metrics.scheduler_outcomes_total.labels(
            kind, "failed" if finished else "superseded",
        ).inc()
        obs_logging.log_event(
            _log, event="scheduler.unknown_kind", component="scheduler",
            level=logging.WARNING, kind=kind,
        )
        return

    max_lateness_s = int(job.get("max_lateness_s") or 0)
    if max_lateness_s > 0 and (now - job["run_at"]).total_seconds() > max_lateness_s:
        # H92: too late (long outage) => cancelled, no execution, no outbox.
        with tenant_tx(tenant_id) as conn:
            repos_scheduler.cancel_by_id(conn, job_id=job_id, reason="too_late")
        metrics.scheduler_outcomes_total.labels(kind, "cancel_too_late").inc()
        obs_logging.log_event(
            _log, event="scheduler.too_late", component="scheduler",
            level=logging.WARNING, kind=kind,
        )
        return

    max_attempts, _base, _cap = _effective(spec, settings)
    attempts = int(job.get("attempts") or 0)  # includes THIS claim
    if attempts > max_attempts:
        with tenant_tx(tenant_id) as conn:
            finished = repos_scheduler.finish_failed(
                conn, job_id=job_id, error="max_attempts_exceeded",
            )
        metrics.scheduler_outcomes_total.labels(
            kind, "failed" if finished else "superseded",
        ).inc()
        return

    try:
        with tenant_tx(tenant_id) as conn:
            result = spec.handler(
                settings, conn, tenant_id=tenant_id, payload=job["payload"], now=now,
            )
            _apply_result(conn, kind=kind, job_id=job_id, result=result)
    except Exception as exc:  # noqa: BLE001 - H93: isolate, count, backoff
        metrics.scheduler_handler_errors_total.labels(kind).inc()
        _retry_or_fail(settings, spec, kind=kind, job_id=job_id, tenant_id=tenant_id,
                       error=exc, attempts=attempts, now=now)
        obs_logging.log_event(
            _log, event="scheduler.handler_error", component="scheduler",
            level=logging.ERROR, kind=kind, job_id=str(job_id),
            error=f"{type(exc).__name__}: {exc}"[:300],  # no payload, ever (H48/H93)
        )


def _apply_result(conn, *, kind: str, job_id, result: Any) -> None:
    if isinstance(result, Defer):
        # H88 + F-P3-23: False => the job was cancelled meanwhile (H92 wins);
        # the verdict applied to nothing and the outcome is "superseded".
        outcome = "defer" if repos_scheduler.defer(
            conn, job_id=job_id, run_at=result.run_at,
        ) else "superseded"
    elif isinstance(result, Cancel):
        repos_scheduler.cancel_by_id(conn, job_id=job_id, reason=result.reason)
        outcome = "cancel_" + result.reason
    else:  # Done (or a handler returning nothing sensible -> treat as done)
        outcome = "done" if repos_scheduler.complete(
            conn, job_id=job_id,
        ) else "superseded"
    metrics.scheduler_outcomes_total.labels(kind, outcome).inc()


def _retry_or_fail(
    settings: WorkerSettings, spec: JobKind, *, kind: str, job_id, tenant_id,
    error: BaseException, attempts: int, now: datetime,
) -> None:
    max_attempts, base, cap = _effective(spec, settings)
    msg = f"{type(error).__name__}: {error}"[:500]
    if attempts >= max_attempts:
        with tenant_tx(tenant_id) as conn:
            finished = repos_scheduler.finish_failed(conn, job_id=job_id, error=msg)
        metrics.scheduler_outcomes_total.labels(
            kind, "failed" if finished else "superseded",
        ).inc()
        return
    backoff_s = min(cap, base * 2 ** max(0, attempts - 1))
    with tenant_tx(tenant_id) as conn:
        recorded = repos_scheduler.fail_attempt(
            conn, job_id=job_id, error=msg, retry_at=now + timedelta(seconds=backoff_s),
        )
    metrics.scheduler_outcomes_total.labels(
        kind, "retry" if recorded else "superseded",
    ).inc()


def _refresh_stats() -> None:
    """Gauges from app.scheduler_job_stats() - a stopped engine is visible only
    through its metrics (H93). Best-effort: stats must never kill a cycle."""
    try:
        with system_tx() as conn:
            rows = repos_scheduler.stats(conn)
    except Exception as exc:  # noqa: BLE001
        obs_logging.log_event(
            _log, event="scheduler.stats_error", component="scheduler",
            level=logging.ERROR, error=str(exc)[:200],
        )
        return
    for kind, status, jobs, oldest_due_s in rows:
        metrics.scheduler_jobs.labels(kind, status).set(jobs)
        if status == "pending":
            metrics.scheduler_oldest_due_seconds.labels(kind).set(oldest_due_s)

