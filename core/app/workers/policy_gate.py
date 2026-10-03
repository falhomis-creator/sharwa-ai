"""core/app/workers/policy_gate.py - the single proactive send gate (H76/H79/H80).

For every claimed `origin='automation'` row, ONE tenant transaction re-reads the
world (consent, suppression, frequency, quiet hours, channel state, health) and
calls the pure `app.policy.decision.decide`, then applies drop/defer/reserve.
The network send happens AFTER that transaction commits (H40); a second
transaction records the result (sent -> handed_off, else release the slot).

H80: any exception while gating/reserving => fail CLOSED: defer + policy_errors_total
+ no send.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.db import repos_outbox
from app.db import repos_policy
from app.db.context import tenant_tx
from app.obs import logging as obs_logging
from app.obs import metrics
from app.policy import decision, quiet_hours
from app.policy.types import DEFER, DROP, PolicyInput
from app.workers import config
from app.workers.config import WorkerSettings

_log = obs_logging.get_logger("policy_gate")


@dataclass(frozen=True)
class GateDecision:
    send: bool
    session_id: str | None
    message_class: str


def _gap_s(settings: WorkerSettings, message_class: str) -> int:
    lo, hi = (
        settings.send_policy_marketing_gap if message_class == "marketing"
        else settings.send_policy_utility_gap
    )
    return random.randint(lo, hi)


def _parse_hhmm(src: str) -> time:
    h, m = src.split(":", 1)
    return time(int(h), int(m))


def _in_quiet(now: datetime, tz, settings: WorkerSettings) -> bool:
    return quiet_hours.in_quiet_hours(
        now, tz,
        _parse_hhmm(settings.send_policy_quiet_start),
        _parse_hhmm(settings.send_policy_quiet_end),
    )


def gate(settings: WorkerSettings, row: repos_outbox.OutboxRow, *, now: datetime, gap_s: int) -> GateDecision:
    """Transaction 1: pre-read + decide + apply (drop/defer/reserve). No network."""
    template_id = str(row.payload.get("template", ""))
    entry = config.proactive_template(template_id)

    with tenant_tx(row.tenant_id) as conn:
        if entry is None:
            repos_policy.mark_policy_outcome(
                conn, outbox_id=row.id, status="dropped_policy", reason="unknown_template",
            )
            metrics.policy_verdicts_total.labels(row.message_class, "drop", "unknown_template").inc()
            return GateDecision(False, None, row.message_class)

        meta, _text, _keys = entry

        customer_id = (
            repos_outbox.customer_id_for_conversation(conn, row.conversation_id)
            if row.conversation_id is not None else None
        )
        bot_status: str | None = None
        if row.conversation_id is not None:
            es = repos_outbox.read_conversation_epoch_status(conn, row.conversation_id)
            bot_status = es[1] if es is not None else None

        ttl_hours = (
            settings.send_policy_marketing_ttl_h if meta.message_class == "marketing"
            else settings.send_policy_utility_ttl_h
        )
        kill_switch = repos_outbox.effective_switch(
            conn, tenant_id=row.tenant_id, channel_account_id=row.channel_account_id,
            capability=meta.capability,
        )
        suppressed = customer_id is not None and repos_outbox.has_suppression(
            conn, row.tenant_id, customer_id, meta.consent_scope,
        )
        has_consent = customer_id is not None and repos_policy.read_latest_consent(
            conn, tenant_id=row.tenant_id, customer_id=customer_id, scope=meta.consent_scope,
        )
        has_prior = customer_id is not None and repos_policy.has_prior_interaction(
            conn, tenant_id=row.tenant_id, customer_id=customer_id,
            window_d=settings.send_policy_interaction_window_d,
        )
        channel_status = repos_policy.read_channel_status(conn, channel_id=row.channel_account_id)
        health = repos_policy.read_number_health(conn, channel_id=row.channel_account_id)
        number_paused = health is not None and health["state"] == "paused"
        active_chat = customer_id is not None and repos_policy.has_active_chat(
            conn, tenant_id=row.tenant_id, customer_id=customer_id,
            cooldown_s=settings.send_policy_active_chat_cooldown_s,
        )

        quiet = False
        if meta.quiet_hours:
            tz_name = repos_policy.read_tenant_timezone(conn, tenant_id=row.tenant_id)
            if tz_name:
                try:
                    quiet = _in_quiet(now, ZoneInfo(tz_name), settings)
                except Exception:
                    quiet = False  # H80: the reservation itself fails closed on a bad tz

        marketing_24h = repos_policy.count_class_handoffs(
            conn, tenant_id=row.tenant_id, customer_id=customer_id,
            message_class="marketing", hours=24,
        ) if customer_id is not None else 0
        marketing_7d = repos_policy.count_class_handoffs(
            conn, tenant_id=row.tenant_id, customer_id=customer_id,
            message_class="marketing", hours=7 * 24,
        ) if customer_id is not None else 0
        utility_24h = repos_policy.count_class_handoffs(
            conn, tenant_id=row.tenant_id, customer_id=customer_id,
            message_class="utility", hours=24,
        ) if customer_id is not None else 0

        inp = PolicyInput(
            template=meta, now=now, created_at=row.created_at, ttl_hours=ttl_hours,
            kill_switch_state=kill_switch,
            conversation_closed=(bot_status == "closed"),
            suppressed=suppressed, has_consent=has_consent,
            has_prior_interaction=has_prior,
            channel_connected=(channel_status == "connected"),
            number_paused=number_paused,
            human_active=(bot_status == "paused_human"),
            active_chat=active_chat, quiet_hours=quiet,
            marketing_24h=marketing_24h, marketing_7d=marketing_7d, utility_24h=utility_24h,
            per_24h_marketing=settings.send_policy_marketing_per_24h,
            per_7d_marketing=settings.send_policy_marketing_per_7d,
            per_24h_utility=settings.send_policy_utility_per_24h,
        )
        verdict = decision.decide(inp)

        if verdict.action == DROP:
            repos_policy.mark_policy_outcome(
                conn, outbox_id=row.id, status="dropped_policy", reason=verdict.reason,
            )
            metrics.policy_verdicts_total.labels(meta.message_class, "drop", verdict.reason).inc()
            return GateDecision(False, None, meta.message_class)

        if verdict.action == DEFER:
            defer_until = verdict.defer_until or (now + timedelta(seconds=60))
            repos_policy.defer_outbox(
                conn, outbox_id=row.id, defer_until=defer_until, reason=verdict.reason,
            )
            metrics.policy_verdicts_total.labels(meta.message_class, "defer", verdict.reason).inc()
            return GateDecision(False, None, meta.message_class)

        # H85: re-processing the same row takes no second slot (UNIQUE(outbox_id)).
        existing = repos_policy.read_ledger_status(conn, outbox_id=row.id)
        if existing in ("reserved", "handed_off"):
            session_id = repos_outbox.read_channel_session_id(conn, row.channel_account_id)
            metrics.policy_verdicts_total.labels(meta.message_class, "ok", "ok").inc()
            return GateDecision(True, session_id, meta.message_class)

        slot = repos_policy.reserve_send_slot(
            conn, channel_id=row.channel_account_id, message_class=meta.message_class,
            gap_s=gap_s, p_now=now,
        )
        metrics.policy_reserve_total.labels(meta.message_class, slot["verdict"]).inc()
        if slot["verdict"] != "reserved":
            reason = "number_paused" if slot["verdict"] == "paused" else slot["verdict"]
            defer_until = slot.get("defer_until") or (now + timedelta(seconds=60))
            repos_policy.defer_outbox(conn, outbox_id=row.id, defer_until=defer_until, reason=reason)
            metrics.policy_verdicts_total.labels(meta.message_class, "defer", reason).inc()
            return GateDecision(False, None, meta.message_class)

        if customer_id is not None:
            repos_policy.insert_proactive_ledger(
                conn, tenant_id=row.tenant_id, channel_id=row.channel_account_id,
                customer_id=customer_id, outbox_id=row.id,
                message_class=meta.message_class, template_id=template_id,
            )
        session_id = repos_outbox.read_channel_session_id(conn, row.channel_account_id)
        metrics.policy_verdicts_total.labels(meta.message_class, "ok", "ok").inc()
        return GateDecision(True, session_id, meta.message_class)


def fail_closed(settings: WorkerSettings, row: repos_outbox.OutboxRow, exc: BaseException) -> None:
    """H80: a gate/reserve exception defers the row and never sends."""
    with tenant_tx(row.tenant_id) as conn:
        repos_policy.defer_outbox(
            conn, outbox_id=row.id,
            defer_until=datetime.now().astimezone() + timedelta(seconds=60),
            reason="policy_error",
        )
    metrics.policy_errors_total.labels("gate").inc()
    obs_logging.log_event(
        _log, event="policy.error", component="policy_gate",
        level=logging.ERROR, outbox_id=str(row.id), error=str(exc),
    )


def release_slot(settings: WorkerSettings, row: repos_outbox.OutboxRow) -> None:
    """H85: a failure before delivery gives the slot back (ledger released)."""
    with tenant_tx(row.tenant_id) as conn:
        repos_policy.release_send_slot(
            conn, channel_id=row.channel_account_id, message_class=row.message_class,
        )
        repos_policy.mark_ledger_released(conn, outbox_id=row.id)


def mark_sent(settings: WorkerSettings, row: repos_outbox.OutboxRow) -> None:
    """202 accepted: sent + ledger handed_off."""
    with tenant_tx(row.tenant_id) as conn:
        repos_outbox.mark_outbox_sent(conn, outbox_id=row.id)
        repos_policy.mark_ledger_handed_off(conn, outbox_id=row.id)
    metrics.dispatch_attempts_total.labels("sent").inc()
