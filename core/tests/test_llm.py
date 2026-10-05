"""Pure P1.5 LLM-layer tests (PROMPT §6: M3/M4/M5/M7/M9/M10/M13) - no DB/network.

The DB-transaction guarantees (H40 accounting, M6/M8, M12) need real Postgres and
are recorded as debt (M16). Everything here is pure: schema validation, the
budget ladder, cost math, the circuit breaker, H41 masking, composition (H38),
and the production fake-provider guard.
"""
from __future__ import annotations

import httpx2
import openai
import pytest

from app.config import ConfigError
from app.llm.adapters.deepseek import DeepSeekProvider
from app.llm.breaker import BreakerState, CircuitBreaker
from app.llm.budget import compute_budget_state, compute_cost_micro_usd
from app.llm.port import LlmProviderError, LlmTimeoutError
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
    validate_llm_provider("production", "deepseek")  # P4.2: the real provider


def test_unknown_provider_rejected_in_production():
    # P4.2: a name without a real adapter must stop the boot in production (a
    # typo must refuse, not silently fall back); non-production passes it
    # through to the registry's own refusal.
    with pytest.raises(ConfigError):
        validate_llm_provider("production", "not-a-provider")
    validate_llm_provider("test", "not-a-provider")  # no raise (registry refuses later)


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


# --- P4.2: the real DeepSeek adapter (recorded stub client, no network) --------


class _DeepSeekSettings:
    """The three WorkerSettings fields the adapter's build() reads."""

    llm_api_key = "sk-test-deepseek"
    llm_base_url = "https://api.deepseek.com"
    llm_model = "deepseek-chat"


class _StubCompletions:
    def __init__(
        self, *, content: str, error: Exception | None = None,
        input_tokens: int = 12, output_tokens: int = 7,
    ) -> None:
        self._content = content
        self._error = error
        self._in_toks = input_tokens
        self._out_toks = output_tokens
        self.kwargs: dict[str, object] | None = None

    def create(self, **kwargs: object):
        self.kwargs = kwargs
        if self._error is not None:
            raise self._error
        from types import SimpleNamespace
        usage = SimpleNamespace(prompt_tokens=self._in_toks, completion_tokens=self._out_toks)
        message = SimpleNamespace(content=self._content)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)], usage=usage, model="deepseek-chat",
        )


class _StubClient:
    def __init__(self, completions: _StubCompletions) -> None:
        from types import SimpleNamespace
        self.chat = SimpleNamespace(completions=completions)


def _ds(stub: _StubCompletions) -> DeepSeekProvider:
    return DeepSeekProvider(api_key="sk-test", client=_StubClient(stub))


def test_deepseek_complete_json_parses_and_accounts():
    stub = _StubCompletions(content='{"intent": "product_search", "query": "قميص", "confidence": 0.9}')
    result = _ds(stub).complete_json(
        system="أعد JSON حصراً", messages=[{"role": "user", "content": "عندكم قميص؟"}],
        schema_name="router", max_output_tokens=64, timeout_s=8.0,
    )
    assert result.data["intent"] == "product_search"
    assert result.data["confidence"] == 0.9
    assert result.usage.provider == "deepseek"
    assert result.usage.model == "deepseek-chat"
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 7
    assert result.usage.latency_ms >= 0
    assert stub.kwargs is not None
    assert stub.kwargs["response_format"] == {"type": "json_object"}
    assert stub.kwargs["model"] == "deepseek-chat"
    assert stub.kwargs["max_tokens"] == 64
    assert stub.kwargs["timeout"] == 8.0
    assert stub.kwargs["temperature"] == 0.0
    assert stub.kwargs["messages"][0]["role"] == "system"


def test_deepseek_complete_json_invalid_json_is_empty_data():
    # M4: any malformation => upstream 'other'; the adapter NEVER re-calls and
    # never raises on unparseable content (the tokens are still accounted).
    stub = _StubCompletions(content="ليس JSON")
    result = _ds(stub).complete_json(
        system="أعد JSON", messages=[{"role": "user", "content": "x"}],
        schema_name="router", max_output_tokens=64, timeout_s=8.0,
    )
    assert result.data == {}
    assert result.usage.output_tokens == 7


def test_deepseek_complete_json_non_dict_is_empty_data():
    stub = _StubCompletions(content='["not", "a", "dict"]')
    result = _ds(stub).complete_json(
        system="أعد JSON", messages=[{"role": "user", "content": "x"}],
        schema_name="router", max_output_tokens=64, timeout_s=8.0,
    )
    assert result.data == {}


def test_deepseek_complete_text_echoes_content():
    stub = _StubCompletions(content="ملخص المحادثة")
    result = _ds(stub).complete_text(
        system="لخّص", messages=[{"role": "user", "content": "المحادثة"}],
        max_output_tokens=400, timeout_s=8.0,
    )
    assert result.text == "ملخص المحادثة"
    assert stub.kwargs is not None
    assert "response_format" not in stub.kwargs  # JSON mode is complete_json-only


def test_deepseek_timeout_maps_to_port_timeout():
    request = httpx2.Request("POST", "https://api.deepseek.com/chat/completions")
    stub = _StubCompletions(content="", error=openai.APITimeoutError(request=request))
    with pytest.raises(LlmTimeoutError):
        _ds(stub).complete_text(
            system="s", messages=[{"role": "user", "content": "x"}],
            max_output_tokens=10, timeout_s=1.0,
        )


def test_deepseek_5xx_maps_to_transient_provider_error():
    request = httpx2.Request("POST", "https://api.deepseek.com/chat/completions")
    response = httpx2.Response(500, request=request)
    stub = _StubCompletions(
        content="", error=openai.InternalServerError("boom", response=response, body=None),
    )
    with pytest.raises(LlmProviderError):
        _ds(stub).complete_text(
            system="s", messages=[{"role": "user", "content": "x"}],
            max_output_tokens=10, timeout_s=1.0,
        )


def test_deepseek_empty_key_refuses_construction():
    with pytest.raises(RuntimeError):
        DeepSeekProvider(api_key="")


def test_registry_builds_deepseek_and_refuses_unknown():
    from app.llm import registry

    provider = registry.build_provider("deepseek", _DeepSeekSettings())
    assert isinstance(provider, DeepSeekProvider)
    with pytest.raises(RuntimeError):
        registry.build_provider("no-such-provider", _DeepSeekSettings())


def test_cost_math_fractional_deepseek_rates():
    # P4.2: deepseek-chat peak rates are sub-micro-USD per 1k tokens; the
    # budget math must not truncate them to zero (input 0.3, output 1.2).
    table = {"deepseek": {"deepseek-chat": {"input": 0.3, "output": 1.2}}}
    assert compute_cost_micro_usd(1000, 1000, table, "deepseek", "deepseek-chat") == 1
    assert compute_cost_micro_usd(10_000, 10_000, table, "deepseek", "deepseek-chat") == 15
    assert compute_cost_micro_usd(1000, 1000, table, "deepseek", "no-such-model") == 0
