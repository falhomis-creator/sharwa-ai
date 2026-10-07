"""P4 Task 8: SizeAdvisor (§5.1) - deterministic, pure. One test per branch; the golden
table and the monotonicity property are Task 9."""
from __future__ import annotations

import random
from decimal import Decimal

from app.fit.size_advisor import REASONS, SizeAdvice, SizeChart, SizeRow, advise

D = Decimal  # short alias for the tables below


def _r(lo: str, hi: str) -> tuple[Decimal, Decimal]:
    return (D(lo), D(hi))


S = SizeRow("S", 1, height_cm=_r("160", "170"), weight_kg=_r("50", "62"), chest_cm=_r("86", "91"))
M = SizeRow("M", 2, height_cm=_r("165", "178"), weight_kg=_r("60", "75"), chest_cm=_r("91", "97"))
L = SizeRow("L", 3, height_cm=_r("172", "185"), weight_kg=_r("73", "88"), chest_cm=_r("97", "103"))
XL = SizeRow("XL", 4, height_cm=_r("178", "192"), weight_kg=_r("86", "100"), chest_cm=_r("103", "109"))
FULL = SizeChart(rows=(S, M, L, XL))
NO_XL = SizeChart(rows=(S, M, L))
MULTI = ("multiple_matches_fit_pref",)
NEAREST = ("nearest_out_of_range",)


def _t(a: SizeAdvice) -> tuple[object, ...]:
    return (a.size, a.alt_size, a.confidence, a.reasons, a.out_of_range)


def _none(reason: str, out_of_range: bool = False) -> tuple[object, ...]:
    return (None, None, "low", (reason,), out_of_range)


def test_no_chart_means_no_size():
    for chart in (None, SizeChart(rows=())):
        assert _t(advise(chart, height_cm=D(175), weight_kg=D(70))) == _none("no_chart")


def test_invalid_chart_is_refused():
    bad = [
        SizeChart(rows=(S, SizeRow("S", 9))),                          # duplicate label
        SizeChart(rows=(SizeRow("A", 1, weight_kg=_r("70", "60")),)),  # lo > hi
        SizeChart(rows=(SizeRow("A", 1, weight_kg=_r("60", "80")),
                        SizeRow("B", 2, weight_kg=_r("50", "70")))),   # bigger size, smaller bounds
        SizeChart(rows=(S,), fit_type="baggy"),
        SizeChart(rows=(S,), stretch_pct=D(60)),
    ]
    for chart in bad:
        assert _t(advise(chart, height_cm=D(165), weight_kg=D(55))) == _none("invalid_chart")
    assert _t(advise(FULL, height_cm=D(165), weight_kg=D(55), fit_pref="tight")) == _none("invalid_chart")


def test_implausible_input_is_refused():
    hw = {"height_cm": D(175), "weight_kg": D(70)}
    for kw in ({"height_cm": D(90), "weight_kg": D(70)}, {"height_cm": D(175), "weight_kg": D(300)},
               {**hw, "body_cm": {"chest": D(10)}}, {**hw, "body_cm": {"neck": D(40)}}):
        assert _t(advise(FULL, **kw)) == _none("implausible_input")


def test_single_height_weight_match():
    a = advise(FULL, height_cm=D(168), weight_kg=D(55))
    assert _t(a) == ("S", None, "medium", ("height_weight_match",), False)


def test_multiple_matches_follow_fit_pref():
    kw = {"height_cm": D(175), "weight_kg": D(74)}  # inside both M and L
    assert _t(advise(FULL, fit_pref="fitted", **kw)) == ("M", "L", "medium", MULTI, False)
    assert _t(advise(FULL, fit_pref="loose", **kw)) == ("L", "M", "medium", MULTI, False)
    # regular: equally far from both mid-points -> the tie goes to the larger size
    assert _t(advise(FULL, fit_pref="regular", **kw)) == ("L", "M", "medium", MULTI, False)


def test_95kg_never_gets_m():
    assert _t(advise(NO_XL, height_cm=D(175), weight_kg=D(95))) == _none("heavier_than_chart", True)
    a = advise(FULL, height_cm=D(180), weight_kg=D(95))
    assert _t(a) == ("XL", None, "medium", ("height_weight_match",), False)


def test_outside_every_range_is_flagged_not_guessed():
    assert _t(advise(FULL, height_cm=D(150), weight_kg=D(45))) == ("S", None, "low", NEAREST, True)


