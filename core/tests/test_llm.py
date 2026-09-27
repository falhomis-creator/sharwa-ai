"""Pure P1.5 LLM-layer tests (PROMPT §6: M3/M4/M5/M7/M9/M10/M13) - no DB/network.

The DB-transaction guarantees (H40 accounting, M6/M8, M12) need real Postgres and
are recorded as debt (M16). Everything here is pure: schema validation, the
budget ladder, cost math, the circuit breaker, H41 masking, composition (H38),
and the production fake-provider guard.
"""
from __future__ import annotations

import pytest

from app.config import ConfigError
from app.llm.breaker import BreakerState, CircuitBreaker
from app.llm.budget import compute_budget_state, compute_cost_micro_usd
from app.llm.router import (
    build_router_messages,
    mask_phones,
    parse_router_json,
    run_router,
)
from app.workers.compose import (
    MAX_POLICY_CHARS,
    compose_policy_answer,
    compose_product_list,
)
from app.workers.config import validate_llm_provider
from tests.fake_llm import FakeLlmProvider, classify_text


# --- M4: strict router schema ------------------------------------------------


def test_parse_valid_json():
    d = parse_router_json({"intent": "product_search", "query": "قميص", "confidence": 0.9})
    assert d.intent == "product_search"
    assert d.query == "قميص"
    assert d.confidence == 0.9


def test_parse_unknown_intent_is_other():
    assert parse_router_json({"intent": "unknown", "query": "x", "confidence": 0.9}).intent == "other"


def test_parse_missing_confidence_is_other():
    assert parse_router_json({"intent": "product_search", "query": "x"}).intent == "other"


def test_parse_string_confidence_is_other():
    assert parse_router_json({"intent": "product_search", "query": "x", "confidence": "high"}).intent == "other"


def test_parse_extra_fields_are_ignored():
    d = parse_router_json({"intent": "policy_question", "query": "", "confidence": 0.9, "extra": 1})
    assert d.intent == "policy_question"


def test_parse_corrupt_input_is_other():
    assert parse_router_json(["not", "a", "dict"]).intent == "other"
    assert parse_router_json("just text").intent == "other"


# --- M5: budget ladder -------------------------------------------------------


def test_budget_ladder_boundaries():
    limit = 20_000_000
    assert compute_budget_state(15_800_000, limit) == "ok"        # 79%
    assert compute_budget_state(16_000_000, limit) == "warn_80"   # 80%
    assert compute_budget_state(20_000_000, limit) == "degraded"  # 100%
    assert compute_budget_state(20_000_001, limit) == "degraded"
    assert compute_budget_state(0, 0) == "degraded"               # zero limit


def test_cost_math():
    table = {"fake": {"fake-router": {"input": 10, "output": 20}}}  # micro-USD per 1k tokens
    assert compute_cost_micro_usd(1000, 1000, table, "fake", "fake-router") == 30
    assert compute_cost_micro_usd(1000, 1000, {}, "unknown", "m") == 0


# --- M7: circuit breaker -----------------------------------------------------


def test_breaker_opens_after_threshold():
    b = CircuitBreaker(fail_threshold=5, reset_s=60.0)
    for _ in range(5):
        b.record_failure()
    assert b.state() == BreakerState.OPEN
    assert not b.allow()
    assert b.state() == BreakerState.OPEN


def test_breaker_recovers_after_reset_window(monkeypatch):
    import time
    b = CircuitBreaker(fail_threshold=5, reset_s=0.05)
    for _ in range(5):
        b.record_failure()
    assert not b.allow()  # open
    time.sleep(0.06)
    assert b.allow()      # half-open trial
    b.record_success()
    assert b.state() == BreakerState.CLOSED


# --- M9 / H41: masking -------------------------------------------------------


def test_mask_phones_keeps_last_three_digits():
    assert mask_phones("اتصل على 967123456789") == "اتصل على ***789"
    assert mask_phones("my number 1234567") == "my number ***567"


def test_build_router_messages_masks_and_has_no_identifier():
    msgs = build_router_messages(["رقمي 967123456789"], 1000)
    assert msgs == [{"role": "user", "content": "رقمي ***789"}]


# --- M10 / H38: composition --------------------------------------------------


def test_compose_product_list_has_no_price_or_stock():
    text = compose_product_list([{"title": "قميص قطني", "price_hint_minor": 100, "stock_hint": 5, "currency": "SAR"}])
    assert "قميص قطني" in text
    assert "100" not in text
    assert "SAR" not in text
    assert "price_hint_minor" not in text


def test_compose_policy_answer_is_verbatim_and_capped():
    content = "سياسة الاسترجاع: خلال 14 يوم."
    assert compose_policy_answer(content) == content
    assert len(compose_policy_answer("x" * 5000)) == MAX_POLICY_CHARS


# --- M13: fake provider never reaches production -----------------------------


def test_fake_provider_rejected_in_production():
    with pytest.raises(ConfigError):
        validate_llm_provider("production", "fake")
    validate_llm_provider("test", "fake")          # no raise
    validate_llm_provider("production", "real")    # a future real provider


# --- Router integration with the fake provider -------------------------------


def test_run_router_classifies_product_search():
    provider = FakeLlmProvider()
    breaker = CircuitBreaker(5, 60.0)
    result = run_router(
        provider, breaker, provider_name="fake",
        messages=[{"role": "user", "content": "عندكم قميص؟"}],
        min_confidence=0.6, max_output_tokens=64, timeout_s=8.0,
    )
    assert result.status == "ok"
    assert result.decision.intent == "product_search"
    assert result.usage is not None


def test_fake_classify_rules():
    assert classify_text("سعر المنتج")[0] == "product_search"
    assert classify_text("ما سياسة الاسترجاع")[0] == "policy_question"
    assert classify_text("أريد موظف")[0] == "handoff_request"
    assert classify_text("شكراً")[0] == "other"
