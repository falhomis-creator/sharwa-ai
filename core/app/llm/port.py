"""core/app/llm/port.py - LlmProvider, the single LLM abstraction (L1).

Designed so a future provider adapter can translate these parameters into its
own wire shape WITHOUT any caller change: every provider takes a system prompt +
role-tagged messages + an output token cap + a timeout, and returns parsed JSON
plus token usage. Nothing provider-specific (tools, response_format, beta
headers) belongs here - those are adapter details, and the adapters/ package
stays empty until the first real adapter (H1: no dead code).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class LlmUsage:
    input_tokens: int
    output_tokens: int
    model: str
    provider: str
    latency_ms: int


@dataclass(frozen=True)
class LlmJsonResult:
    data: dict[str, Any]       # parsed JSON, not yet validated
    usage: LlmUsage


@dataclass(frozen=True)
class LlmTextResult:
    text: str                  # free text (the rolling summary), already masked by caller
    usage: LlmUsage


class LlmProviderError(Exception):
    """Transient provider failure (network/5xx) - one retry is allowed."""


class LlmTimeoutError(LlmProviderError):
    """The provider call exceeded timeout_s."""


class LlmProvider(Protocol):
    def complete_json(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        schema_name: str,
        max_output_tokens: int,
        timeout_s: float,
    ) -> LlmJsonResult: ...

    def complete_text(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        max_output_tokens: int,
        timeout_s: float,
    ) -> LlmTextResult: ...


# --- P1.5b embedding interface (V1) -------------------------------------------


@dataclass(frozen=True)
class EmbeddingResult:
    """One batch of vectors, in the SAME order as the input texts."""

    vectors: list[list[float]]
    usage: LlmUsage


class EmbeddingProviderError(Exception):
    """Transient embedding-provider failure (network/5xx)."""


class EmbeddingTimeoutError(EmbeddingProviderError):
    """The embedding call exceeded timeout_s."""


class EmbeddingProvider(Protocol):
    """A batched text-embedding provider. Every real provider supports embedding
    a batch of texts in one call, which is exactly what makes the P1.5b batch
    embed worker possible without one paid call per product."""

    def embed(self, *, texts: list[str], timeout_s: float) -> EmbeddingResult: ...