def test_merchant_can_opt_out_of_the_weight_guard():
    chart = SizeChart(rows=(S, M, L), allow_under_weight=True)
    assert _t(advise(chart, height_cm=D(180), weight_kg=D(95))) == ("L", None, "low", NEAREST, True)


def test_missing_height_or_weight_asks():
    assert _t(advise(FULL, weight_kg=D(70))) == _none("missing_height_weight")
    assert _t(advise(FULL, height_cm=D(170))) == _none("missing_height_weight")


def test_measurements_that_agree_are_high_confidence():
    a = advise(FULL, height_cm=D(175), weight_kg=D(70), body_cm={"chest": D(90)})
    assert _t(a) == ("M", "L", "high", ("measurements_match",), False)


def test_measurements_that_disagree_win_with_medium_confidence():
    a = advise(FULL, height_cm=D(175), weight_kg=D(70), body_cm={"chest": D(96)})
    reasons = ("measurements_match", "measurements_disagree_height_weight")
    assert _t(a) == ("L", "XL", "medium", reasons, False)


def test_stretch_lets_a_smaller_size_fit():
    kw = {"body_cm": {"chest": D(99)}, "fit_pref": "fitted"}
    assert advise(FULL, **kw).size == "L"
    assert advise(SizeChart(rows=(S, M, L, XL), stretch_pct=D(5)), **kw).size == "M"


def test_too_small_for_measurements():
    a = advise(FULL, height_cm=D(185), weight_kg=D(90), body_cm={"chest": D(120)})
    assert _t(a) == _none("too_small_for_measurements", True)


def test_reasons_stay_in_the_closed_set():
    labels = {"S", "M", "L", "XL", None}
    for h in range(100, 231, 10):
        for w in range(30, 251, 10):
            for pref in ("fitted", "regular", "loose"):
                a = advise(FULL, height_cm=D(h), weight_kg=D(w), fit_pref=pref)
                assert set(a.reasons) <= REASONS and a.size in labels and a.alt_size in labels


def test_row_order_does_not_change_the_answer():
    rows = [S, M, L, XL]
    kw = {"height_cm": D(175), "weight_kg": D(74), "body_cm": {"chest": D(95)}}
    expected = _t(advise(FULL, **kw))
    rnd = random.Random(8)
    for _ in range(10):
        rnd.shuffle(rows)
        assert _t(advise(SizeChart(rows=tuple(rows)), **kw)) == expected


def test_measurements_reasons_stay_in_closed_set():
    labels = {"S", "M", "L", "XL", None}
    for chest in range(80, 121, 5):
        for pref in ("fitted", "regular", "loose"):
            a = advise(FULL, height_cm=D(175), weight_kg=D(70),
                       body_cm={"chest": D(chest)}, fit_pref=pref)
            assert set(a.reasons) <= REASONS and a.size in labels and a.alt_size in labels


def test_measurements_far_below_smallest_size_flagged():
    a = advise(FULL, height_cm=D(175), weight_kg=D(55), body_cm={"chest": D(62)})
    assert _t(a) == ("S", "M", "low", ("nearest_out_of_range",), True)


def test_measurements_exact_boundary_not_flagged():
    # need == rng[0] - SLACK_CM: chest 82 (regular ease 2) => need 84; S min 86; 86-84 == 2 == SLACK (not >)
    a = advise(FULL, height_cm=D(175), weight_kg=D(55), body_cm={"chest": D(82)})
    assert a.out_of_range is False


def test_measurements_within_range_not_flagged():
    a = advise(FULL, height_cm=D(175), weight_kg=D(55), body_cm={"chest": D(95)})
    assert a.out_of_range is False


def test_two_dims_one_far_below_flagged():
    chart = SizeChart(rows=(
        SizeRow("S", 1, chest_cm=_r("86", "91"), waist_cm=_r("66", "71")),
        SizeRow("M", 2, chest_cm=_r("91", "97"), waist_cm=_r("71", "77")),
        SizeRow("L", 3, chest_cm=_r("97", "103"), waist_cm=_r("77", "83")),
    ))
    a = advise(chart, height_cm=D(175), weight_kg=D(70), body_cm={"chest": D(90), "waist": D(55)})
    assert a.out_of_range is True
    assert a.reasons == ("nearest_out_of_range",)