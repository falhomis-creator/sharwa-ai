"""core/app/llm/registry.py - provider selection from config (L2).

This batch has NO real provider: LLM_PROVIDER defaults to 'fake' and the only
provider is the deterministic FakeLlmProvider (imported from tests/fake_llm.py
via this ONE conditional path - the documented H7 exception, exactly like
FakeCommerce: only the external network is faked).

The production guard (M13) lives in app/workers/config.py::validate_llm_provider
so config never has to import this package.
"""
from __future__ import annotations

from typing import Any

from app.llm.port import EmbeddingProvider, LlmProvider


def build_provider(llm_provider: str) -> LlmProvider:
    """Return the single provider for this batch. 'fake' is the only value with
    a real implementation; anything else refuses to boot (no dead adapter)."""
    if llm_provider == "fake":
        # H7 exception (P1_DEVIATIONS.md): only the external network is faked.
        from tests.fake_llm import FakeLlmProvider
        return FakeLlmProvider()
    raise RuntimeError(f"unknown LLM_PROVIDER {llm_provider!r}")


def build_embedding_provider(embedding_provider: str) -> EmbeddingProvider:
    """Return the single embedding provider (P1.5b V1). 'fake' is the only value
    with a real implementation - a deterministic bag-of-words vectorizer, NOT a
    semantic model (documented in tests/fake_embedding.py). Anything else refuses
    to boot, exactly like build_provider (H7 exception: only the network is faked)."""
    if embedding_provider == "fake":
        from tests.fake_embedding import FakeEmbeddingProvider
        return FakeEmbeddingProvider()
    raise RuntimeError(f"unknown EMBEDDING_PROVIDER {embedding_provider!r}")
