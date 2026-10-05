"""core/app/workers/summary.py - the P1.5b rolling summary worker (V6).

H43: the summary is internal bot memory - context, not content. It is read ONLY
to build the prompt input (and later minds in P1.6), written ONLY to
conversations.summary, and never shown to a customer/staff, never logged by value,
never measured, never read by compose.py. The input is phone-masked before the
call and the output is phone-masked AGAIN before storage (the model may repeat a
number it heard).

The ceilings (V9) are all written settings and enforced here in code, and the
trigger/min-between/daily-cap decisions are PURE functions so they are
table-testable without a database.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from app import db as core_db
from app.db import repos_summary
from app.llm.router import mask_phones
from app.obs import logging as obs_logging
from app.obs import metrics
from app.workers.config import WorkerSettings

_log = obs_logging.get_logger("summary")

# Fixed Arabic summarization prompt - no tenant data, no product names, no
# identifiers (H41 posture). The task and output only.
SUMMARY_SYSTEM_PROMPT = (
    "أنت موجِّز محادثات خدمة عملاء. لخِّص الجوهر فقط (ما يبحث عنه العميل وما طلبه "
    "أو اعترض عليه) بأقل كلمات ممكنة، وبلا رقم هاتف كامل ولا معرّف داخلي."
)

# Skip reasons that are deliberate (not errors), counted via summary_skipped_total.
SKIP_REASONS = frozenset({
    "below_trigger", "min_between", "daily_cap",
    "budget_degraded", "breaker_open", "no_messages",
})


@dataclass(frozen=True)
class SummaryHandle:
    """LLM provider + breaker built from settings (only summary.py builds this)."""

    provider: Any
    breaker: Any
    provider_name: str
    model_name: str


def build_summary(settings: WorkerSettings) -> SummaryHandle:
    from app.llm import registry
    from app.llm.breaker import CircuitBreaker

    provider = registry.build_provider(settings.llm_provider, settings)
    breaker = CircuitBreaker(
        settings.llm_breaker_fail_threshold, settings.llm_breaker_reset_s,
    )
    return SummaryHandle(
        provider=provider, breaker=breaker,
        provider_name=settings.llm_provider,
        model_name="fake-router" if settings.llm_provider == "fake" else settings.llm_model,
    )


# --- pure ceilings (V9): table-testable without a DB --------------------------


def summary_decision(
    *, message_count: int, last_inbound_seq: int, summary_seq: int | None,
    trigger_messages: int, min_between: int, max_per_day: int, day_count: int,
) -> str | None:
    """Return None to proceed, or a skip reason. Enforces the trigger, the
    min-messages-between ceiling, and the daily cap (the three skip ceilings)."""
    if message_count <= trigger_messages:
        return "below_trigger"
    if summary_seq is not None and (last_inbound_seq - summary_seq) < min_between:
        return "min_between"
    if day_count >= max_per_day:
        return "daily_cap"
    return None


def build_summary_input(
    messages: list[tuple[int, str, str]], existing_summary: str | None,
    max_messages: int, max_chars: int,
) -> str:
    """[existing summary] + last `max_messages` messages, every part phone-masked,
    total hard-capped to max_chars by cutting from the OLDEST end (H43/H41)."""
    parts: list[str] = []
    if existing_summary:
        parts.append(mask_phones(existing_summary))
    for _seq, _direction, body in messages[-max_messages:]:
        if body:
            parts.append(mask_phones(body))
    joined = "\n".join(parts)
    if len(joined) > max_chars:
        joined = joined[-max_chars:]
    return joined


def cap_summary_output(text: str, max_chars: int) -> str:
    """Truncate the stored summary, whatever the model returned (H4 hard cap)."""
    return text[:max_chars]


def _process_one(
    settings: WorkerSettings, handle: SummaryHandle,
    conversation_id: uuid.UUID, tenant_id: uuid.UUID,
) -> str | None:
    """Summarize one conversation. Returns None on success, or a skip/error reason.
    H40: read (short tx) => provider call (outside tx) => write+account (short tx)."""
    from app.llm import budget
    from app.llm.port import LlmProviderError

    # Phase A: read state + messages + budget check (one short tx).
    try:
        with core_db.tenant_tx(tenant_id) as conn:
            state = repos_summary.read_summary_state(
                conn, tenant_id=tenant_id, conversation_id=conversation_id,
            )
            if state is None:
                return "no_messages"
            existing_summary, slots, last_inbound_seq, message_seq = state
            summary_seq = slots.get("summary_seq")
            day_count = repos_summary.count_summaries_today(
                conn, conversation_id=conversation_id,
            )
            messages = repos_summary.read_recent_messages(
                conn, conversation_id=conversation_id,
                limit=settings.summary_input_messages,
            )
            budget_state = budget.ensure_and_check(
                conn, tenant_id=tenant_id, month=budget.current_month(),
                limit_micro_usd=settings.tenant_monthly_budget_micro_usd,
            )
    except Exception as exc:  # noqa: BLE001 - a read failure must not crash the thread
        obs_logging.log_event(_log, event="summary.read_failed", component="summary",
                              level=logging.ERROR, error=str(exc))
        return "error"

    if budget_state == "degraded":
        return "budget_degraded"

    decision = summary_decision(
        message_count=message_seq, last_inbound_seq=last_inbound_seq,
        summary_seq=summary_seq,
        trigger_messages=settings.summary_trigger_messages,
        min_between=settings.summary_min_messages_between,
        max_per_day=settings.summary_max_per_conversation_per_day,
        day_count=day_count,
    )
    if decision is not None:
        return decision

    # Phase B: build input + provider call (OUTSIDE any transaction, H40).
    prompt = build_summary_input(
        messages=messages, existing_summary=existing_summary,
        max_messages=settings.summary_input_messages,
        max_chars=settings.summary_input_max_chars,
    )
    if not handle.breaker.allow():
        return "breaker_open"
    try:
        result = handle.provider.complete_text(
            system=SUMMARY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            max_output_tokens=settings.summary_max_output_tokens,
            timeout_s=settings.llm_timeout_s,
        )
    except LlmProviderError:
        handle.breaker.record_failure()
        return "error"
    except Exception as exc:  # noqa: BLE001 - a provider bug must not crash the thread
        handle.breaker.record_failure()
        obs_logging.log_event(_log, event="summary.provider_error", component="summary",
                              level=logging.ERROR, error=str(exc))
        return "error"
    handle.breaker.record_success()

    # Phase C: mask output AGAIN (H43), cap, write + account (short tx).
    stored = cap_summary_output(mask_phones(result.text), settings.summary_max_chars)
    try:
        with core_db.tenant_tx(tenant_id) as conn:
            repos_summary.update_summary(
                conn, tenant_id=tenant_id, conversation_id=conversation_id,
                summary=stored, slots={"summary_seq": last_inbound_seq},
            )
            budget.account(
                conn, tenant_id=tenant_id, month=budget.current_month(),
                conversation_id=conversation_id, purpose="summary",
                provider=result.usage.provider, model=result.usage.model,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                cost_micro_usd=budget.compute_cost_micro_usd(
                    result.usage.input_tokens, result.usage.output_tokens,
                    settings.llm_price_table, result.usage.provider, result.usage.model,
                ),
                latency_ms=result.usage.latency_ms, status="ok",
            )
        metrics.llm_calls_total.labels("summary", result.usage.provider, "ok").inc()
        metrics.summary_output_chars.observe(len(stored))
    except Exception as exc:  # noqa: BLE001 - a write failure must not crash the thread
        obs_logging.log_event(_log, event="summary.write_failed", component="summary",
                              level=logging.ERROR, error=str(exc))
        return "error"
    return None


def summary_once(*, settings: WorkerSettings, handle: SummaryHandle) -> str:
    """One bounded cycle over up to SUMMARY_MAX_PER_CYCLE conversations. Returns
    'ok' | 'error' for the cycle itself."""
    try:
        with core_db.system_tx() as conn:
            candidates = repos_summary.list_conversations_needing_summary(
                conn, limit=settings.summary_max_per_cycle,
                trigger_messages=settings.summary_trigger_messages,
                min_between=settings.summary_min_messages_between,
                max_per_day=settings.summary_max_per_conversation_per_day,
            )
    except Exception as exc:  # noqa: BLE001 - a transient DB error must not crash the thread
        obs_logging.log_event(_log, event="summary.list_failed", component="summary",
                              level=logging.ERROR, error=str(exc))
        metrics.summary_runs_total.labels("error").inc()
        return "error"

    for conversation_id, tenant_id in candidates:
        reason = _process_one(settings, handle, conversation_id, tenant_id)
        if reason is None:
            continue
        if reason in SKIP_REASONS:
            metrics.summary_skipped_total.labels(reason).inc()
        else:
            metrics.summary_skipped_total.labels("error").inc()

    metrics.summary_runs_total.labels("ok").inc()
    return "ok"


