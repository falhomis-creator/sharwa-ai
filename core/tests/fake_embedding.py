"""core/tests/fake_embedding.py - FakeEmbeddingProvider: deterministic, no-network.

P1.5b V1 (owner decision): a fake embedding provider in the same spirit as
FakeLlmProvider / FakeCommerce. It is a deterministic bag-of-words vectorizer
(hashed token -> one of 1024 buckets -> L2 normalise), NOT a semantic model:

  * the SAME text always produces the SAME vector byte-for-byte;
  * two similar texts (shared words) produce close vectors, two disjoint texts
    produce orthogonal vectors - which is what makes the hybrid-search tests
    meaningful.

Documented approximation for tests only: it encodes lexical overlap, not meaning.
No network, no SDK, no cost.
"""
from __future__ import annotations

import hashlib
import math
import re

from app.llm.port import EmbeddingProvider, EmbeddingResult, LlmUsage
from app.text.arabic import normalize

# H44: the catalog_embeddings.embedding column is vector(1024). This is the
# contract; the fake produces exactly this width and the real provider must too.
EMBED_DIM = 1024

_PROVIDER = "fake"
_MODEL = "fake-embedding"

# Word tokens only (Arabic + Latin + digits), after the shared Arabic normalizer.
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """Deterministic token list: normalize (shared C6) then split into word runs."""
    return _TOKEN_RE.findall(normalize(text))


def embed_text(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Bag-of-words over hashed token indices, L2-normalized. Pure and
    deterministic (md5 token hash - Python's `hash()` is salted per process, so
    it is deliberately NOT used)."""
    vec = [0.0] * dim
    for tok in tokenize(text):
        idx = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16) % dim
        vec[idx] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec  # empty text -> zero vector, still deterministic
    return [v / norm for v in vec]


class FakeEmbeddingProvider(EmbeddingProvider):
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, *, texts: list[str], timeout_s: float) -> EmbeddingResult:
        self.calls.append(list(texts))
        vectors = [embed_text(t) for t in texts]
        total_chars = sum(len(t) for t in texts)
        # Estimate only (documented, like FakeLlmProvider): chars // 4, no output.
        usage = LlmUsage(
            input_tokens=total_chars // 4,
            output_tokens=0,
            model=_MODEL,
            provider=_PROVIDER,
            latency_ms=1,
        )
        return EmbeddingResult(vectors=vectors, usage=usage)
