"""P4 Task 9: SizeAdvisor golden table + property tests (§5.1 "الاختبار الذهبي").

Golden: hand-checked cases on the reference chart below, including the disaster
example (95 kg never gets M unless the chart allows it). Properties: exhaustive,
deterministic sweeps over a grid (no random generator, so a failure always
reproduces) - monotonicity in weight, height and chest, the hard weight guard on
every recommended size (alt included), out_of_range consistency and the closed
reason set. No DB, no network: the advisor is pure.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.fit.size_advisor import (
    FIT_PREFS,
    REASONS,
    WEIGHT_TOLERANCE_KG,
    SizeChart,
    SizeRow,
    advise,
)

D = Decimal


def _r(lo: int, hi: int) -> tuple[Decimal, Decimal]:
    return (D(lo), D(hi))


S = SizeRow("S", 1, height_cm=_r(160, 170), weight_kg=_r(50, 62), chest_cm=_r(86, 91))
M = SizeRow("M", 2, height_cm=_r(165, 178), weight_kg=_r(60, 75), chest_cm=_r(91, 97))
L = SizeRow("L", 3, height_cm=_r(172, 185), weight_kg=_r(73, 88), chest_cm=_r(97, 103))
XL = SizeRow("XL", 4, height_cm=_r(178, 192), weight_kg=_r(86, 100), chest_cm=_r(103, 109))
FULL = SizeChart(rows=(S, M, L, XL))
NO_XL = SizeChart(rows=(S, M, L))
ORDER = {"S": 1, "M": 2, "L": 3, "XL": 4}
ROWS = {r.label: r for r in (S, M, L, XL)}


def _t(a):
    return (a.size, a.alt_size, a.confidence, a.reasons, a.out_of_range)


HW = ("height_weight_match",)
MULTI = ("multiple_matches_fit_pref",)
NEAR = ("nearest_out_of_range",)

# (id, chart, height, weight, fit_pref, chest, expected)
GOLDEN = [
    ("g01 middle of S", FULL, 162, 55, "regular", None, ("S", None, "medium", HW, False)),
    ("g02 middle of M", FULL, 168, 68, "regular", None, ("M", None, "medium", HW, False)),
    ("g03 S/M overlap fitted", FULL, 166, 61, "fitted", None, ("S", "M", "medium", MULTI, False)),
    ("g04 S/M overlap loose", FULL, 166, 61, "loose", None, ("M", "S", "medium", MULTI, False)),
    ("g05 L/XL overlap loose", FULL, 180, 87, "loose", None, ("XL", "L", "medium", MULTI, False)),
    ("g06 95 kg, no XL: guard", NO_XL, 175, 95, "regular", None,
     (None, None, "low", ("heavier_than_chart",), True)),
    ("g07 95 kg with XL", FULL, 180, 95, "regular", None, ("XL", None, "medium", HW, False)),
    ("g08 95 kg short: never M", FULL, 170, 95, "regular", None, ("XL", None, "low", NEAR, True)),
    ("g09 90 kg, no XL: 88+2 tolerance", NO_XL, 180, 90, "regular", None, ("L", None, "low", NEAR, True)),
    ("g10 91 kg, no XL: past tolerance", NO_XL, 180, 91, "regular", None,
     (None, None, "low", ("heavier_than_chart",), True)),
    ("g11 small and light", FULL, 150, 45, "regular", None, ("S", None, "low", NEAR, True)),
    ("g12 tall and heavy", FULL, 200, 100, "regular", None, ("XL", None, "low", NEAR, True)),
    ("g13 chest decides M", FULL, None, None, "regular", 90,
     ("M", "L", "medium", ("measurements_match",), False)),
    ("g14 chest agrees with h/w", FULL, 168, 68, "regular", 90,
     ("M", "L", "high", ("measurements_match",), False)),
    ("g15 chest too big for every size", FULL, None, None, "regular", 120,
     (None, None, "low", ("too_small_for_measurements",), True)),
    ("g16 nothing to measure", FULL, None, 70, "regular", None,
     (None, None, "low", ("missing_height_weight",), False)),
    ("g17 implausible weight", FULL, 170, 20, "regular", None,
     (None, None, "low", ("implausible_input",), False)),
]


@pytest.mark.parametrize(
    ("cid", "chart", "h", "w", "pref", "chest", "expected"), GOLDEN, ids=[g[0] for g in GOLDEN],
)
def test_golden(cid, chart, h, w, pref, chest, expected):
    a = advise(chart, height_cm=None if h is None else D(h), weight_kg=None if w is None else D(w),
               fit_pref=pref, body_cm=None if chest is None else {"chest": D(chest)})
    assert _t(a) == expected


def test_95kg_gets_m_only_when_the_chart_allows_it():
    # §5.1 disaster example: never M by default; the merchant's explicit opt-out changes the guard.
    for h in range(165, 179):
        assert advise(FULL, height_cm=D(h), weight_kg=D(95)).size != "M"
    opted = SizeChart(rows=(S, M), allow_under_weight=True)
    assert advise(opted, height_cm=D(170), weight_kg=D(95)).size == "M"


# ---- properties (exhaustive grid sweeps) ---------------------------------------

HEIGHTS = range(150, 201, 2)
WEIGHTS = range(40, 106)


def _rank(a) -> int | None:
    return ORDER[a.size] if a.size is not None else None


@pytest.mark.parametrize("pref", FIT_PREFS)
@pytest.mark.parametrize("chart", [FULL, NO_XL], ids=["full", "no_xl"])
def test_more_weight_never_gives_a_smaller_size(pref, chart):
    for h in HEIGHTS:
        last = None
        for w in WEIGHTS:
            rank = _rank(advise(chart, height_cm=D(h), weight_kg=D(w), fit_pref=pref))
            if rank is not None:
                assert last is None or rank >= last, (h, w, pref)
                last = rank


@pytest.mark.parametrize("pref", FIT_PREFS)
def test_more_height_never_gives_a_smaller_size(pref):
    for w in WEIGHTS[::3]:
        last = None
        for h in range(150, 201):
            rank = _rank(advise(FULL, height_cm=D(h), weight_kg=D(w), fit_pref=pref))
            if rank is not None:
                assert last is None or rank >= last, (h, w, pref)
                last = rank


@pytest.mark.parametrize("pref", FIT_PREFS)
def test_bigger_chest_never_gives_a_smaller_size(pref):
    last = None
    for c in range(70, 121):
        rank = _rank(advise(FULL, body_cm={"chest": D(c)}, fit_pref=pref))
        if rank is not None:
            assert last is None or rank >= last, (c, pref)
            last = rank


@pytest.mark.parametrize("chart", [FULL, NO_XL], ids=["full", "no_xl"])
def test_weight_guard_holds_for_every_recommended_size(chart):
    # §5.1 step 3: no recommended size (alt included) whose max weight is below
    # the customer's weight minus the tolerance.
    for h in HEIGHTS:
        for w in WEIGHTS:
            for pref in FIT_PREFS:
                a = advise(chart, height_cm=D(h), weight_kg=D(w), fit_pref=pref)
                for label in (a.size, a.alt_size):
                    if label is not None:
                        assert ROWS[label].weight_kg[1] >= D(w) - WEIGHT_TOLERANCE_KG, (h, w, pref, label)


def test_out_of_range_flag_matches_its_reasons_and_reasons_stay_closed():
    flagging = {"nearest_out_of_range", "heavier_than_chart", "too_small_for_measurements"}
    for h in HEIGHTS:
        for w in WEIGHTS[::2]:
            a = advise(FULL, height_cm=D(h), weight_kg=D(w))
            assert set(a.reasons) <= REASONS
            assert a.out_of_range == bool(flagging & set(a.reasons)), (h, w, a)
            assert a.size is None or a.size in ORDER
            assert a.alt_size is None or (a.size is not None and a.alt_size != a.size)


def test_same_input_same_output():
    for h, w in ((166, 61), (180, 87), (175, 95)):
        first = advise(FULL, height_cm=D(h), weight_kg=D(w))
        assert all(advise(FULL, height_cm=D(h), weight_kg=D(w)) == first for _ in range(5))
