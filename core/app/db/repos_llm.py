"""core/app/db/repos_llm.py - the LLM governance raw SQL (P1.5 L9).

llm_calls and tenant_budgets already exist in 0001_baseline.sql with their
columns. The SQL below is the ONLY place core/ writes those tables (H2), so
callers in app/llm/budget.py pass an already-open connection from tenant_tx()
and never see a SQL string or a psycopg import themselves.
"""
from __future__ import annotations

import datetime
import uuid

import psycopg


def ensure_month_budget(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, month: datetime.date,
    limit_micro_usd: int,
) -> tuple[int, int]:
    """(used_micro_usd, limit_micro_usd) for this tenant/month, creating the row
    with the configured limit on first use (ON CONFLICT DO NOTHING - idempotent)."""
    conn.execute(
        "INSERT INTO tenant_budgets (tenant_id, month, limit_micro_usd) "
        "VALUES (%s, %s, %s) ON CONFLICT (tenant_id, month) DO NOTHING",
        (tenant_id, month, limit_micro_usd),
    )
    row = conn.execute(
        "SELECT used_micro_usd, limit_micro_usd FROM tenant_budgets "
        "WHERE tenant_id = %s AND month = %s",
        (tenant_id, month),
    ).fetchone()
    if row is None:
        raise RuntimeError("tenant_budgets row vanished after ensure")
    return int(row[0]), int(row[1])


def get_budget(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, month: datetime.date,
) -> tuple[int, int]:
    """(used_micro_usd, limit_micro_usd) for an existing month row."""
    row = conn.execute(
        "SELECT used_micro_usd, limit_micro_usd FROM tenant_budgets "
        "WHERE tenant_id = %s AND month = %s",
        (tenant_id, month),
    ).fetchone()
    if row is None:
        raise RuntimeError("tenant_budgets row missing at accounting time")
    return int(row[0]), int(row[1])


def record_llm_call(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID | None,
    purpose: str, provider: str, model: str, input_tokens: int, output_tokens: int,
    cost_micro_usd: int, latency_ms: int | None, status: str,
) -> None:
    """One immutable row in llm_calls (success OR failure - M6). sharwa_app has
    INSERT/SELECT but never UPDATE/DELETE on llm_calls (0001 REVOKE)."""
    conn.execute(
        "INSERT INTO llm_calls "
        "(tenant_id, conversation_id, purpose, provider, model, input_tokens, "
        " output_tokens, cost_micro_usd, latency_ms, status) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            tenant_id, conversation_id, purpose, provider, model,
            input_tokens, output_tokens, cost_micro_usd, latency_ms, status,
        ),
    )


def apply_budget_usage(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, month: datetime.date,
    cost_micro_usd: int, new_state: str,
) -> int:
    """Add the cost and set the recomputed state; returns the new used_micro_usd."""
    row = conn.execute(
        "UPDATE tenant_budgets SET used_micro_usd = used_micro_usd + %s, state = %s "
        "WHERE tenant_id = %s AND month = %s RETURNING used_micro_usd",
        (cost_micro_usd, new_state, tenant_id, month),
    ).fetchone()
    if row is None:
        raise RuntimeError("tenant_budgets row vanished during accounting")
    return int(row[0])


def budget_state_counts(conn: psycopg.Connection) -> list[tuple[str, int]]:
    """(state, count) across all tenants, via the SECURITY DEFINER function
    app.budget_state_counts() (0008). Called on a system_tx() connection."""
    rows = conn.execute("SELECT state, count FROM app.budget_state_counts()").fetchall()
    return [(r[0], int(r[1])) for r in rows]
