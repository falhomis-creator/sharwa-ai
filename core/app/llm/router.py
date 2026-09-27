"""core/app/llm/router.py - the deterministic router (L6, H38).

The model is asked to classify intent and return STRICT JSON {intent, query,
confidence}. Whatever the model returns is validated against a pydantic schema;
any malformation => 'other' (never re-called, never shown). The extracted query
is passed ONLY to search_products as a parameter (H19): it is never logged and
never sent to a customer.

H41: before any text reaches the provider, phone runs are masked to their last 3
digits, and no internal identifier is ever included.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from app.llm.breaker import BreakerState, CircuitBreaker
from app.llm.port import LlmJsonResult, LlmProvider, LlmProviderError
from app.obs import metrics
from app.text.redact import mask_phones  # noqa: F401 - re-exported for H21 back-compat (P1.6 C6)

INTENTS = ("product_search", "policy_question", "handoff_request", "other")

# H19: fixed Arabic system prompt - the task and output only. No tenant data, no
# product names, and nothing the customer's text could turn into an instruction.
ROUTER_SYSTEM_PROMPT = (
    "أنت موجِّه رسائل لخدمة عملاء متجر. حدِّد نية رسالة العميل إلى واحدة فقط، "
    "وأعد JSON حصراً بهذا الشكل بلا أي نص إضافي: "
    '{"intent": "product_search|policy_question|handoff_request|other", '
    '"query": "نص البحث المستخرج أو سلسلة فارغة", "confidence": 0.0}'
)

def build_router_messages(texts: list[str], max_input_chars: int) -> list[dict[str, str]]:
    """Last-N text messages, phone-masked and length-capped (H41)."""
    messages: list[dict[str, str]] = []
    total = 0
    for text in reversed(texts):
        if not text:
            continue
        masked = mask_phones(text)
        if total + len(masked) > max_input_chars:
            break
        messages.append({"role": "user", "content": masked})
        total += len(masked)
    messages.reverse()
    return messages


@dataclass(frozen=True)
class RouterDecision:
    intent: str  # one of INTENTS
    query: str
    confidence: float


class _RouterResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    intent: Literal["product_search", "policy_question", "handoff_request", "other"]
    query: str
    confidence: float


_OTHER = RouterDecision(intent="other", query="", confidence=0.0)


def parse_router_json(data: Any) -> RouterDecision:
    """Strict schema validation (M4): any malformation (bad type, unknown intent,
    missing/string confidence, non-object/corrupt) => 'other'. Never raises,
    never re-calls."""
    try:
        parsed = _RouterResponse.model_validate(data)
    except (ValidationError, TypeError, ValueError):
        metrics.router_invalid_total.inc()
        return _OTHER
    return RouterDecision(intent=parsed.intent, query=parsed.query, confidence=parsed.confidence)


@dataclass(frozen=True)
class RouteResult:
    status: str  # ok | error | timeout | breaker_open
    decision: RouterDecision
    usage: LlmJsonResult | None


def _state_int(breaker: CircuitBreaker) -> int:
    return {BreakerState.CLOSED: 0, BreakerState.OPEN: 1, BreakerState.HALF_OPEN: 2}[breaker.state()]


def run_router(
    provider: LlmProvider,
    breaker: CircuitBreaker,
    *,
    provider_name: str,
    messages: list[dict[str, str]],
    min_confidence: float,
    max_output_tokens: int,
    timeout_s: float,
) -> RouteResult:
    """One router call, guarded by the breaker, with ONE retry on a transient
    error (H39). The network call happens OUTSIDE any DB transaction (H40) - this
    function is called by turn.py between two short transactions."""
    def call() -> LlmJsonResult:
        return provider.complete_json(
            system=ROUTER_SYSTEM_PROMPT, messages=messages, schema_name="router",
            max_output_tokens=max_output_tokens, timeout_s=timeout_s,
        )

    if not breaker.allow():
        metrics.llm_breaker_state.labels(provider_name).set(1)
        return RouteResult(status="breaker_open", decision=_OTHER, usage=None)

    result: LlmJsonResult | None = None
    status = "error"
    try:
        result = call()
    except LlmProviderError:
        breaker.record_failure()
        status = "error"

    if result is None:
        # One retry for a transient error, then templates-only.
        if not breaker.allow():
            metrics.llm_breaker_state.labels(provider_name).set(1)
            return RouteResult(status="breaker_open", decision=_OTHER, usage=None)
        try:
            result = call()
        except LlmProviderError:
            breaker.record_failure()
            metrics.llm_breaker_state.labels(provider_name).set(_state_int(breaker))
            return RouteResult(status=status, decision=_OTHER, usage=None)

    if result is None:
        return RouteResult(status=status, decision=_OTHER, usage=None)

    breaker.record_success()
    metrics.llm_breaker_state.labels(provider_name).set(_state_int(breaker))

    decision = parse_router_json(result.data)
    if decision.intent != "other" and decision.confidence < min_confidence:
        metrics.router_low_confidence_total.inc()
        decision = _OTHER
    metrics.router_intents_total.labels(decision.intent).inc()
    return RouteResult(status="ok", decision=decision, usage=result)
