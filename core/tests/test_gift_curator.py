"""P4 Task 12: GiftCurator solver (§5.2) - deterministic, pure. One test per branch;
the boundary/performance suite is Task 13."""
from __future__ import annotations

import random

import pytest

from app.gift import curator
from app.gift.curator import REASONS, GiftBasket, GiftCandidate, GiftResult, curate

G = GiftCandidate

# Hand-checked example (budget 1000 => window [850, 1000]):
#   A+C+E = 1000: 410 relevance + 50 (2 categories) - 0 shortfall      = 460
#   B+C+E =  950: 410 + 50 - 10*5                                        = 410
#   A+C   =  900: 400 + 0 - 10*10                                        = 300
#   B+C   =  850: 400 + 50 - 10*15                                       = 300 (ties A+C; lower total => 4th)
#   A+B   =  950: 200 + 50 - 50 = 200;  D = 1000: 50
EXAMPLE = (
    G("A", 500, 100, "x"), G("B", 450, 100, "y"), G("C", 400, 300, "x"),
    G("D", 1000, 50, "z"), G("E", 100, 10, "y"),
)


def _ids(r: GiftResult) -> list[tuple[str, ...]]:
    return [b.product_ids for b in r.baskets]


def test_best_three_ranked_score_then_total_then_ids():
    r = curate(EXAMPLE, 1000)
    assert r.reasons == ()
    assert r.baskets == (
        GiftBasket(("A", "C", "E"), 1000, 460),
        GiftBasket(("B", "C", "E"), 950, 410),
        GiftBasket(("A", "C"), 900, 300),
    )


def test_input_order_never_changes_the_result():
    expected = curate(EXAMPLE, 1000)
    rng = random.Random(7)
    for _ in range(20):
        shuffled = list(EXAMPLE)
        rng.shuffle(shuffled)
        assert curate(tuple(shuffled), 1000) == expected


def test_window_is_exact_both_edges():
    # budget 1000 => [850, 1000]; 849 and 1001 are outside, 850 and 1000 inside
    assert _ids(curate((G("a", 849, 1, "x"),), 1000)) == []
    assert _ids(curate((G("a", 850, 1, "x"),), 1000)) == [("a",)]
    assert _ids(curate((G("a", 1000, 1, "x"),), 1000)) == [("a",)]
    assert _ids(curate((G("a", 1001, 1, "x"),), 1000)) == []


def test_window_low_edge_rounds_up_without_floats():
    # budget 999 => 85% = 849.15 => low edge 850 (ceil), so 849 is outside
    assert _ids(curate((G("a", 849, 1, "x"),), 999)) == []
    assert _ids(curate((G("a", 850, 1, "x"),), 999)) == [("a",)]


def test_at_most_four_items():
    five = tuple(G(f"p{i}", 200, 10, "x") for i in range(5))
    r = curate(five, 1000)  # only a 5-item basket would reach 850
    assert r.baskets == ()
    assert r.reasons == ("no_basket_in_window",)
    r2 = curate((*five, G("q", 250, 10, "x")), 1000)  # 200*3 + 250 = 850
    assert r2.baskets and all(len(b.product_ids) <= 4 for b in r2.baskets)
    assert ("p0", "p1", "p2", "q") in _ids(r2)


def test_each_candidate_used_at_most_once():
    r = curate((G("a", 500, 10, "x"),), 1000)  # a+a would be 1000
    assert r.baskets == ()


def test_diversity_bonus_breaks_equal_relevance():
    same = curate((G("a", 500, 100, "x"), G("b", 500, 100, "x")), 1000)
    mixed = curate((G("a", 500, 100, "x"), G("b", 500, 100, "y")), 1000)
    assert same.baskets[0].score == 200
    assert mixed.baskets[0].score == 250


def test_shortfall_penalty_prefers_closer_to_budget():
    r = curate((G("near", 990, 100, "x"), G("far", 860, 100, "x")), 1000)
    assert _ids(r) == [("near",), ("far",)]
    # shortfall in whole percent points: 990 => 1 point, 860 => 14 points
    assert [b.score for b in r.baskets] == [100 - 10 * 1, 100 - 10 * 14]


def test_invalid_budget():
    for bad in (0, -5, True, 1000.0, "1000"):
        assert curate(EXAMPLE, bad) == GiftResult((), ("invalid_budget",))  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [
    G("a", 0, 1, "x"),            # price must be > 0
    G("a", 10.5, 1, "x"),         # float price
    G("a", True, 1, "x"),         # bool is not a price
    G("a", 10, -1, "x"),          # negative relevance
    G("a", 10, 1.5, "x"),         # float relevance
    G("", 10, 1, "x"),            # empty id
    G("a", 10, 1, None),          # category must be text
])
def test_invalid_candidate_fails_closed(bad):
    assert curate((*EXAMPLE, bad), 1000) == GiftResult((), ("invalid_candidate",))


def test_duplicate_ids_and_non_tuple_fail_closed():
    assert curate((G("a", 10, 1, "x"), G("a", 20, 1, "y")), 1000).reasons == ("invalid_candidate",)
    assert curate(list(EXAMPLE), 1000).reasons == ("invalid_candidate",)  # type: ignore[arg-type]


def test_no_candidates():
    assert curate((), 1000) == GiftResult((), ("no_candidates",))


def test_more_than_k_candidates_are_truncated_by_relevance():
    # 60 relevant items priced above the budget + one irrelevant exact-fit item:
    # with 61 candidates the lowest-relevance one is dropped BEFORE the search,
    # so no basket exists; with 60 it is kept and is the only basket.
    many = tuple(G(f"p{i:02d}", 1001, 100, "x") for i in range(60))
    exact = G("zz", 1000, 0, "x")
    r = curate((*many, exact), 1000)
    assert r.baskets == ()
    assert r.reasons == ("candidates_truncated", "no_basket_in_window")
    kept = curate((*many[:59], exact), 1000)
    assert kept.reasons == ()
    assert _ids(kept) == [("zz",)]


def test_step_budget_exhaustion_falls_back_to_greedy(monkeypatch):
    monkeypatch.setattr(curator, "MAX_STEPS", 3)
    r = curate(EXAMPLE, 1000)
    # greedy order = relevance desc, price asc, id: C(400), B(450), A(500), D(1000), E(100)
    # => C+B = 850, A and D no longer fit, E fits => 950 (not the optimum A+C+E)
    assert r.reasons == ("search_budget_exhausted", "greedy_fallback")
    assert r.baskets == (GiftBasket(("B", "C", "E"), 950, 410),)


def test_exhaustion_with_no_greedy_basket(monkeypatch):
    monkeypatch.setattr(curator, "MAX_STEPS", 1)
    # greedy takes h(600) first; then m(450) => 1050 and l(500) => 1100 overflow,
    # so greedy stops at 600 < 850 - although the search optimum l+m = 950 exists.
    cands = (G("h", 600, 9, "x"), G("l", 500, 1, "x"), G("m", 450, 1, "y"))
    r = curate(cands, 1000)
    assert r.reasons == ("search_budget_exhausted", "no_basket_in_window")
    assert r.baskets == ()


def test_reasons_are_a_closed_set():
    results = [
        curate(EXAMPLE, 1000), curate((), 1000), curate(EXAMPLE, 0),
        curate((G("a", 1, 1, "x"),), 1000),
    ]
    for r in results:
        assert set(r.reasons) <= REASONS
