"""P4 Task 15: the paid semantic embedding adapter (openai_embedding.py) and its
wiring - pure tests, no network: the SDK client is a recording stub, exactly
like tests/test_llm.py does for the DeepSeek adapter."""
from __future__ import annotations

from types import SimpleNamespace

import httpx2
import openai
import pytest

from app.llm import budget, registry
from app.llm.adapters import openai_embedding
from app.llm.port import EmbeddingProviderError, EmbeddingTimeoutError
from app.workers import embed
from app.workers.config import DEFAULT_LLM_PRICE_TABLE, ConfigError, WorkerSettings
from tests.test_workers_config import _base_env

DIM = 1024


class _StubEmbeddings:
    def __init__(self, *, error: Exception | None = None, shuffle: bool = False,
                 drop_one: bool = False) -> None:
        self.calls: list[dict] = []
        self._error = error
        self._shuffle = shuffle
        self._drop_one = drop_one

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        data = [
            SimpleNamespace(index=i, embedding=[float(i + 1)] * kwargs["dimensions"])
            for i in range(len(kwargs["input"]))
        ]
        if self._shuffle:
            data.reverse()
        if self._drop_one:
            data = data[:-1]
        return SimpleNamespace(data=data, model="text-embedding-3-small",
                               usage=SimpleNamespace(prompt_tokens=7, total_tokens=7))


def _provider(stub: _StubEmbeddings, model: str = "text-embedding-3-small"):
    client = SimpleNamespace(embeddings=stub)
    return openai_embedding.OpenAIEmbeddingProvider(
        api_key="sk-test", dim=DIM, model=model, client=client,
    )


# ---- the adapter -------------------------------------------------------------

def test_requests_exactly_the_contract_width_and_the_configured_model():
    stub = _StubEmbeddings()
    result = _provider(stub).embed(texts=["قميص قطني", "حذاء"], timeout_s=3.5)
    call = stub.calls[0]
    assert call["dimensions"] == DIM            # H44: never the native 1536
    assert call["model"] == "text-embedding-3-small"
    assert call["input"] == ["قميص قطني", "حذاء"]
    assert call["encoding_format"] == "float"
    assert call["timeout"] == 3.5
    assert [len(v) for v in result.vectors] == [DIM, DIM]
    assert (result.usage.provider, result.usage.model, result.usage.input_tokens) == (
        "openai", "text-embedding-3-small", 7,
    )


def test_vectors_follow_the_input_order_even_if_the_api_reorders():
    result = _provider(_StubEmbeddings(shuffle=True)).embed(texts=["a", "b", "c"], timeout_s=1.0)
    assert [v[0] for v in result.vectors] == [1.0, 2.0, 3.0]


def test_blank_texts_get_a_zero_vector_without_a_paid_call():
    stub = _StubEmbeddings()
    result = _provider(stub).embed(texts=["", "قميص", "   "], timeout_s=1.0)
    assert stub.calls[0]["input"] == ["قميص"]
    assert result.vectors[0] == [0.0] * DIM and result.vectors[2] == [0.0] * DIM
    assert result.vectors[1][0] == 1.0
    stub2 = _StubEmbeddings()
    assert _provider(stub2).embed(texts=["  "], timeout_s=1.0).usage.input_tokens == 0
    assert stub2.calls == []


def test_a_missing_vector_is_a_provider_error_not_a_silent_shift():
    with pytest.raises(EmbeddingProviderError):
        _provider(_StubEmbeddings(drop_one=True)).embed(texts=["a", "b"], timeout_s=1.0)


_REQ = httpx2.Request("POST", "https://api.openai.com/v1/embeddings")


def _status(cls, code: int) -> Exception:
    return cls("x", response=httpx2.Response(code, request=_REQ), body=None)


@pytest.mark.parametrize(("error", "expected"), [
    (openai.APITimeoutError(request=_REQ), EmbeddingTimeoutError),
    (openai.APIConnectionError(request=_REQ), EmbeddingProviderError),
    (_status(openai.RateLimitError, 429), EmbeddingProviderError),
    (_status(openai.InternalServerError, 500), EmbeddingProviderError),
    (_status(openai.AuthenticationError, 401), EmbeddingProviderError),
    (_status(openai.BadRequestError, 400), EmbeddingProviderError),
])
def test_sdk_errors_map_onto_the_port_vocabulary(error, expected):
    # H39/H42: the caller's breaker and fail-open only understand the port errors.
    with pytest.raises(expected):
        _provider(_StubEmbeddings(error=error)).embed(texts=["قميص"], timeout_s=1.0)


def test_empty_key_refuses_construction():
    with pytest.raises(RuntimeError):
        openai_embedding.OpenAIEmbeddingProvider(api_key="", dim=DIM)


