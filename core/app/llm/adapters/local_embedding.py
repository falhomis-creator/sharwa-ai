"""core/app/llm/adapters/local_embedding.py - the production-safe LOCAL
embedding provider (P4.2).

Owner decision P4.2: DeepSeek is the LLM, but DeepSeek exposes NO embeddings
endpoint (api-docs.deepseek.com lists chat models only). The worker refuses
to boot in production with EMBEDDING_PROVIDER=fake (V1 H-safety: a TEST
approximation never reaches real customers), so the deterministic
bag-of-words vectorizer moves into production code under the explicit name
``local``:

  * real local code, not a stub: no network, no SDK, no cost;
  * byte-identical vectors to the dev/test fake provider (same shared Arabic
    normalizer app/text/arabic.py, same md5 bucket hash), so catalog
    embeddings written in dev/test remain valid after the switch;
  * still NOT a semantic model - it encodes lexical overlap. Vector search is
    an OPTIMIZATION (H42: it fails open to plain search), so this keeps the
    worker bootable until the owner picks a paid embedding provider.

EMBEDDING_DIM=1024 stays the hard H44 contract (the vector column width).
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from app.llm.port import EmbeddingProvider, EmbeddingResult, LlmUsage
from app.text.arabic import normalize

PROVIDER_NAME = "local"
MODEL_NAME = "local-embedding"
EMBED_DIM = 1024

# Word tokens only (Arabic + Latin + digits), after the shared Arabic normalizer.
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _embed_text(text: str, dim: int) -> list[float]:
    """Bag-of-words over hashed token indices, L2-normalized. Pure and
    deterministic (md5 token hash - Python's `hash()` is salted per process,
    so it is deliberately NOT used)."""
    vec = [0.0] * dim
    for tok in _TOKEN_RE.findall(normalize(text)):
        idx = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16) % dim
        vec[idx] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec  # empty text -> zero vector, still deterministic
    return [v / norm for v in vec]


class LocalEmbeddingProvider(EmbeddingProvider):
    def __init__(self, dim: int = EMBED_DIM) -> None:
        self._dim = dim

    def embed(self, *, texts: list[str], timeout_s: float) -> EmbeddingResult:
        vectors = [_embed_text(t, self._dim) for t in texts]
        # Local computation: zero tokens, zero cost (the price table has no
        # 'local' entry, so budget.compute_cost_micro_usd returns 0).
        usage = LlmUsage(
            input_tokens=0, output_tokens=0, model=MODEL_NAME,
            provider=PROVIDER_NAME, latency_ms=1,
        )
        return EmbeddingResult(vectors=vectors, usage=usage)


def build(settings: Any) -> LocalEmbeddingProvider:
    """Registry entry point (EMBEDDING_PROVIDER=local)."""
    return LocalEmbeddingProvider(dim=settings.embedding_dim)
