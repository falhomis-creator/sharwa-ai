"""core/app/db/repos_policy.py - P3.1 send-policy raw SQL (H76-H86).

Same discipline as repos_outbox/repos_stock: every SQL string for the send
policy (the drip slot, the sweeper, the CLI, and the gate's snapshot reads)
lives here and ONLY here. Callers pass an already-open connection from
tenant_tx()/system_tx() and never see a SQL string themselves.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row


# --- the atomic drip slot (H79) --------------------------------------------

def reserve_send_slot(
    conn: psycopg.Connection, *, channel_id: uuid.UUID, message_class: str,
    gap_s: int, p_now: datetime,
) -> dict[str, Any]:
    """Call app.reserve_send_slot (SECURITY INVOKER, FOR UPDATE on number_health).
    Columns are read BY NAME (H86). `p_now` is injected (H84: the clock is an
    input), never SQL now() - deterministic tests. The caller passes the
    in-band gap (jittered marketing/utility gap) as p_gap_s."""
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(
            "SELECT verdict, defer_until, sent_today, daily_cap "
            "FROM app.reserve_send_slot(%s, %s, %s, %s)",
            (channel_id, message_class, gap_s, p_now),
        ).fetchone()
    if row is None:
        raise RuntimeError("app.reserve_send_slot returned no row")
    return row


def release_send_slot(conn: psycopg.Connection, *, channel_id: uuid.UUID, message_class: str) -> None:
    """Call app.release_send_slot (decrements the right counter, never below 0)."""
    conn.execute("SELECT app.release_send_slot(%s, %s)", (channel_id, message_class))


# --- sweeper targets (system role) -----------------------------------------

def policy_sweep_targets(conn: psycopg.Connection) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """app.policy_sweep_targets() - every ai_core WhatsApp channel, cross-tenant
    (SECURITY DEFINER, sharwa_system). The sweeper then opens tenant_tx per row."""
    rows = conn.execute("SELECT tenant_id, channel_account_id FROM app.policy_sweep_targets()").fetchall()
    return [(r[0], r[1]) for r in rows]


# --- number_health CRUD ----------------------------------------------------

def ensure_number_health(conn: psycopg.Connection, *, tenant_id: uuid.UUID, channel_id: uuid.UUID) -> None:
    """N-3: the sweeper creates the number_health row (ON CONFLICT DO NOTHING) -
    until it does, reserve_send_slot returns no_health_row forever."""
    conn.execute(
        "INSERT INTO number_health (channel_account_id, tenant_id) VALUES (%s, %s) "
        "ON CONFLICT (channel_account_id) DO NOTHING",
        (channel_id, tenant_id),
    )


def read_number_health(conn: psycopg.Connection, *, channel_id: uuid.UUID) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT channel_account_id, tenant_id, score, daily_cap, sent_today, day, state, "
            " warmup_started_at, next_marketing_at, next_utility_at, utility_daily_cap, "
            " utility_sent_today, state_reason, state_changed_at, updated_at "
            "FROM number_health WHERE channel_account_id = %s",
            (channel_id,),
        ).fetchone()


def set_daily_cap(conn: psycopg.Connection, *, channel_id: uuid.UUID, cap: int) -> None:
    conn.execute(
        "UPDATE number_health SET daily_cap = %s, updated_at = now() WHERE channel_account_id = %s",
        (cap, channel_id),
    )


def reset_warmup(conn: psycopg.Connection, *, channel_id: uuid.UUID) -> None:
    conn.execute(
        "UPDATE number_health SET warmup_started_at = NULL, updated_at = now() "
        "WHERE channel_account_id = %s",
        (channel_id,),
    )


def apply_health_transition(
    conn: psycopg.Connection, *, channel_id: uuid.UUID, state: str, reason: str,
) -> None:
    conn.execute(
        "UPDATE number_health SET state = %s, state_reason = %s, "
        " state_changed_at = now(), updated_at = now() WHERE channel_account_id = %s",
        (state, reason, channel_id),
    )


def set_score(conn: psycopg.Connection, *, channel_id: uuid.UUID, score: float) -> None:
    conn.execute(
        "UPDATE number_health SET score = %s, updated_at = now() WHERE channel_account_id = %s",
        (score, channel_id),
    )


def last_marketing_sent_at(conn: psycopg.Connection, *, channel_id: uuid.UUID) -> datetime | None:
    """The most recent marketing delivery (outbox sent_at) - drives the idle
    warm-up reset (SEND_POLICY_IDLE_RESET_D)."""
    row = conn.execute(
        "SELECT max(sent_at) FROM outbox "
        "WHERE channel_account_id = %s AND message_class = 'marketing' AND status = 'sent'",
        (channel_id,),
    ).fetchone()
    return row[0] if row is not None else None


# --- consent (H78) ---------------------------------------------------------

def read_latest_consent(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID, scope: str,
) -> bool:
    """Latest-wins: the most recent consents row for (tenant, customer, scope).
    No row => NOT consented (H78: the absence is a refusal)."""
    row = conn.execute(
        "SELECT granted FROM consents "
        "WHERE tenant_id = %s AND customer_id = %s AND scope = %s "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (tenant_id, customer_id, scope),
    ).fetchone()
    return bool(row is not None and row[0])


def write_consent(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
    scope: str, granted: bool, source: str, evidence: str | None,
) -> None:
    """Append a consents row (latest-wins). Written in the waitlist-join
    transaction (H50: in the coordinator, not the tool)."""
    conn.execute(
        "INSERT INTO consents (tenant_id, customer_id, scope, granted, source, evidence) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (tenant_id, customer_id, scope, granted, source, evidence),
    )


# --- gate snapshot reads (H76) ---------------------------------------------

def read_channel_status(conn: psycopg.Connection, *, channel_id: uuid.UUID) -> str | None:
    row = conn.execute(
        "SELECT status FROM channel_accounts WHERE id = %s", (channel_id,),
    ).fetchone()
    return None if row is None else row[0]


def read_tenant_timezone(conn: psycopg.Connection, *, tenant_id: uuid.UUID) -> str | None:
    row = conn.execute(
        "SELECT timezone FROM tenants WHERE id = %s", (tenant_id,),
    ).fetchone()
    return None if row is None else row[0]


def has_prior_interaction(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
    window_d: int, now: datetime,
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM messages m JOIN conversations c ON c.id = m.conversation_id "
        "WHERE c.tenant_id = %s AND c.customer_id = %s AND m.direction = 'in' "
        "AND m.created_at > %s - make_interval(days => %s) LIMIT 1",
        (tenant_id, customer_id, now, window_d),
    ).fetchone()
    return row is not None


def has_active_chat(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
    cooldown_s: int, now: datetime,
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM messages m JOIN conversations c ON c.id = m.conversation_id "
        "WHERE c.tenant_id = %s AND c.customer_id = %s AND m.direction = 'in' "
        "AND m.created_at > %s - make_interval(secs => %s) LIMIT 1",
        (tenant_id, customer_id, now, cooldown_s),
    ).fetchone()
    return row is not None


def last_inbound_at(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
) -> datetime | None:
    """The most recent inbound message from one customer (for active-chat defer)."""
    row = conn.execute(
        "SELECT max(m.created_at) FROM messages m JOIN conversations c ON c.id = m.conversation_id "
        "WHERE c.tenant_id = %s AND c.customer_id = %s AND m.direction = 'in'",
        (tenant_id, customer_id),
    ).fetchone()
    return row[0] if row is not None else None


def count_class_handoffs(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
    message_class: str, hours: int, now: datetime,
    exclude_outbox_id: uuid.UUID | None = None,
) -> int:
    """Frequency caps (per customer): reserved + handed_off ledger rows in the
    window (N-6: reserved also counts - a reserved slot is a delivery in flight).
    F-P3-20: `exclude_outbox_id` removes the ROW BEING GATED from the count -
    its own reservation is the delivery in question, not a prior one. Without
    it a re-processed reserved row counts itself into the cap and is dropped
    (marketing: frequency_cap_skip) or deferred (utility at cap-1) while its
    ledger row stays 'reserved' forever."""
    sql = (
        "SELECT count(*) FROM proactive_ledger "
        "WHERE tenant_id = %s AND customer_id = %s AND message_class = %s "
        "AND status IN ('reserved','handed_off') "
        "AND reserved_at > %s - make_interval(hours => %s)"
    )
    params: list[Any] = [tenant_id, customer_id, message_class, now, hours]
    if exclude_outbox_id is not None:
        sql += " AND outbox_id <> %s"
        params.append(exclude_outbox_id)
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row is not None else 0


# --- ledger (H85) ----------------------------------------------------------

def insert_proactive_ledger(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, channel_id: uuid.UUID,
    customer_id: uuid.UUID, outbox_id: uuid.UUID, message_class: str, template_id: str,
    reserved_at: datetime | None = None,
) -> None:
    """One reservation record per outbox row; UNIQUE(outbox_id) makes re-processing
    idempotent (H85). ON CONFLICT DO NOTHING: a re-run takes no second slot.
    `reserved_at` defaults to now() but the gate passes its injected `now` so the
    frequency windows are deterministic (H84)."""
    conn.execute(
        "INSERT INTO proactive_ledger "
        "(tenant_id, channel_account_id, customer_id, outbox_id, message_class, template_id, reserved_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, now())) ON CONFLICT (outbox_id) DO NOTHING",
        (tenant_id, channel_id, customer_id, outbox_id, message_class, template_id, reserved_at),
    )


def read_ledger_status(conn: psycopg.Connection, *, outbox_id: uuid.UUID) -> str | None:
    """The reservation status for one outbox row, or None (never reserved)."""
    row = conn.execute(
        "SELECT status FROM proactive_ledger WHERE outbox_id = %s", (outbox_id,),
    ).fetchone()
    return None if row is None else row[0]


def mark_ledger_handed_off(conn: psycopg.Connection, *, outbox_id: uuid.UUID) -> None:
    conn.execute(
        "UPDATE proactive_ledger SET status = 'handed_off' WHERE outbox_id = %s", (outbox_id,),
    )


def mark_ledger_released(conn: psycopg.Connection, *, outbox_id: uuid.UUID) -> None:
    conn.execute(
        "UPDATE proactive_ledger SET status = 'released' WHERE outbox_id = %s", (outbox_id,),
    )


# --- outbox policy outcome -------------------------------------------------

def mark_policy_outcome(
    conn: psycopg.Connection, *, outbox_id: uuid.UUID, status: str, reason: str,
) -> None:
    """Set outbox status + policy_reason in one write (drop/defer outcomes)."""
    conn.execute(
        "UPDATE outbox SET status = %s, policy_reason = %s WHERE id = %s",
        (status, reason, outbox_id),
    )


def defer_outbox(
    conn: psycopg.Connection, *, outbox_id: uuid.UUID, defer_until: datetime, reason: str,
    rollback_attempt: bool = True,
) -> None:
    """Defer a row: back to pending with a FUTURE next_attempt_at + policy_reason.
    F-P3-16: a policy defer is NOT a send attempt - the claim's `attempts` increment
    is rolled back (GREATEST(attempts - 1, 0)). `fail_closed` passes
    rollback_attempt=False: a poisoned row SHOULD consume its attempt and fail."""
    if rollback_attempt:
        conn.execute(
            "UPDATE outbox SET status = 'pending', next_attempt_at = %s, policy_reason = %s, "
            "attempts = GREATEST(attempts - 1, 0) WHERE id = %s",
            (defer_until, reason, outbox_id),
        )
    else:
        conn.execute(
            "UPDATE outbox SET status = 'pending', next_attempt_at = %s, policy_reason = %s WHERE id = %s",
            (defer_until, reason, outbox_id),
        )


def cancel_pending_proactive(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, customer_id: uuid.UUID,
    template_ids: tuple[str, ...],
) -> int:
    """STOP cancels the queue (D4): every pending automation row for templates of
    the suppressed scopes is dropped with policy_reason='suppressed'. Rows already
    'sending' are caught by the send-time gate (H76)."""
    cur = conn.execute(
        "UPDATE outbox SET status = 'dropped_policy', policy_reason = 'suppressed' "
        "WHERE tenant_id = %s AND origin = 'automation' AND status = 'pending' "
        "AND payload->>'template' = ANY(%s) "
        "AND conversation_id IN (SELECT id FROM conversations WHERE tenant_id = %s AND customer_id = %s)",
        (tenant_id, list(template_ids), tenant_id, customer_id),
    )
    return cur.rowcount


# --- CLI (status / reinstate) ----------------------------------------------

def read_policy_status(conn: psycopg.Connection, *, channel_id: uuid.UUID) -> dict[str, Any] | None:
    """No phone / no customer text - just the health row (H48)."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT channel_account_id, tenant_id, score, daily_cap, sent_today, day, state, "
            " warmup_started_at, utility_daily_cap, utility_sent_today, state_reason, state_changed_at "
            "FROM number_health WHERE channel_account_id = %s",
            (channel_id,),
        ).fetchone()


