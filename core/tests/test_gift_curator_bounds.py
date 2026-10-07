"""P4 Task 13: GiftCurator boundaries, empty input, greedy-fallback quality and the
structural performance bound. Pure and deterministic: seeded corpora, no clock
assertions (H7: no flaky tests). Wall-clock time is MEASURED and logged by the
directive, never asserted here."""
from __future__ import annotations

import random

import pytest

from app.gift import curator
from app.gift.curator import GiftCandidate, GiftResult, curate

G = GiftCandidate
EXHAUSTED_GREEDY = ("search_budget_exhausted", "greedy_fallback")
EXHAUSTED_NONE = ("search_budget_exhausted", "no_basket_in_window")


def _corpus(seed: int, n: int = 200) -> list[tuple[tuple[GiftCandidate, ...], int]]:
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        k = rng.randint(1, 30)
        budget = rng.choice([500, 1000, 2500, 10000])
        cands = tuple(
            G(f"p{j:02d}", rng.randint(max(1, budget // 20), budget), rng.randint(0, 1000),
              f"c{rng.randint(0, 4)}")
            for j in range(k)
        )
        out.append((cands, budget))
    return out


CORPUS = _corpus(2026)


def _in_window(total: int, budget: int) -> bool:
    return -(-budget * curator.WINDOW_LOW_PCT // 100) <= total <= budget


def _well_formed(r: GiftResult, cands: tuple[GiftCandidate, ...], budget: int) -> None:
    by_id = {c.product_id: c for c in cands}
    for b in r.baskets:
        assert 1 <= len(b.product_ids) <= curator.MAX_ITEMS
        assert len(set(b.product_ids)) == len(b.product_ids)
        assert set(b.product_ids) <= set(by_id)
        assert b.total_minor == sum(by_id[i].price_minor for i in b.product_ids)
        assert _in_window(b.total_minor, budget)


# ---- empty / degenerate input ------------------------------------------------

def test_empty_input_is_no_candidates_for_any_valid_budget():
    for budget in (1, 7, 1000, 10**12):
        assert curate((), budget) == GiftResult((), ("no_candidates",))


def test_budget_is_checked_before_candidates():
    assert curate((), 0) == GiftResult((), ("invalid_budget",))
    assert curate((G("", 0, -1, "x"),), -1) == GiftResult((), ("invalid_budget",))


def test_all_candidates_above_budget_is_no_basket_not_no_candidates():
    r = curate((G("a", 1001, 5, "x"), G("b", 5000, 5, "y")), 1000)
    assert r == GiftResult((), ("no_basket_in_window",))


def test_unreachable_window_is_pruned_without_exhausting_the_budget(monkeypatch):
    # 60 items of 10: even the 4 dearest (40) cannot reach 850 => the window
    # bound prunes the root; zero search steps, so even MAX_STEPS=0 is not hit.
    monkeypatch.setattr(curator, "MAX_STEPS", 0)
    cheap = tuple(G(f"p{i:02d}", 10, 100, "x") for i in range(60))
    assert curate(cheap, 1000) == GiftResult((), ("no_basket_in_window",))


# ---- tight budgets -----------------------------------------------------------

def test_budget_of_one_minor_unit():
    assert curate((G("a", 1, 0, "x"),), 1).baskets[0].product_ids == ("a",)
    assert curate((G("a", 2, 0, "x"),), 1).baskets == ()


def test_tiny_window_edges():
    # budget 7 => 85% = 5.95 => window [6, 7]
    assert curate((G("a", 5, 0, "x"),), 7).baskets == ()
    assert [b.total_minor for b in curate((G("a", 6, 0, "x"),), 7).baskets] == [6]
    pair = curate((G("a", 3, 0, "x"), G("b", 4, 0, "y")), 7)
    assert [b.product_ids for b in pair.baskets] == [("a", "b")]


def test_huge_integer_budget_stays_exact():
    budget = 10**15 + 7
    low = -(-budget * 85 // 100)
    r = curate((G("a", low, 0, "x"), G("b", low - 1, 0, "y")), budget)
    assert [b.product_ids for b in r.baskets] == [("a",)]


# ---- greedy fallback: strict validity and quality ----------------------------

def test_greedy_is_always_well_formed_and_labelled(monkeypatch):
    monkeypatch.setattr(curator, "MAX_STEPS", 0)
    for cands, budget in CORPUS:
        r = curate(cands, budget)
        # ("no_basket_in_window",) alone = the search never took a step (the window
        # bound pruned the root, or every candidate is above the budget).
        assert r.reasons in (EXHAUSTED_GREEDY, EXHAUSTED_NONE, ("no_basket_in_window",))
        assert len(r.baskets) == (1 if r.reasons == EXHAUSTED_GREEDY else 0)
        _well_formed(r, cands, budget)


def test_greedy_never_beats_the_exhaustive_optimum(monkeypatch):
    pairs = []
    for cands, budget in CORPUS:
        monkeypatch.setattr(curator, "MAX_STEPS", 10**9)
        best = curate(cands, budget)
        monkeypatch.setattr(curator, "MAX_STEPS", 0)
        greedy = curate(cands, budget)
        _well_formed(best, cands, budget)
        pairs.append((best, greedy))
    for best, greedy in pairs:
        if greedy.baskets:  # a greedy basket is a valid basket => an optimum exists
            assert best.baskets
            assert best.baskets[0].score >= greedy.baskets[0].score


def test_greedy_quality_floor_on_the_seeded_corpus(monkeypatch):
    """Measured on CORPUS (seed 2026, 200 cases): an optimum exists in 181,
    greedy finds a basket in 163 (90.1%), and hits the optimum score exactly in
    65 of those (39.9%). The floors below fail if greedy quality regresses."""
    with_optimum = greedy_found = exact = 0
    for cands, budget in CORPUS:
        monkeypatch.setattr(curator, "MAX_STEPS", 10**9)
        best = curate(cands, budget)
        monkeypatch.setattr(curator, "MAX_STEPS", 0)
        greedy = curate(cands, budget)
        if best.baskets:
            with_optimum += 1
        if greedy.baskets:
            greedy_found += 1
            exact += greedy.baskets[0].score == best.baskets[0].score
    assert with_optimum == 181
    assert greedy_found / with_optimum >= 0.85
    assert exact / greedy_found >= 0.33


def test_exhaustion_result_ignores_input_order(monkeypatch):
    monkeypatch.setattr(curator, "MAX_STEPS", 5)
    rng = random.Random(13)
    for cands, budget in CORPUS[:40]:
        expected = curate(cands, budget)
        shuffled = list(cands)
        rng.shuffle(shuffled)
        assert curate(tuple(shuffled), budget) == expected


# ---- structural performance bound -------------------------------------------

def _worst_case() -> tuple[tuple[GiftCandidate, ...], int]:
    rng = random.Random(1)
    return tuple(
        G(f"p{i:02d}", rng.randint(150, 260), rng.randint(0, 1000), f"c{i % 5}") for i in range(60)
    ), 1000


def test_dense_k60_case_hits_the_step_budget_and_falls_back():
    # ~500k feasible 4-item combinations: the deterministic step budget MUST stop
    # the search (the structural guarantee behind the wall-clock target).
    cands, budget = _worst_case()
    r = curate(cands, budget)
    assert r.reasons == EXHAUSTED_GREEDY
    _well_formed(r, cands, budget)


def test_oversized_input_is_cut_to_k_before_search():
    rng = random.Random(5)
    many = tuple(G(f"p{i:04d}", rng.randint(150, 260), rng.randint(0, 1000), "x") for i in range(1000))
    r = curate(many, 1000)
    assert r.reasons[0] == "candidates_truncated"
    kept = sorted(many, key=lambda c: (-c.relevance, c.product_id))[: curator.K_MAX]
    allowed = {c.product_id for c in kept}
    for b in r.baskets:
        assert set(b.product_ids) <= allowed


@pytest.mark.parametrize("steps", [1, 2, 3, 10, 100])
def test_any_step_budget_gives_a_valid_labelled_result(monkeypatch, steps):
    monkeypatch.setattr(curator, "MAX_STEPS", steps)
    cands, budget = _worst_case()
    r = curate(cands, budget)
    assert r.reasons in (EXHAUSTED_GREEDY, EXHAUSTED_NONE, ())
    _well_formed(r, cands, budget)
