"""core/app/llm/adapters/openai_embedding.py - the real (paid) embedding
provider (P4 Task 15, owner decision 2026-10-09).

DeepSeek - the engine's only LLM - exposes no embeddings endpoint, so semantic
search gets its own provider: OpenAI embeddings, default model
``text-embedding-3-small`` (multilingual, including Arabic). OpenAI stays
EXCLUDED as the LLM (P4.2); this adapter only turns text into vectors and
never produces customer-facing text (H38).

  * H44: the vector column is vector(1024). The text-embedding-3 models accept
    a ``dimensions`` request parameter (native width 1536 for -small), so the
    adapter ALWAYS asks for exactly settings.embedding_dim (=1024) and the
    caller still checks every vector's length before any write.
  * H39: the SDK's own retry loop is disabled (max_retries=0); SDK errors map
    onto the port's EmbeddingProviderError / EmbeddingTimeoutError so the
    caller's circuit breaker and the H42 fail-open stay in charge.
  * The API rejects an empty input string, so an empty/blank text gets the
    same zero vector the local provider gives it, without a paid call.
  * ``model_name`` is what the caller stores with every product vector and
    scopes the query cache and the vector search by (F-P4-13): vectors from
    two different models are never compared.
"""
from __future__ import annotations

import time
from typing import Any

import openai

from app.llm.port import (
    EmbeddingProvider,
    EmbeddingProviderError,
    EmbeddingResult,
    EmbeddingTimeoutError,
    LlmUsage,
)

PROVIDER_NAME = "openai"
DEFAULT_MODEL = "text-embedding-3-small"
DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Batched embeddings through the official SDK, width pinned to ``dim``."""

    def __init__(
        self, *, api_key: str, dim: int, model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL, client: Any = None,
    ) -> None:
        if not api_key:
            # Defensive only - app/workers/config.py already refuses an empty
            # key at boot (H5) when EMBEDDING_PROVIDER selects this adapter.
            raise RuntimeError("the openai embedding provider requires a non-empty OPENAI_API_KEY")
        self.model_name = model
        self._dim = dim
        # max_retries=0: retries are OURS (H39 - the breaker decides).
        self._client = client if client is not None else openai.OpenAI(
            api_key=api_key, base_url=base_url, max_retries=0,
        )

    def embed(self, *, texts: list[str], timeout_s: float) -> EmbeddingResult:
        started = time.monotonic()
        vectors: list[list[float]] = [[0.0] * self._dim for _ in texts]
        to_send = [(i, t) for i, t in enumerate(texts) if t.strip()]
        input_tokens = 0
        if to_send:
            response = self._create([t for _i, t in to_send], timeout_s)
            data = sorted(response.data, key=lambda d: d.index)
            if len(data) != len(to_send):
                raise EmbeddingProviderError(
                    f"openai: {len(data)} vectors for {len(to_send)} texts"
                )
            for (i, _t), item in zip(to_send, data, strict=True):
                vectors[i] = [float(x) for x in item.embedding]
            usage = getattr(response, "usage", None)
            input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        return EmbeddingResult(
            vectors=vectors,
            usage=LlmUsage(
                input_tokens=input_tokens, output_tokens=0, model=self.model_name,
                provider=PROVIDER_NAME,
                latency_ms=int((time.monotonic() - started) * 1000),
            ),
        )

    def _create(self, inputs: list[str], timeout_s: float) -> Any:
        try:
            return self._client.embeddings.create(
                model=self.model_name, input=inputs, dimensions=self._dim,
                encoding_format="float", timeout=timeout_s,
            )
        except openai.APITimeoutError as exc:
            raise EmbeddingTimeoutError(f"openai: no response within {timeout_s}s") from exc
        except openai.APIConnectionError as exc:  # connection reset / DNS / refused
            raise EmbeddingProviderError("openai: connection error") from exc
        except openai.RateLimitError as exc:
            raise EmbeddingProviderError("openai: rate limited (429)") from exc
        except openai.InternalServerError as exc:
            raise EmbeddingProviderError("openai: server error (5xx)") from exc
        except openai.APIStatusError as exc:
            raise EmbeddingProviderError(f"openai: http {exc.status_code}") from exc
        except openai.APIError as exc:
            raise EmbeddingProviderError(f"openai: api error ({exc.__class__.__name__})") from exc


def build(settings: Any) -> OpenAIEmbeddingProvider:
    """Registry entry point (EMBEDDING_PROVIDER=openai)."""
    return OpenAIEmbeddingProvider(
        api_key=settings.embedding_api_key,
        dim=settings.embedding_dim,
        model=settings.embedding_model,
        base_url=settings.embedding_base_url,
    )
