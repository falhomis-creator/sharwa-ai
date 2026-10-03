"""core/app/workers/policy_sweep.py - the send-policy sweeper (H81: down fast, up slow).

Runs beside the dispatcher. Every SEND_POLICY_SWEEP_INTERVAL_S it calls
app.policy_sweep_targets() then, for each ai_core WhatsApp channel in its own
tenant_tx: creates the number_health row if absent (N-3), applies the warm-up
cap for the current local day, classifies health from a 24h signal window, and
applies the transition - `paused` is NEVER exited here (only `policy reinstate`),
and `throttled` -> `healthy` waits SEND_POLICY_THROTTLE_COOLDOWN_H since
state_changed_at (slow rise, N-2). The clock is injectable (H84).
"""
from __future__ import annotations

import logging
import time as _time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.db import repos_policy
from app.db.context import system_tx, tenant_tx
from app.obs import logging as obs_logging
from app.obs import metrics
from app.policy import health as health_policy
from app.policy import warmup
from app.text import arabic
from app.workers.config import WorkerSettings

_log = obs_logging.get_logger("policy_sweep")

_HEALTH_CONFIG = health_policy.HealthConfig()


def _complaints(conn, *, channel_id: uuid.UUID, words: tuple[str, ...]) -> int:
    norm_words = {arabic.normalize(w) for w in words if arabic.normalize(w)}
    count = 0
    for body in repos_policy.complaint_bodies(conn, channel_id=channel_id):
        if body and arabic.normalize(body) in norm_words:  # equality, not substring (H49)
            count += 1
    return count


def _apply_transition(conn, *, channel_id, current, verdict, changed_at, now, settings) -> str:
    target = verdict.state
    if current == "paused":
        return "paused"  # H81: never auto-exit paused
    if target == current:
        return current
    if target == "paused":  # down fast
        repos_policy.apply_health_transition(
            conn, channel_id=channel_id, state="paused", reason=verdict.reason,
        )
        metrics.policy_state_transitions_total.labels("paused").inc()
        return "paused"
    if current == "throttled" and target == "healthy":
        # slow rise: stay throttled until the cooldown has elapsed since state_changed_at.
        if changed_at is not None and (now - changed_at) < timedelta(hours=settings.send_policy_throttle_cooldown_h):
            return "throttled"
        repos_policy.apply_health_transition(
            conn, channel_id=channel_id, state="healthy", reason=verdict.reason,
        )
        metrics.policy_state_transitions_total.labels("healthy").inc()
        return "healthy"
    # healthy -> throttled (down fast)
    repos_policy.apply_health_transition(
        conn, channel_id=channel_id, state="throttled", reason=verdict.reason,
    )
    metrics.policy_state_transitions_total.labels("throttled").inc()
    return "throttled"


def _sweep_channel(settings: WorkerSettings, tenant_id: uuid.UUID, channel_id: uuid.UUID, *, now: datetime) -> str | None:
    with tenant_tx(tenant_id) as conn:
        repos_policy.ensure_number_health(conn, tenant_id=tenant_id, channel_id=channel_id)
        row = repos_policy.read_number_health(conn, channel_id=channel_id)
        if row is None:
            return None
        current = row["state"]
        tz_name = repos_policy.read_tenant_timezone(conn, tenant_id=tenant_id)
        tz = ZoneInfo(tz_name) if tz_name else timezone.utc

        # (b) idle warm-up reset: no marketing delivery for IDLE_RESET_D days.
        warmup_started = row["warmup_started_at"]
        if warmup_started is not None:
            last = repos_policy.last_marketing_sent_at(conn, channel_id=channel_id)
            if last is None or (now - last) > timedelta(days=settings.send_policy_idle_reset_d):
                repos_policy.reset_warmup(conn, channel_id=channel_id)
                warmup_started = None

        # (c) daily cap: cap_for(day_index, ladder), throttled caps it by factor.
        day_idx = warmup.day_index(warmup_started, now, tz) if warmup_started is not None else 0
        target_cap = warmup.cap_for(day_idx, settings.send_policy_warmup_ladder)
        cap = int(target_cap * settings.send_policy_throttle_cap_factor) if current == "throttled" else target_cap
        repos_policy.set_daily_cap(conn, channel_id=channel_id, cap=cap)

        # (d) health transition (hard channel state is immediate).
        channel_status = repos_policy.read_channel_status(conn, channel_id=channel_id)
        if channel_status in ("banned", "logged_out", "conflict"):
            if current != "paused":
                repos_policy.apply_health_transition(
                    conn, channel_id=channel_id, state="paused", reason=f"channel_{channel_status}",
                )
                metrics.policy_state_transitions_total.labels("paused").inc()
            new_state = "paused"
        else:
            counts = repos_policy.health_signal_counts(conn, channel_id=channel_id)
            signals = health_policy.HealthSignals(
                marketing_sends=counts["marketing_sends"],
                marketing_failures=counts["marketing_failures"],
                optouts_after_marketing=repos_policy.optouts_after_marketing(conn, channel_id=channel_id),
                complaints=_complaints(conn, channel_id=channel_id, words=settings.send_policy_complaint_words),
                channel_status=channel_status,
            )
            verdict = health_policy.classify(signals, _HEALTH_CONFIG)
            new_state = _apply_transition(
                conn, channel_id=channel_id, current=current, verdict=verdict,
                changed_at=row["state_changed_at"], now=now, settings=settings,
            )

        score = {"healthy": 1.0, "throttled": 0.5, "paused": 0.0}[new_state]
        repos_policy.set_score(conn, channel_id=channel_id, score=score)
        return new_state


def sweep_once(settings: WorkerSettings, *, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    with system_tx() as conn:
        targets = repos_policy.policy_sweep_targets(conn)

    counts: dict[str, int] = {"healthy": 0, "throttled": 0, "paused": 0}
    for tenant_id, channel_id in targets:
        try:
            state = _sweep_channel(settings, tenant_id, channel_id, now=now)
            if state in counts:
                counts[state] += 1
        except Exception as exc:
            metrics.policy_errors_total.labels("sweep").inc()
            obs_logging.log_event(
                _log, event="policy.sweep_error", component="policy_sweep",
                level=logging.ERROR, channel_id=str(channel_id), error=str(exc),
            )

    for state, n in counts.items():
        metrics.number_health_state.labels(state).set(n)
    metrics.policy_sweeper_last_success_timestamp_seconds.set(_time.time())
