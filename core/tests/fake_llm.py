"""core/tests/fake_llm.py - FakeLlmProvider: deterministic, no-network LLM (L3).

Matches written keyword rules and returns the strict router JSON plus an
ESTIMATED usage (total chars // 4) - documented as an estimate, not a
measurement. Deterministic: fixed input -> fixed output, no network, no SDK.
"""
from __future__ import annotations

import json
from typing import Any

from app.llm.port import LlmJsonResult, LlmProvider, LlmTextResult, LlmUsage

_PROVIDER = "fake"
_MODEL = "fake-router"


def _rules() -> tuple[tuple[str, tuple[str, ...]], ...]:
    return (
        ("handoff_request", ("موظف", "شخص حقيقي", "بشري", "ممثل", "خدمة العملاء", "human", "agent", "representative")),
        ("policy_question", ("استرجاع", "ارجاع", "شحن", "توصيل", "سياسة", "return", "refund", "shipping", "policy")),
        ("product_search", ("عندكم", "متوفر", "سعر", "منتج", "product", "price", "available", "لديكم", "ابحث")),
    )


def classify_text(text: str) -> tuple[str, str]:
    """Deterministic intent classification used by the fake provider. Exposed so
    tests can assert the fake's routing rules directly."""
    lowered = text.casefold()
    for intent, keywords in _rules():
        for kw in keywords:
            if kw.casefold() in lowered:
                query = text[:80] if intent == "product_search" else ""
                return intent, query
    return "other", ""


class FakeLlmProvider(LlmProvider):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def complete_json(
        self, *, system: str, messages: list[dict[str, str]], schema_name: str,
        max_output_tokens: int, timeout_s: float,
    ) -> LlmJsonResult:
        joined = " ".join(m.get("content", "") for m in messages)
        intent, query = classify_text(joined)
        data: dict[str, Any] = {"intent": intent, "query": query, "confidence": 0.9}
        self.calls.append(data)
        # Estimate only (documented): chars // 4, not a real tokenizer.
        usage = LlmUsage(
            input_tokens=(len(system) + len(joined)) // 4,
            output_tokens=len(json.dumps(data, ensure_ascii=False)) // 4,
            model=_MODEL, provider=_PROVIDER, latency_ms=1,
        )
        return LlmJsonResult(data=data, usage=usage)

    def complete_text(
        self, *, system: str, messages: list[dict[str, str]],
        max_output_tokens: int, timeout_s: float,
    ) -> LlmTextResult:
        """Deterministic fake summarizer (P1.5b V6): a fixed truncated echo of the
        input. Not a semantic model - a test approximation only, like complete_json."""
        joined = " ".join(m.get("content", "") for m in messages)
        text = "ملخص: " + joined[:200]
        self.calls.append({"summary": text})
        usage = LlmUsage(
            input_tokens=(len(system) + len(joined)) // 4,
            output_tokens=len(text) // 4,
            model=_MODEL, provider=_PROVIDER, latency_ms=1,
        )
        return LlmTextResult(text=text, usage=usage)
