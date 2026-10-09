"""core/app/llm/budget.py - budget governance (L4, owner decision #3).

20 USD/month hard limit = 20_000_000 micro-USD per tenant, warn at 80%, degrade
at 100% (templates-only, zero model calls), exhausted reserved for a manual
owner stop (never transitioned automatically; read as 'degraded').

The state/cost math is PURE here (table-testable, M5); the SQL lives in
app/db/repos_llm.py. The check runs BEFORE the provider call and the accounting
AFTER, in two short tenant transactions (H39/H40).
"""
from __future__ import annotations

import datetime
import uuid
from typing import Any

from app.db import repos_llm

# The month key is "YYYY-MM" (day-1 date) - deterministic and indexable.
_WARN_RATIO = 0.8


def current_month() -> datetime.date:
    today = datetime.date.today()
    return datetime.date(today.year, today.month, 1)


def compute_budget_state(used_micro_usd: int, limit_micro_usd: int) -> str:
    """ok | warn_80 | degraded, from the owner's numbers (M5)."""
    if limit_micro_usd <= 0:
        return "degraded"
    if used_micro_usd >= limit_micro_usd:
        return "degraded"
    if used_micro_usd >= int(limit_micro_usd * _WARN_RATIO):
        return "warn_80"
    return "ok"


def compute_cost_micro_usd(
    input_tokens: int, output_tokens: int, price_table: dict[str, Any],
    provider: str, model: str,
) -> int:
    """micro-USD from the (provider, model) price table: micro-USD per 1k tokens
    in and out. Unknown provider/model => 0 (fake/local providers are free by
    default). Rates may be floats; the result is an int (floor). F-P4-14: $X per
    1M tokens = X*1000 micro-USD per 1k (DEFAULT_LLM_PRICE_TABLE in
    app/workers/config.py)."""
    entry = price_table.get(provider, {}).get(model)
    if entry is None:
        return 0
    input_rate = float(entry.get("input", 0))
    output_rate = float(entry.get("output", 0))
    return int((input_tokens * input_rate + output_tokens * output_rate) // 1000)


def ensure_and_check(
    conn: Any, *, tenant_id: uuid.UUID, month: datetime.date, limit_micro_usd: int,
) -> str:
    """Read/ensure the month row and return its state (before the call)."""
    used, limit = repos_llm.ensure_month_budget(
        conn, tenant_id=tenant_id, month=month, limit_micro_usd=limit_micro_usd,
    )
    return compute_budget_state(used, limit)


def account(
    conn: Any, *, tenant_id: uuid.UUID, month: datetime.date,
    conversation_id: uuid.UUID | None, purpose: str, provider: str, model: str,
    input_tokens: int, output_tokens: int, cost_micro_usd: int,
    latency_ms: int | None, status: str,
) -> str:
    """Record the call (success OR failure, M6) and update the budget (after).

    Zero-cost failure rows still land in llm_calls with status='error|timeout|
    breaker_open' but no token/cost (a call that never happened is never billed).
    """
    used, limit = repos_llm.get_budget(conn, tenant_id=tenant_id, month=month)
    new_state = compute_budget_state(used + cost_micro_usd, limit)
    repos_llm.record_llm_call(
        conn, tenant_id=tenant_id, conversation_id=conversation_id, purpose=purpose,
        provider=provider, model=model, input_tokens=input_tokens,
        output_tokens=output_tokens, cost_micro_usd=cost_micro_usd,
        latency_ms=latency_ms, status=status,
    )
    repos_llm.apply_budget_usage(
        conn, tenant_id=tenant_id, month=month,
        cost_micro_usd=cost_micro_usd, new_state=new_state,
    )
    return new_state
