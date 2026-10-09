"""P4 Task 16: the golden set's metric and the F-P4-15 setting - pure tests."""
from __future__ import annotations

import pytest

from app.db import repos_catalog
from app.workers.config import ConfigError, WorkerSettings
from tests import search_golden as g
from tests.test_workers_config import _base_env


def test_rank_and_scores_follow_the_declared_metric():
    assert g.rank_of(frozenset({"B"}), ["A", "B", "C"]) == 2
    assert g.rank_of(frozenset({"Z"}), ["A", "B"]) is None
    results = {q.qid: [] for q in g.QUERIES}
    exact = [q for q in g.QUERIES if q.cls == "exact"]
    results[exact[0].qid] = ["X", "Y", next(iter(exact[0].expected))]   # rank 3 -> counts
    results[exact[1].qid] = ["X", "Y", "Z", next(iter(exact[1].expected))]   # rank 4 -> MRR only
    s = g.score(results)["exact"]
    assert s.n == len(exact)
    assert s.recall_at_k == pytest.approx(1 / len(exact))
    assert s.mrr == round((1 / 3 + 1 / 4) / len(exact), 3)


def test_golden_set_is_well_formed():
    assert {q.cls for q in g.QUERIES} == set(g.CLASSES)
    assert len({q.qid for q in g.QUERIES}) == len(g.QUERIES)
    assert all(q.expected <= set(g.CATALOG) for q in g.QUERIES)
    assert all(text not in {q.query for q in g.QUERIES} for text in g.ABSENT)


def test_oracle_is_semantic_by_construction():
    o = g.OracleSemanticProvider()
    a, b, c = o.embed(texts=["جزمة", "حذاء", "عطر"], timeout_s=1.0).vectors
    assert a == b and a != c


def test_query_vector_defaults_to_the_relevance_floor():
    default = repos_catalog.QueryVector("m", [0.0]).max_distance
    assert default == repos_catalog.VECTOR_MAX_DISTANCE_DEFAULT == 0.99


@pytest.mark.parametrize("value", ["0", "-0.1", "2.5"])
def test_search_vector_max_distance_bounds(monkeypatch, value):
    _base_env(monkeypatch)
    monkeypatch.setenv("SEARCH_VECTOR_MAX_DISTANCE", value)
    with pytest.raises(ConfigError, match="SEARCH_VECTOR_MAX_DISTANCE"):
        WorkerSettings.load()


def test_search_vector_max_distance_default_and_override(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.delenv("SEARCH_VECTOR_MAX_DISTANCE", raising=False)
    assert WorkerSettings.load().search_vector_max_distance == 0.99
    monkeypatch.setenv("SEARCH_VECTOR_MAX_DISTANCE", "0.55")
    assert WorkerSettings.load().search_vector_max_distance == 0.55
