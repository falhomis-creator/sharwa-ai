"""P4 Task 15 / F-P4-13: embeddings are scoped by MODEL, on real PostgreSQL.

Switching EMBEDDING_PROVIDER (local -> the paid semantic model) must never mix
two vector spaces: the candidate lister re-embeds a vector written by another
model (0020), the batch worker writes the running model's name, the vector
source of search compares only same-model vectors, and the query cache key
carries the model."""
from __future__ import annotations

import dataclasses
import os
import uuid

import psycopg
import pytest
import redis

from app import db as core_db
from app.db import repos_catalog
from app.db import testsupport as db_testsupport
from app.llm.breaker import CircuitBreaker
from app.workers import embed
from app.workers.config import WorkerSettings
from tests.fake_embedding import EMBED_DIM, FakeEmbeddingProvider, embed_text

pytestmark = pytest.mark.db

TENANT_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = uuid.UUID("22222222-2222-2222-2222-222222222222")
OLD, NEW = "local-embedding", "semantic-test-model"


class _NamedFake(FakeEmbeddingProvider):
    """The deterministic fake, presenting itself as another model."""

    def __init__(self, model_name: str) -> None:
        super().__init__()
        self.model_name = model_name


def _settings() -> WorkerSettings:
    required = {
        f.name for f in dataclasses.fields(WorkerSettings)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    }
    values: dict[str, object] = dict.fromkeys(required)
    values.update(core_max_consecutive_bot_replies=5, env="test")
    return WorkerSettings(**values)


def _handle(model: str) -> embed.EmbedHandle:
    return embed.EmbedHandle(provider=_NamedFake(model), breaker=CircuitBreaker(5, 60.0),
                             provider_name="fake", model_name=model)


@pytest.fixture()
def products():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.delete_tenant_full(dsn, TENANT_A)
    db_testsupport.delete_tenant_full(dsn, TENANT_B)
    db_testsupport.seed_two_tenants(dsn, TENANT_A, TENANT_B)
    db_testsupport.seed_catalog_product(dsn, tenant_id=TENANT_A, platform_product_id="P-SHIRT",
                                        category=None, title="قميص قطني أزرق")
    db_testsupport.seed_catalog_product(dsn, tenant_id=TENANT_A, platform_product_id="P-SHOE",
                                        category=None, title="حذاء رياضي جلد")
    with psycopg.connect(dsn) as conn:
        ids = dict(conn.execute(
            "SELECT platform_product_id, id FROM catalog_products WHERE tenant_id = %s", (TENANT_A,),
        ).fetchall())
    yield dsn, ids
    db_testsupport.delete_tenant_full(dsn, TENANT_A)
    db_testsupport.delete_tenant_full(dsn, TENANT_B)


def _write(product_id: uuid.UUID, title: str, model: str) -> None:
    with core_db.tenant_tx(TENANT_A) as conn:
        repos_catalog.upsert_catalog_embedding(
            conn, tenant_id=TENANT_A, product_id=product_id,
            content_hash=repos_catalog.content_hash(title, None), model=model,
            embedding=embed_text(title),
        )


def _candidates(model: str) -> set[uuid.UUID]:
    with core_db.system_tx() as conn:
        rows = repos_catalog.list_products_needing_embedding(conn, limit=10_000, model=model)
    return {pid for tid, pid in rows if tid == TENANT_A}


def _models(dsn: str) -> dict[str, str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT cp.platform_product_id, ce.model FROM catalog_embeddings ce "
            "JOIN catalog_products cp ON cp.tenant_id = ce.tenant_id AND cp.id = ce.product_id "
            "WHERE ce.tenant_id = %s", (TENANT_A,),
        ).fetchall()
    return {str(r[0]): str(r[1]) for r in rows}


def test_a_vector_of_another_model_needs_re_embedding(products):
    _dsn, ids = products
    _write(ids["P-SHIRT"], "قميص قطني أزرق", OLD)
    _write(ids["P-SHOE"], "حذاء رياضي جلد", NEW)
    assert _candidates(NEW) == {ids["P-SHIRT"]}     # old model => re-embed
    assert _candidates(OLD) == {ids["P-SHOE"]}
    _write(ids["P-SHIRT"], "قميص قطني أزرق", NEW)
    assert _candidates(NEW) == set()                # same model + same content => done


def test_batch_worker_replaces_old_model_vectors_with_the_running_model(products):
    dsn, ids = products
    _write(ids["P-SHIRT"], "قميص قطني أزرق", OLD)
    assert embed.embed_once(settings=_settings(), handle=_handle(NEW)) == "ok"
    assert _models(dsn) == {"P-SHIRT": NEW, "P-SHOE": NEW}
    assert _candidates(NEW) == set()


def test_vector_search_compares_only_same_model_vectors(products):
    _dsn, ids = products
    _write(ids["P-SHIRT"], "قميص قطني أزرق", OLD)
    _write(ids["P-SHOE"], "حذاء رياضي جلد", NEW)
    query = embed_text("قميص قطني")

    def vector_ids(model: str) -> list[str]:
        with core_db.tenant_tx(TENANT_A) as conn:
            return repos_catalog._vector_product_ids(
                # max_distance=2.0: no relevance floor (F-P4-15), so this test
                # sees model scoping alone - the shoe shares no word with the query.
                conn, tenant_id=TENANT_A,
                query_vector=repos_catalog.QueryVector(model, query, max_distance=2.0),
                category=None, limit=10,
            )
    assert vector_ids(OLD) == [str(ids["P-SHIRT"])]
    assert vector_ids(NEW) == [str(ids["P-SHOE"])]   # the old-model shirt is never compared
    assert vector_ids("unknown-model") == []


def test_query_vector_is_cached_under_its_model(products):
    client = redis.Redis(host="127.0.0.1", port=6390, password="test-redis-pw", decode_responses=True)
    for key in client.scan_iter("emb:q:*"):
        client.delete(key)
    handle = _handle(NEW)
    qv = embed.query_vector(settings=_settings(), handle=handle, query="قميص قطني",
                            tenant_id=TENANT_A, cache_client=client)
    assert isinstance(qv, repos_catalog.QueryVector)
    assert qv.model == NEW and len(qv.values) == EMBED_DIM
    assert client.get(embed.query_cache_key("قميص قطني", NEW)) is not None
    assert client.get(embed.query_cache_key("قميص قطني", OLD)) is None
    # a second call is a cache hit: no new provider call
    again = embed.query_vector(settings=_settings(), handle=handle, query="قميص قطني",
                               tenant_id=TENANT_A, cache_client=client)
    assert again == qv and len(handle.provider.calls) == 1
    # another model never reuses that cached vector
    other = _handle(OLD)
    assert embed.query_vector(settings=_settings(), handle=other, query="قميص قطني",
                              tenant_id=TENANT_A, cache_client=client).model == OLD
    assert len(other.provider.calls) == 1
