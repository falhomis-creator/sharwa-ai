"""P4 Task 16: the search golden set on real PostgreSQL - lexical vs hybrid
(lexical + vector, RRF) through the production search_products(), with the
declared metric from tests/search_golden.py.

Offline (always, with the db suite):
  * lexical baseline is computed and the exact class is fully answered (the set
    and the harness are sound);
  * the deterministic `local` provider (bag of words) is wired in hybrid mode
    and never loses a lexical top-3 hit;
  * a semantic ORACLE proves the plumbing: when vectors ARE semantic, hybrid
    search answers the synonym / English queries lexical search misses, with
    no regression.

Live (only when OPENAI_API_KEY is set): the same set against the real
`openai` adapter (text-embedding-3-small, 1024 dims) - the Arabic-comprehension
measurement the owner asked for. It prints the full table (`pytest -s`).
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import psycopg
import pytest

from app import db as core_db
from app.db import repos_catalog
from app.db import testsupport as db_testsupport
from app.llm.adapters.local_embedding import LocalEmbeddingProvider
from tests import search_golden as g

pytestmark = pytest.mark.db

TENANT_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture()
def catalog():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.delete_tenant_full(dsn, TENANT_A)
    db_testsupport.delete_tenant_full(dsn, TENANT_B)
    db_testsupport.seed_two_tenants(dsn, TENANT_A, TENANT_B)
    with psycopg.connect(dsn, autocommit=True) as conn:
        for pid, (title, description, category) in g.CATALOG.items():
            conn.execute(
                "INSERT INTO catalog_products "
                "(tenant_id, platform_product_id, title, description, category, source_version) "
                "VALUES (%s, %s, %s, %s, %s, 1)",
                (TENANT_A, pid, title, description, category),
            )
        ids = dict(conn.execute(
            "SELECT platform_product_id, id FROM catalog_products WHERE tenant_id = %s", (TENANT_A,),
        ).fetchall())
    yield ids
    db_testsupport.delete_tenant_full(dsn, TENANT_A)
    db_testsupport.delete_tenant_full(dsn, TENANT_B)


def _embed_catalog(ids: dict[str, uuid.UUID], provider) -> None:
    pids = list(g.CATALOG)
    result = provider.embed(texts=[g.embed_text_for(p) for p in pids], timeout_s=30.0)
    with core_db.tenant_tx(TENANT_A) as conn:
        for pid, vec in zip(pids, result.vectors, strict=True):
            title, description, _c = g.CATALOG[pid]
            repos_catalog.upsert_catalog_embedding(
                conn, tenant_id=TENANT_A, product_id=ids[pid],
                content_hash=repos_catalog.content_hash(title, description),
                model=provider.model_name, embedding=vec,
            )


def _run(provider=None) -> dict[str, list[str]]:
    """qid -> platform ids of the returned cards, in rank order."""
    vectors: dict[str, list[float]] = {}
    if provider is not None:
        res = provider.embed(texts=[q.query for q in g.QUERIES], timeout_s=30.0)
        vectors = {q.qid: v for q, v in zip(g.QUERIES, res.vectors, strict=True)}
    out: dict[str, list[str]] = {}
    with core_db.tenant_tx(TENANT_A) as conn:
        for q in g.QUERIES:
            qv = (repos_catalog.QueryVector(provider.model_name, vectors[q.qid])
                  if provider is not None else None)
            cards = repos_catalog.search_products(conn, tenant_id=TENANT_A, query=q.query, query_vector=qv)
            out[q.qid] = [str(c["platform_product_id"]) for c in cards]
    return out


def _absent_empty_rate(provider=None) -> float:
    """F-P4-15: share of ABSENT queries answered with no cards."""
    empty = 0
    with core_db.tenant_tx(TENANT_A) as conn:
        for text in g.ABSENT:
            qv = None
            if provider is not None:
                vec = provider.embed(texts=[text], timeout_s=30.0).vectors[0]
                qv = repos_catalog.QueryVector(provider.model_name, vec)
            empty += not repos_catalog.search_products(conn, tenant_id=TENANT_A, query=text, query_vector=qv)
    return empty / len(g.ABSENT)


def _top_distances(provider, texts: list[str]) -> list[float]:
    """Cosine distance of each text's nearest catalog vector (calibration aid)."""
    vecs = provider.embed(texts=texts, timeout_s=30.0).vectors
    out = []
    with core_db.tenant_tx(TENANT_A) as conn:
        for vec in vecs:
            lit = "[" + ",".join(repr(float(x)) for x in vec) + "]"
            row = conn.execute(
                "SELECT min(embedding <=> %s::vector) FROM catalog_embeddings "
                "WHERE tenant_id = %s AND model = %s", (lit, TENANT_A, provider.model_name),
            ).fetchone()
            out.append(round(float(row[0]), 3))
    return out


