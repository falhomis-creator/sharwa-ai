"""core/app/db/repos_scheduler.py - the SINGLE scheduled_jobs writer (H87).

Every SQL string that writes scheduled_jobs lives here and ONLY here; the only
callers of the writing functions are app/workers/scheduler.py, its handler
modules under app/workers/, and app/workers/carts.py (the webhook's same-tx
cancel, H92) - plus tests (the S24 static gate enforces exactly that).

Reading/claiming crosses tenants only through the frozen app.claim_due_jobs
SECURITY DEFINER function (sharwa_system); everything else runs on a caller's
open tenant_tx()/system_tx() connection under RLS.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


# --- writers (tenant_tx connection; RLS scopes to the job's tenant) ----------

# F-P3-22: the ONLY cancel reasons a fresh event may revive the job from - an
# outage (too_late), the dark-period template gap (template_not_registered), or
# a customer with no conversation yet (no_conversation). Everything else is
# terminal: the cart trio (cart_recovered / cart_cleared / cart_closed) says
# the cart itself ended, and failed/done jobs are never revived by schedule().
REVIVABLE_CANCEL_REASONS = (
    "too_late",
    "template_not_registered",
    "no_conversation",
)


def schedule(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, kind: str, dedupe_key: str,
    run_at: Any, payload: dict[str, Any], max_lateness_s: int,
) -> bool:
    """INSERT ... ON CONFLICT (tenant_id, dedupe_key) (H89: the dedupe key
    makes re-delivery of the same event a no-op), and F-P3-22: a job cancelled
    for a RECOVERABLE reason (REVIVABLE_CANCEL_REASONS) is revived as pending
    with the fresh run_at and attempts reset - the dark period must freeze the
    pipeline, not burn it. True when a NEW job was inserted OR a cancelled one
    was revived; False when an identical key already exists in any other state
    (pending => the caller keeps rescheduling it, H88-adjacent)."""
    row = conn.execute(
        "INSERT INTO scheduled_jobs (tenant_id, kind, dedupe_key, run_at, payload, max_lateness_s) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (tenant_id, dedupe_key) DO UPDATE SET "
        "status = 'pending', run_at = EXCLUDED.run_at, attempts = 0, cancel_reason = NULL, "
        "finished_at = NULL, last_error = NULL, max_lateness_s = EXCLUDED.max_lateness_s "
        "WHERE scheduled_jobs.status = 'cancelled' "
        "AND scheduled_jobs.cancel_reason = ANY(%s) RETURNING id",
        (tenant_id, kind, dedupe_key, run_at, Jsonb(payload), max_lateness_s,
         REVIVABLE_CANCEL_REASONS),
    ).fetchone()
    return row is not None


def reschedule(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, dedupe_key: str, run_at: Any,
) -> bool:
    """Move a PENDING job's run_at (fresh cart activity pushes the reminder
    out). Never touches a job that started executing or already finished."""
    cur = conn.execute(
        "UPDATE scheduled_jobs SET run_at = %s "
        "WHERE tenant_id = %s AND dedupe_key = %s AND status = 'pending'",
        (run_at, tenant_id, dedupe_key),
    )
    return cur.rowcount > 0


def cancel(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, dedupe_key: str, reason: str,
) -> bool:
    """Terminal cancel by dedupe key (H92: cart.recovered/cleared cancels the
    reminder in the SAME transaction that finalizes the cart)."""
    cur = conn.execute(
        "UPDATE scheduled_jobs SET status = 'cancelled', cancel_reason = %s, finished_at = now() "
        "WHERE tenant_id = %s AND dedupe_key = %s AND status IN ('pending','processing')",
        (reason, tenant_id, dedupe_key),
    )
    return cur.rowcount > 0


def cancel_by_id(conn: psycopg.Connection, *, job_id: uuid.UUID, reason: str) -> bool:
    """The engine's terminal cancel for the job it is holding (too_late, or a
    handler Cancel verdict)."""
    cur = conn.execute(
        "UPDATE scheduled_jobs SET status = 'cancelled', cancel_reason = %s, finished_at = now() "
        "WHERE id = %s AND status IN ('pending','processing')",
        (reason, job_id),
    )
    return cur.rowcount > 0


def complete(conn: psycopg.Connection, *, job_id: uuid.UUID) -> bool:
    """F-P3-23: status-guarded - only the job the engine is holding
    ('processing') may complete. One cancelled meanwhile stays cancelled
    (H92: an explicit cancel beats the engine's own write-back); the caller
    counts outcome="superseded" when this returns False."""
    cur = conn.execute(
        "UPDATE scheduled_jobs SET status = 'done', finished_at = now(), last_error = NULL "
        "WHERE id = %s AND status = 'processing'",
        (job_id,),
    )
    return cur.rowcount > 0


def fail_attempt(
    conn: psycopg.Connection, *, job_id: uuid.UUID, error: str, retry_at: Any,
) -> bool:
    """A REAL handler failure (exception, H88): the attempt stays consumed and
    the job goes back to pending with an exponential-backoff run_at. F-P3-23:
    only while still 'processing' - a concurrent cancel wins (H92) and the
    engine counts outcome="superseded" when this returns False."""
    cur = conn.execute(
        "UPDATE scheduled_jobs SET status = 'pending', last_error = %s, run_at = %s "
        "WHERE id = %s AND status = 'processing'",
        (error, retry_at, job_id),
    )
    return cur.rowcount > 0


def defer(conn: psycopg.Connection, *, job_id: uuid.UUID, run_at: Any) -> bool:
    """H88: a defer is NOT an attempt - the claim's attempts increment is
    rolled back (GREATEST(attempts - 1, 0)) and the job returns to pending.
    F-P3-23: only while still 'processing'; a concurrent cancel wins (H92)."""
    cur = conn.execute(
        "UPDATE scheduled_jobs SET status = 'pending', run_at = %s, "
        "attempts = GREATEST(attempts - 1, 0) WHERE id = %s AND status = 'processing'",
        (run_at, job_id),
    )
    return cur.rowcount > 0


def finish_failed(conn: psycopg.Connection, *, job_id: uuid.UUID, error: str) -> bool:
    """F-P3-23: status-guarded terminal failure - only for the 'processing' job
    the engine holds; a concurrent cancel wins (H92) and the engine counts
    outcome="superseded" when this returns False."""
    cur = conn.execute(
        "UPDATE scheduled_jobs SET status = 'failed', last_error = %s, finished_at = now() "
        "WHERE id = %s AND status = 'processing'",
        (error, job_id),
    )
    return cur.rowcount > 0


# --- claim + stats (system role) ---------------------------------------------


def claim(conn: psycopg.Connection, *, limit: int, lease_s: int) -> list[dict[str, Any]]:
    """app.claim_due_jobs (FROZEN - never modified): cross-tenant claim ordered
    globally by run_at, attempts+1 per claim, lease-guarded crash recovery.
    Columns read BY NAME through a dict_row cursor (F-P3-11/H86)."""
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            "SELECT id, tenant_id, kind, dedupe_key, run_at, payload, status, attempts, "
            "max_lateness_s FROM app.claim_due_jobs(%s::integer, make_interval(secs => %s))",
            (limit, lease_s),
        ).fetchall()
    return list(rows)


def stats(conn: psycopg.Connection) -> list[tuple[str, str, int, float]]:
    """app.scheduler_job_stats() - counts + oldest overdue seconds per
    (kind, status). No payloads ever leave the database here (H48)."""
    rows = conn.execute(
        "SELECT v_kind, v_status, v_jobs, v_oldest_due_s FROM app.scheduler_job_stats()"
    ).fetchall()
    return [(r[0], r[1], int(r[2]), float(r[3])) for r in rows]


# --- operator CLI (DSN-based, migration role) --------------------------------


def scheduler_status(dsn: str) -> list[tuple[str, str, int, float]]:
    with psycopg.connect(dsn) as conn:
        return stats(conn)


def scheduler_cancel(
    dsn: str, *, platform_ref: str, dedupe_key: str, reason: str,
) -> tuple[uuid.UUID | None, bool]:
    """Resolve the tenant by platform_ref, cancel one job by dedupe key, and
    write the audit trail. Returns (tenant_id, cancelled?). None tenant =>
    unknown platform_ref."""
    from app.db import repos

    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT id FROM tenants WHERE platform_ref = %s", (platform_ref,),
        ).fetchone()
        if row is None:
            return None, False
        tenant_id: uuid.UUID = row[0]
        cur = conn.execute(
            "UPDATE scheduled_jobs SET status = 'cancelled', cancel_reason = %s, finished_at = now() "
            "WHERE tenant_id = %s AND dedupe_key = %s AND status IN ('pending','processing')",
            (reason, tenant_id, dedupe_key),
        )
        cancelled = cur.rowcount > 0
        repos.insert_audit_log(
            conn, tenant_id=str(tenant_id), actor_sub="cli", actor_role="platform_admin",
            action="scheduler.cancel", target=f"job:{dedupe_key}",
            before=json.dumps({"dedupe_key": dedupe_key}),
            after=json.dumps({"cancelled": cancelled, "reason": reason}),
            request_id=str(uuid.uuid4()),
        )
        conn.commit()
        return tenant_id, cancelled