def test_sdk_retries_are_disabled():
    provider = openai_embedding.OpenAIEmbeddingProvider(api_key="sk-test", dim=DIM)
    assert provider._client.max_retries == 0   # H39: retries are ours


# ---- registry + config ---------------------------------------------------------

def _settings(**kw):
    base = dict(embedding_dim=DIM, embedding_api_key="sk-test",
                embedding_model="text-embedding-3-small",
                embedding_base_url="https://api.openai.com/v1")
    base.update(kw)
    return SimpleNamespace(**base)


def test_registry_builds_the_adapter_by_name_and_local_still_works():
    provider = registry.build_embedding_provider("openai", _settings())
    assert isinstance(provider, openai_embedding.OpenAIEmbeddingProvider)
    assert provider.model_name == "text-embedding-3-small"
    assert registry.build_embedding_provider("local", _settings()).model_name == "local-embedding"
    for bad in ("no-such", "nope", "../x"):
        with pytest.raises(RuntimeError):
            registry.build_embedding_provider(bad, _settings())


def test_openai_without_key_refuses_boot(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        WorkerSettings.load()


def test_openai_production_boot_with_key(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-deepseek")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    s = WorkerSettings.load()
    assert (s.embedding_provider, s.embedding_api_key, s.embedding_model, s.embedding_base_url) == (
        "openai", "sk-test-openai", "text-embedding-3-small", "https://api.openai.com/v1",
    )


def test_local_default_needs_no_key(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert WorkerSettings.load().embedding_api_key == ""


def test_price_entry_is_in_the_budget_unit():
    # $0.02 per 1M tokens = 20,000 micro-USD per 1M = 20 per 1k (the table's unit).
    cost = budget.compute_cost_micro_usd(
        1_000_000, 0, DEFAULT_LLM_PRICE_TABLE, "openai", "text-embedding-3-small",
    )
    assert cost == 20_000


# ---- F-P4-13: the model identity reaches the handle and the cache key ----------

def test_handle_carries_the_providers_model(monkeypatch):
    monkeypatch.setattr(registry, "build_embedding_provider",
                        lambda name, settings: _provider(_StubEmbeddings(), model="text-embedding-3-large"))
    s = SimpleNamespace(embedding_provider="openai", llm_breaker_fail_threshold=5, llm_breaker_reset_s=60)
    assert embed.build_embed(s).model_name == "text-embedding-3-large"


def test_query_cache_key_is_scoped_by_model():
    a = embed.query_cache_key("قميص", "local-embedding")
    b = embed.query_cache_key("قميص", "text-embedding-3-small")
    assert a != b and a.startswith("emb:q:local-embedding:")


def test_batch_embedding_cost_is_priced_and_split_by_tenant(monkeypatch):
    # P4 Task 15: a paid batch is accounted at its real token count (split by
    # text length) and priced; the fake/local path keeps cost 0.
    import contextlib
    import uuid

    t1, t2 = uuid.uuid4(), uuid.uuid4()
    rows: list[dict] = []
    monkeypatch.setattr(embed.core_db, "tenant_tx", lambda tid: contextlib.nullcontext(None))
    monkeypatch.setattr(embed.repos_llm, "record_llm_call", lambda conn, **kw: rows.append(kw))
    s = SimpleNamespace(llm_price_table=DEFAULT_LLM_PRICE_TABLE)
    items = [(t1, uuid.uuid4(), "a" * 300, "h"), (t2, uuid.uuid4(), "b" * 100, "h")]
    embed._record_embedding_calls(s, items, "openai", "text-embedding-3-small", 5,
                                  input_tokens=400_000)
    assert [(r["input_tokens"], r["cost_micro_usd"]) for r in rows] == [(300_000, 6_000), (100_000, 2_000)]
    rows.clear()
    embed._record_embedding_calls(s, items, "local", "local-embedding", 1)
    assert [(r["input_tokens"], r["cost_micro_usd"]) for r in rows] == [(75, 0), (25, 0)]


def test_default_deepseek_prices_are_in_the_budget_unit():
    # F-P4-14 (owner decision): $0.30/M in + $1.20/M out (deepseek-chat) =>
    # 1k in + 1k out = $0.0015 = 1500 micro-USD; 1M in = $0.30 = 300,000.
    t = DEFAULT_LLM_PRICE_TABLE
    assert budget.compute_cost_micro_usd(1000, 1000, t, "deepseek", "deepseek-chat") == 1500
    assert budget.compute_cost_micro_usd(1_000_000, 0, t, "deepseek", "deepseek-flash") == 300_000
    assert budget.compute_cost_micro_usd(1_000_000, 1_000_000, t, "deepseek", "deepseek-v4-pro") == 5_280_000