def reinstate_number(
    conn: psycopg.Connection, *, channel_id: uuid.UUID, reason: str,
) -> None:
    """The ONLY exit from paused (H81): healthy + warm-up restarts from degree 0."""
    conn.execute(
        "UPDATE number_health SET state = 'healthy', warmup_started_at = NULL, "
        " state_reason = %s, state_changed_at = now(), updated_at = now() "
        "WHERE channel_account_id = %s",
        (f"reinstated: {reason}", channel_id),
    )


# (the reinstate audit row is written through app.db.repos.insert_audit_log, the
# single audit_log writer - its schema is kill-switch shaped, not a free-form
# detail column.)

# --- health signals (24h window, H81) --------------------------------------

def health_signal_counts(conn: psycopg.Connection, *, channel_id: uuid.UUID) -> dict[str, int]:
    """marketing sends / failures in the last 24h for one channel. Opt-outs and
    complaints need text/customer correlation and are assembled by the sweeper."""
    row = conn.execute(
        "SELECT "
        "  count(*) FILTER (WHERE status = 'handed_off' AND message_class = 'marketing'), "
        "  count(*) FILTER (WHERE status = 'failed' AND message_class = 'marketing') "
        "FROM proactive_ledger "
        "WHERE channel_account_id = %s AND reserved_at > now() - interval '24 hours'",
        (channel_id,),
    ).fetchone()
    return {"marketing_sends": int(row[0] or 0), "marketing_failures": int(row[1] or 0)}


