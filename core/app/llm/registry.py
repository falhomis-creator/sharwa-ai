"""core/app/llm/registry.py - provider selection from config (L2).

P4.2 (owner decision): the engine has exactly ONE real LLM vendor, selected by
the LLM_PROVIDER name in the worker config. 'fake' remains the deterministic
dev/test provider (tests/fake_llm.py via this ONE conditional path - the
documented H7 exception, exactly like FakeCommerce: only the external network
is faked). Every other name must have a REAL adapter module under
app/llm/adapters/<name>.py exposing ``build(settings)``; it is imported
dynamically so this file itself never names a vendor (S8 rule 3: provider
names live in the adapters package and the two config files only). An unknown
name refuses to boot - no dead adapter.

The production guard (M13) lives in app/workers/config.py::validate_llm_provider
so config never has to import this package.
"""
from __future__ import annotations

import importlib
from typing import Any

from app.llm.port import EmbeddingProvider, LlmProvider


def build_provider(llm_provider: str, settings: Any) -> LlmProvider:
    """Return the provider for this name: 'fake' (dev/tests only) or a real
    adapter from app/llm/adapters/; anything else refuses to boot."""
    if llm_provider == "fake":
        # H7 exception (P1_DEVIATIONS.md): only the external network is faked.
        from tests.fake_llm import FakeLlmProvider
        return FakeLlmProvider()
    try:
        adapter = importlib.import_module(f"app.llm.adapters.{llm_provider}")
    except ImportError as exc:
        raise RuntimeError(
            f"unknown LLM_PROVIDER {llm_provider!r} (no app/llm/adapters/{llm_provider}.py)"
        ) from exc
    return adapter.build(settings)


def build_embedding_provider(embedding_provider: str, settings: Any) -> EmbeddingProvider:
    """Return the embedding provider (P1.5b V1, amended P4.2): 'fake'
    (dev/tests only), or 'local' - the deterministic, no-network, no-cost
    provider in app/llm/adapters/local_embedding.py (the LLM vendor exposes no
    embeddings endpoint). Anything else refuses to boot."""
    if embedding_provider == "fake":
        from tests.fake_embedding import FakeEmbeddingProvider
        return FakeEmbeddingProvider()
    if embedding_provider == "local":
        from app.llm.adapters import local_embedding
        return local_embedding.build(settings)
    raise RuntimeError(f"unknown EMBEDDING_PROVIDER {embedding_provider!r}")