def _lost(lexical: dict[str, list[str]], hybrid: dict[str, list[str]]) -> list[str]:
    """Queries lexical search answers in its top 3 that hybrid search loses."""
    lost = []
    for q in g.QUERIES:
        lr, hr = g.rank_of(q.expected, lexical[q.qid]), g.rank_of(q.expected, hybrid[q.qid])
        if lr is not None and lr <= g.TOP_K and (hr is None or hr > g.TOP_K):
            lost.append(q.qid)
    return lost


def test_golden_set_offline(catalog, capsys):
    lexical = _run()
    local = LocalEmbeddingProvider()
    _embed_catalog(catalog, local)
    hybrid_local = _run(local)
    oracle = g.OracleSemanticProvider()
    _embed_catalog(catalog, oracle)   # a second model: the other vectors are replaced (one row per product)
    hybrid_oracle = _run(oracle)

    scores = {"lexical": g.score(lexical), "hybrid/local": g.score(hybrid_local),
              "hybrid/oracle": g.score(hybrid_oracle)}
    with capsys.disabled():
        print("\n" + g.format_table(scores))

    # the set and the harness are sound: lexical search answers every exact query
    assert scores["lexical"]["exact"].recall_at_k == 1.0
    # no regression from the vector half, whatever the provider
    assert _lost(lexical, hybrid_local) == []
    assert _lost(lexical, hybrid_oracle) == []
    # plumbing: semantic vectors lift the classes lexical search cannot reach
    semantic = ("synonym", "english")
    assert g.recall_over(hybrid_oracle, semantic) == 1.0
    assert g.recall_over(hybrid_oracle, semantic) > g.recall_over(lexical, semantic)


def test_absent_products_return_no_cards(catalog, capsys):
    # F-P4-15: a product the store does not sell must not come back as 8
    # unrelated cards just because the vector list always ranks something.
    assert _absent_empty_rate() == 1.0
    for provider in (LocalEmbeddingProvider(), g.OracleSemanticProvider()):
        _embed_catalog(catalog, provider)
        rate = _absent_empty_rate(provider)
        with capsys.disabled():
            print(f"\nabsent-empty-rate {provider.model_name}: {rate:.2f}")
        assert rate == 1.0


def test_wrong_model_vectors_add_nothing(catalog):
    # F-P4-13 inside the golden harness: a query vector of another model finds
    # no vectors to compare with, so hybrid == lexical exactly.
    _embed_catalog(catalog, LocalEmbeddingProvider())
    lexical = _run()
    oracle = g.OracleSemanticProvider()   # query model 'golden-oracle', catalog model 'local-embedding'
    assert _run(oracle) == lexical


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="live: needs OPENAI_API_KEY")
def test_golden_set_live_openai(catalog, capsys):
    from app.llm import registry

    provider = registry.build_embedding_provider("openai", SimpleNamespace(
        embedding_dim=1024, embedding_api_key=os.environ["OPENAI_API_KEY"],
        embedding_model=os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small"),
        embedding_base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    ))
    lexical = _run()
    _embed_catalog(catalog, provider)
    hybrid = _run(provider)
    scores = {"lexical": g.score(lexical), f"hybrid/{provider.model_name}": g.score(hybrid)}
    with capsys.disabled():
        print("\n" + g.format_table(scores))
        for q in g.QUERIES:
            print(f"{q.qid:<4}{q.query:<24} lexical={lexical[q.qid][:3]} hybrid={hybrid[q.qid][:3]}")

    # calibration aid for SEARCH_VECTOR_MAX_DISTANCE (F-P4-15): nearest-vector
    # distance of answered queries vs. queries for products the store lacks.
    hit_d = _top_distances(provider, [q.query for q in g.QUERIES])
    absent_d = _top_distances(provider, list(g.ABSENT))
    with capsys.disabled():
        print(f"nearest distance, golden queries: max={max(hit_d)} all={hit_d}")
        print(f"nearest distance, absent queries: min={min(absent_d)} all={absent_d}")
        print(f"absent-empty-rate at the default floor: {_absent_empty_rate(provider):.2f}")

    semantic = ("synonym", "english")
    assert scores[f"hybrid/{provider.model_name}"]["exact"].recall_at_k == 1.0
    assert _lost(lexical, hybrid) == []
    assert g.recall_over(hybrid, semantic) >= g.LIVE_MIN_SEMANTIC_RECALL
    assert g.recall_over(hybrid, semantic) > g.recall_over(lexical, semantic)