def optouts_after_marketing(
    conn: psycopg.Connection, *, channel_id: uuid.UUID,
) -> int:
    """Distinct customers who wrote a marketing suppression within 24h of a
    marketing hand-off, inside the last 24h."""
    row = conn.execute(
        "SELECT count(DISTINCT s.customer_id) FROM suppressions s "
        "JOIN proactive_ledger l ON l.customer_id = s.customer_id AND l.tenant_id = s.tenant_id "
        "WHERE l.channel_account_id = %s AND l.message_class = 'marketing' "
        "AND l.status = 'handed_off' AND s.scope = 'marketing' "
        "AND s.created_at > l.reserved_at AND s.created_at < l.reserved_at + interval '24 hours' "
        "AND s.created_at > now() - interval '24 hours'",
        (channel_id,),
    ).fetchone()
    return int(row[0] or 0)


def complaint_bodies(conn: psycopg.Connection, *, channel_id: uuid.UUID) -> list[str]:
    """Inbound message bodies written within 24h after a marketing hand-off, for
    the sweeper's complaint-word equality match (H49). The text is returned for
    an in-memory match and is NEVER logged (H48)."""
    rows = conn.execute(
        "SELECT m.body FROM messages m "
        "JOIN conversations c ON c.id = m.conversation_id "
        "JOIN proactive_ledger l ON l.customer_id = c.customer_id AND l.channel_account_id = %s "
        "WHERE m.direction = 'in' AND l.message_class = 'marketing' AND l.status = 'handed_off' "
        "AND m.created_at > l.reserved_at AND m.created_at < l.reserved_at + interval '24 hours' "
        "AND m.created_at > now() - interval '24 hours'",
        (channel_id,),
    ).fetchall()
    return [r[0] for r in rows if r[0] is not None]


# --- operator CLI (DSN-based, migration role) -------------------------------

def policy_status(dsn: str, *, channel_id: uuid.UUID) -> dict[str, Any] | None:
    with psycopg.connect(dsn) as conn:
        return read_policy_status(conn, channel_id=channel_id)


def policy_reinstate(dsn: str, *, channel_id: uuid.UUID, reason: str) -> uuid.UUID | None:
    """The ONLY exit from paused (H81), recorded in audit_log. Returns the tenant
    id, or None when the channel has no number_health row."""
    import json

    from app.db import repos

    with psycopg.connect(dsn) as conn:
        row = read_policy_status(conn, channel_id=channel_id)
        if row is None:
            return None
        tenant_id: uuid.UUID = row["tenant_id"]
        before_state: str = row["state"]
        reinstate_number(conn, channel_id=channel_id, reason=reason)
        repos.insert_audit_log(
            conn, tenant_id=str(tenant_id), actor_sub="cli", actor_role="platform_admin",
            action="policy.reinstate", target=f"channel:{channel_id}",
            before=json.dumps({"state": before_state}),
            after=json.dumps({"state": "healthy", "reason": reason}),
            request_id=str(uuid.uuid4()),
        )
        conn.commit()
        return tenant_id

