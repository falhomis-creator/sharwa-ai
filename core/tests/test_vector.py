"""Pure P1.5b vector-layer tests (PROMPT §6: V4/V5/V6/V7/V11/H42/H44) - no DB/network.

Covers: the deterministic fake embedding provider (V5), the RRF three-list merge
(V4), content_hash stability (V7), the H44 dimension contract + production guard
(V6), the slots whitelist (V11), and the query-embedding cache codec (V5).
"""
from __future__ import annotations

import pytest

from app.config import ConfigError
from app.db.repos_catalog import content_hash, rrf_merge
from app.db.repos_summary import SLOT_ALLOWED_KEYS, validate_slots
from app.workers.config import EMBEDDING_DIM_CONTRACT, validate_embedding_dim, validate_embedding_provider
from app.workers.embed import decode_vector, encode_vector, query_cache_key
from tests.fake_embedding import EMBED_DIM, FakeEmbeddingProvider, embed_text


# --- V5: fake provider determinism + similarity ---------------------------------


def test_fake_embedding_is_deterministic():
    p = FakeEmbeddingProvider()
    r1 = p.embed(texts=["قميص قطني"], timeout_s=1.0)
    r2 = p.embed(texts=["قميص قطني"], timeout_s=1.0)
    assert r1.vectors[0] == r2.vectors[0]
    assert len(r1.vectors[0]) == 1024
    assert EMBED_DIM == 1024


def test_fake_embedding_similar_texts_are_closer():
    a = embed_text("قميص قطني أزرق")
    b = embed_text("قميص قطني أزرق طويل")
    c = embed_text("حذاء رياضي جلد")
    # L2-normalized => dot product == cosine similarity.
    cos_ab = sum(x * y for x, y in zip(a, b))
    cos_ac = sum(x * y for x, y in zip(a, c))
    assert cos_ab > cos_ac


def test_fake_embedding_batch_order_preserved():
    p = FakeEmbeddingProvider()
    r = p.embed(texts=["تفاح", "برتقال"], timeout_s=1.0)
    assert len(r.vectors) == 2
    assert r.vectors[0] == embed_text("تفاح")
    assert r.vectors[1] == embed_text("برتقال")


# --- V4: RRF with three lists, unchanged function ------------------------------


def test_rrf_three_lists_manual_order():
    lists = [["a", "b", "c"], ["b"], ["a"]]
    k = 60
    merged = rrf_merge(lists, k)
    # score(a) = 1/61 (list1 r1) + 1/61 (list3 r1) = 2/61
    # score(b) = 1/62 (list1 r2) + 1/61 (list2 r1)
    # score(c) = 1/63 (list1 r3)
    expected = [
        ("a", 1.0 / 61 + 1.0 / 61),
        ("b", 1.0 / 62 + 1.0 / 61),
        ("c", 1.0 / 63),
    ]
    assert [doc for doc, _ in merged] == ["a", "b", "c"]
    for (doc, score), (edoc, escore) in zip(merged, expected):
        assert doc == edoc
        assert score == pytest.approx(escore)


def test_rrf_still_handles_two_lists():
    merged = rrf_merge([["a"], ["b", "a"]], 60)
    assert merged[0][0] == "a"


# --- V7: content_hash identity -------------------------------------------------


def test_content_hash_stable_and_changes_on_title():
    h1 = content_hash("قميص", "قطن")
    h2 = content_hash("قميص", "قطن")
    h3 = content_hash("قميص آخر", "قطن")
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 32  # md5 hex


def test_content_hash_changes_on_description():
    assert content_hash("قميص", "قطن") != content_hash("قميص", "حرير")


# --- V6 / H44: dimension contract + production guard ---------------------------


def test_embedding_dim_contract_is_1024():
    assert EMBEDDING_DIM_CONTRACT == 1024
    validate_embedding_dim(1024)  # no raise
    with pytest.raises(ConfigError):
        validate_embedding_dim(768)


def test_fake_embedding_provider_rejected_in_production():
    with pytest.raises(ConfigError):
        validate_embedding_provider("production", "fake")
    validate_embedding_provider("test", "fake")  # no raise


def test_local_embedding_provider_allowed_in_production():
    # P4.2: `local` is REAL local code (deterministic, no network, no cost -
    # app/llm/adapters/local_embedding.py), so production boots with it
    # (DeepSeek exposes no embeddings endpoint).
    from types import SimpleNamespace

    from app.llm import registry
    from app.llm.adapters.local_embedding import LocalEmbeddingProvider

    validate_embedding_provider("production", "local")  # no raise
    provider = registry.build_embedding_provider("local", SimpleNamespace(embedding_dim=1024))
    assert isinstance(provider, LocalEmbeddingProvider)
    result = provider.embed(texts=["قميص قطن", "حذاء"], timeout_s=1.0)
    assert all(len(v) == EMBED_DIM for v in result.vectors)
    assert result.usage.provider == "local"
    assert result.usage.input_tokens == 0  # local computation: zero tokens, zero cost
    # Byte-identical vectors to the dev/test fake provider (same algorithm and
    # shared Arabic normalizer): catalog embeddings written before the switch
    # stay valid under EMBEDDING_PROVIDER=local.
    assert result.vectors[0] == embed_text("قميص قطن")
    with pytest.raises(RuntimeError):
        registry.build_embedding_provider("no-such-embedding", SimpleNamespace(embedding_dim=1024))


# --- V11: slots whitelist ------------------------------------------------------


def test_slots_whitelist_exact_vocabulary():
    assert SLOT_ALLOWED_KEYS == {
        "summary_seq", "last_search_query_hash", "last_shown_product_ids", "optout",
    }


def test_slots_whitelist_rejects_unknown_key():
    validate_slots({"summary_seq": 5})  # no raise
    with pytest.raises(ValueError):
        validate_slots({"model_text": "x"})


# --- V5: query-embedding cache codec ------------------------------------------


def test_query_cache_codec_roundtrip():
    vec = [0.1, 0.2, 0.3]
    assert decode_vector(encode_vector(vec)) == vec


def test_query_cache_decode_rejects_malformed():
    assert decode_vector("not json") is None
    assert decode_vector('{"a": 1}') is None
    assert decode_vector('["a", 1]') is None


def test_query_cache_key_normalizes():
    # Different spellings that normalize identically share one key (H20: no raw text).
    assert query_cache_key("قميص") == query_cache_key("قميص  ")
    assert query_cache_key("أحمر") == query_cache_key("احمر")
