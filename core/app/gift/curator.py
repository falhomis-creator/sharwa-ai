"""GiftCurator solver (docs/01_FIVE_TASKS_DESIGN.md §5.2) - deterministic, pure.

Input: at most K candidates (already filtered upstream: in stock, allowed for
gifts) with an INTEGER price in the store's minor units, an integer relevance
score and a category; and an integer budget in the same minor units. sharwa_ai
never converts currency and never computes a checkout total - the platform does
(P4 constitution §3). This module only picks WHICH items.

Output: up to TOP_N baskets of 1..MAX_ITEMS distinct candidates whose exact
total lies in the window [ceil(budget * 85%), budget]. Never "the nearest
basket" outside the window: no basket in the window => no basket (§2).

Search (architect decision, Task 12): exact bounded enumeration over candidates
sorted by (price, id), pruned by the window, under a DETERMINISTIC step budget
(MAX_STEPS node expansions) instead of a quantized-price DP and a wall clock:
- the window guarantee stays exact (quantized buckets can admit a basket whose
  real total is outside the window);
- the category-diversity bonus is not additive, which a (count, bucket) DP
  cannot carry exactly;
- same input => same output on any machine (H45); the wall-clock timeout and
  the process pool belong to the caller (Task 14), not to this pure function.
When the step budget runs out the result is the greedy fallback alone.

Score (written defaults, integers only):
    sum(relevance) + DIVERSITY_BONUS * (distinct categories - 1)
    - SHORTFALL_PENALTY * percent points the total falls short of the budget
Ranking: score desc, then total desc (closer to the budget), then item ids asc.
"""
from __future__ import annotations

from dataclasses import dataclass

K_MAX = 60
MAX_ITEMS = 4
TOP_N = 3
WINDOW_LOW_PCT = 85
MAX_STEPS = 40_000
DIVERSITY_BONUS = 50
SHORTFALL_PENALTY = 10

REASONS: frozenset[str] = frozenset({
    "invalid_budget", "invalid_candidate", "no_candidates", "no_basket_in_window",
    "candidates_truncated", "search_budget_exhausted", "greedy_fallback",
})


@dataclass(frozen=True)
class GiftCandidate:
    product_id: str
    price_minor: int
    relevance: int
    category: str


@dataclass(frozen=True)
class GiftBasket:
    product_ids: tuple[str, ...]
    total_minor: int
    score: int


@dataclass(frozen=True)
class GiftResult:
    baskets: tuple[GiftBasket, ...]
    reasons: tuple[str, ...]


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _window(budget_minor: int) -> tuple[int, int]:
    low = -(-budget_minor * WINDOW_LOW_PCT // 100)  # ceil without floats
    return low, budget_minor


def _valid(c: object) -> bool:
    return (
        isinstance(c, GiftCandidate)
        and isinstance(c.product_id, str) and bool(c.product_id)
        and _is_int(c.price_minor) and c.price_minor > 0
        and _is_int(c.relevance) and c.relevance >= 0
        and isinstance(c.category, str)
    )


def _basket(items: list[GiftCandidate], budget_minor: int) -> GiftBasket:
    total = sum(c.price_minor for c in items)
    shortfall_pct = (budget_minor - total) * 100 // budget_minor
    score = (
        sum(c.relevance for c in items)
        + DIVERSITY_BONUS * (len({c.category for c in items}) - 1)
        - SHORTFALL_PENALTY * shortfall_pct
    )
    return GiftBasket(tuple(sorted(c.product_id for c in items)), total, score)


def _rank_key(b: GiftBasket) -> tuple[int, int, tuple[str, ...]]:
    return (-b.score, -b.total_minor, b.product_ids)


def _greedy(cands: list[GiftCandidate], budget_minor: int) -> GiftBasket | None:
    low, high = _window(budget_minor)
    chosen: list[GiftCandidate] = []
    total = 0
    for c in sorted(cands, key=lambda c: (-c.relevance, c.price_minor, c.product_id)):
        if len(chosen) == MAX_ITEMS:
            break
        if total + c.price_minor <= high:
            chosen.append(c)
            total += c.price_minor
    if chosen and total >= low:
        return _basket(chosen, budget_minor)
    return None


def curate(candidates: tuple[GiftCandidate, ...], budget_minor: int) -> GiftResult:
    """The best TOP_N baskets in the window, or none - never a guess (H52)."""
    if not _is_int(budget_minor) or budget_minor <= 0:
        return GiftResult((), ("invalid_budget",))
    if not isinstance(candidates, tuple) or any(not _valid(c) for c in candidates):
        return GiftResult((), ("invalid_candidate",))
    if len({c.product_id for c in candidates}) != len(candidates):
        return GiftResult((), ("invalid_candidate",))
    if not candidates:
        return GiftResult((), ("no_candidates",))

    reasons: list[str] = []
    cands = list(candidates)
    if len(cands) > K_MAX:
        cands = sorted(cands, key=lambda c: (-c.relevance, c.product_id))[:K_MAX]
        reasons.append("candidates_truncated")

    low, high = _window(budget_minor)
    ordered = sorted(cands, key=lambda c: (c.price_minor, c.product_id))
    found: list[GiftBasket] = []
    steps = 0
    exhausted = False

    prices = [c.price_minor for c in ordered]
    n = len(prices)

    # dearest[r] = the sum of the r dearest candidates (sorted ascending: the last r).
    dearest = [sum(prices[n - r:]) if r else 0 for r in range(MAX_ITEMS + 1)]

    def reachable(start: int, slots: int, total: int) -> bool:
        """Even the `slots` dearest remaining candidates cannot lift the total
        into the window => this whole branch is dead (window pruning)."""
        take = min(slots, n - start)
        return take > 0 and total + dearest[take] >= low

    def walk(start: int, chosen: list[GiftCandidate], total: int) -> None:
        nonlocal steps, exhausted
        if not reachable(start, MAX_ITEMS - len(chosen), total):
            return
        for j in range(start, n):
            c = ordered[j]
            if total + c.price_minor > high:
                break  # sorted by price: every later candidate is dearer
            steps += 1
            if steps > MAX_STEPS:
                exhausted = True
                return
            chosen.append(c)
            new_total = total + c.price_minor
            if new_total >= low:
                basket = _basket(chosen, budget_minor)
                if len(found) < TOP_N or _rank_key(basket) < _rank_key(found[-1]):
                    found.append(basket)
                    found.sort(key=_rank_key)
                    del found[TOP_N:]
            if len(chosen) < MAX_ITEMS:
                walk(j + 1, chosen, new_total)
            chosen.pop()
            if exhausted:
                return

    walk(0, [], 0)

    if exhausted:
        reasons.append("search_budget_exhausted")
        greedy = _greedy(cands, budget_minor)
        if greedy is None:
            return GiftResult((), (*reasons, "no_basket_in_window"))
        return GiftResult((greedy,), (*reasons, "greedy_fallback"))
    if not found:
        return GiftResult((), (*reasons, "no_basket_in_window"))
    return GiftResult(tuple(found), tuple(reasons))
