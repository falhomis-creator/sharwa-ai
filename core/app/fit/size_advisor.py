"""SizeAdvisor (docs/01_FIVE_TASKS_DESIGN.md §5.1) - deterministic, pure.

The model only extracts numbers and phrases the answer from `reasons`; it can never
change the size (P4 constitution §1-2). No chart, a malformed chart, implausible input
or nothing to measure => no size (`size=None`, confidence "low": ask or hand off).
Two branches return no size WITH `out_of_range` (not "ask"): the hard weight guard (no
size whose maximum weight is below the customer's weight minus the tolerance - the
"95 kg never gets M" rule) and too-large-for-every-size measurements. Otherwise outside
every range => the nearest size WITH an explicit `out_of_range` flag. The measurements
path compares the garment minimum against `need` (measurement + ease), not the raw
measurement, and flags `out_of_range` when it exceeds need by more than SLACK_CM (strict >).

Chart ranges are BODY ranges in cm/kg as the merchant enters them in Sharwa
(OQ-P4-05). The ease values below are written defaults (OQ-P4-05), not measurements;
the returns loop tunes them later by report, never automatically.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

FitPref = Literal["fitted", "regular", "loose"]
FitType = Literal["slim", "regular", "oversized"]
Confidence = Literal["high", "medium", "low"]
Range = tuple[Decimal, Decimal]

FIT_PREFS: tuple[str, ...] = ("fitted", "regular", "loose")
FIT_TYPES: tuple[str, ...] = ("slim", "regular", "oversized")
BODY_DIMS: tuple[str, ...] = ("chest", "waist", "hips")

HEIGHT_CM_MIN, HEIGHT_CM_MAX = Decimal("100"), Decimal("230")
WEIGHT_KG_MIN, WEIGHT_KG_MAX = Decimal("30"), Decimal("250")
WEIGHT_TOLERANCE_KG = Decimal("2")
PREF_EASE_CM: dict[str, Decimal] = {"fitted": Decimal("0"), "regular": Decimal("2"), "loose": Decimal("6")}
FIT_TYPE_EASE_CM: dict[str, Decimal] = {
    "slim": Decimal("2"), "regular": Decimal("0"), "oversized": Decimal("-4"),
}
SLACK_CM = Decimal("2")
DIM_WEIGHT: dict[str, Decimal] = {"chest": Decimal("1"), "waist": Decimal("1"), "hips": Decimal("1")}

# The closed set of reason codes (the template layer and the Verifier key on these).
REASONS: frozenset[str] = frozenset({
    "no_chart", "invalid_chart", "implausible_input", "missing_height_weight",
    "height_weight_match", "multiple_matches_fit_pref", "nearest_out_of_range",
    "heavier_than_chart", "measurements_match", "too_small_for_measurements",
    "measurements_disagree_height_weight",
})


@dataclass(frozen=True)
class SizeRow:
    label: str
    sort_order: int
    height_cm: Range | None = None
    weight_kg: Range | None = None
    chest_cm: Range | None = None
    waist_cm: Range | None = None
    hips_cm: Range | None = None

    def dim(self, name: str) -> Range | None:
        value: Range | None = getattr(self, f"{name}_cm")
        return value


@dataclass(frozen=True)
class SizeChart:
    rows: tuple[SizeRow, ...]
    fit_type: str = "regular"
    stretch_pct: Decimal = Decimal("0")
    allow_under_weight: bool = False  # the merchant's explicit opt-out of the weight guard


@dataclass(frozen=True)
class SizeAdvice:
    size: str | None
    alt_size: str | None
    confidence: Confidence
    reasons: tuple[str, ...]
    out_of_range: bool


def _none(reason: str, *, out_of_range: bool = False) -> SizeAdvice:
    return SizeAdvice(size=None, alt_size=None, confidence="low", reasons=(reason,),
                      out_of_range=out_of_range)


def _chart_is_valid(chart: SizeChart) -> bool:
    if chart.fit_type not in FIT_TYPES or not (Decimal("0") <= chart.stretch_pct <= Decimal("50")):
        return False
    labels = [r.label for r in chart.rows]
    orders = [r.sort_order for r in chart.rows]
    if len(set(labels)) != len(labels) or len(set(orders)) != len(orders) or any(not lb for lb in labels):
        return False
    rows = sorted(chart.rows, key=lambda r: r.sort_order)
    for r in rows:
        for rng in (r.height_cm, r.weight_kg, r.chest_cm, r.waist_cm, r.hips_cm):
            if rng is not None and rng[0] > rng[1]:
                return False
    # Bigger sizes never have smaller height/weight bounds (monotone chart).
    for attr in ("height_cm", "weight_kg"):
        ranged = [getattr(r, attr) for r in rows if getattr(r, attr) is not None]
        for a, b in itertools.pairwise(ranged):
            if b[0] < a[0] or b[1] < a[1]:
                return False
    return True


def _width(rng: Range) -> Decimal:
    return max(rng[1] - rng[0], Decimal("1"))


def _outside(x: Decimal, rng: Range) -> Decimal:
    if x < rng[0]:
        return (rng[0] - x) / _width(rng)
    if x > rng[1]:
        return (x - rng[1]) / _width(rng)
    return Decimal("0")


def _outside_kg_cm(x: Decimal, rng: Range) -> Decimal:
    """Raw distance outside a range, in the range's own unit (kg or cm)."""
    return max(rng[0] - x, x - rng[1], Decimal("0"))


def _from_mid(x: Decimal, rng: Range) -> Decimal:
    return abs(x - (rng[0] + rng[1]) / 2) / _width(rng)


def _hw(row: SizeRow) -> tuple[Range, Range]:
    assert row.height_cm is not None and row.weight_kg is not None
    return row.height_cm, row.weight_kg


def _fits(row: SizeRow, need: dict[str, Decimal], stretch: Decimal) -> bool:
    for d, n in need.items():
        rng = row.dim(d)
        if rng is not None and rng[1] * stretch < n:
            return False
    return True


def _score(row: SizeRow, need: dict[str, Decimal]) -> Decimal:
    total = Decimal("0")
    for d, n in need.items():
        rng = row.dim(d)
        if rng is not None:
            total += DIM_WEIGHT[d] * max(Decimal("0"), rng[0] - n - SLACK_CM) ** 2
    return total


def _best(rows: list[SizeRow], key: dict[str, Decimal], *, prefer_smaller: bool = False) -> list[SizeRow]:
    """Lowest key first; a tie goes to the LARGER size (the SMALLER one when asked)."""
    return sorted(rows, key=lambda r: (key[r.label], r.sort_order if prefer_smaller else -r.sort_order))


def advise(
    chart: SizeChart | None,
    *,
    height_cm: Decimal | None = None,
    weight_kg: Decimal | None = None,
    body_cm: dict[str, Decimal] | None = None,
    fit_pref: str = "regular",
) -> SizeAdvice:
    if chart is None or not chart.rows:
        return _none("no_chart")
    if not _chart_is_valid(chart) or fit_pref not in FIT_PREFS:
        return _none("invalid_chart")
    body = dict(body_cm or {})
    if (height_cm is not None and not (HEIGHT_CM_MIN <= height_cm <= HEIGHT_CM_MAX)) \
            or (weight_kg is not None and not (WEIGHT_KG_MIN <= weight_kg <= WEIGHT_KG_MAX)) \
            or any(k not in BODY_DIMS or not (Decimal("30") <= v <= Decimal("250")) for k, v in body.items()):
        return _none("implausible_input")

    rows = sorted(chart.rows, key=lambda r: r.sort_order)
    # Hard weight guard (§5.1 step 3).
    pool = rows
    if weight_kg is not None and not chart.allow_under_weight:
        pool = [r for r in rows if r.weight_kg is None or r.weight_kg[1] >= weight_kg - WEIGHT_TOLERANCE_KG]
        if not pool:
            return _none("heavier_than_chart", out_of_range=True)

    # Step 1: height + weight.
    hw_rows = [r for r in pool if r.height_cm is not None and r.weight_kg is not None]
    in_range: list[SizeRow] = []
    if height_cm is not None and weight_kg is not None and hw_rows:
        in_range = [r for r in hw_rows
                    if _outside(height_cm, _hw(r)[0]) == 0 and _outside(weight_kg, _hw(r)[1]) == 0]

    # Step 2: body measurements refine (hard fit, then soft score).
    if body:
        ease = max(Decimal("0"), PREF_EASE_CM[fit_pref] + FIT_TYPE_EASE_CM[chart.fit_type])
        need = {d: v + ease for d, v in body.items()}
        stretch = Decimal("1") + chart.stretch_pct / Decimal("100")
        measurable = [r for r in pool if any(r.dim(d) is not None for d in need)]
        if measurable:
            fits = [r for r in measurable if _fits(r, need, stretch)]
            if not fits:
                return _none("too_small_for_measurements", out_of_range=True)
            scores = {r.label: _score(r, need) for r in fits}
            ranked = _best(fits, scores, prefer_smaller=fit_pref == "fitted")
            chosen = ranked[0]
            alt = ranked[1].label if len(ranked) > 1 else None
            # The chosen size's garment minimum far ABOVE `need` means the customer is
            # much smaller than this size => out of range, exactly as the height/weight
            # path flags it. Compared against `need` (measurement + ease), not the raw
            # measurement; strict > so equality does not flag.
            for d, n in need.items():
                rng = chosen.dim(d)
                if rng is not None and rng[0] - n > SLACK_CM:
                    return SizeAdvice(chosen.label, alt, "low", ("nearest_out_of_range",), True)
            if height_cm is not None and weight_kg is not None and in_range:
                if chosen in in_range:
                    return SizeAdvice(chosen.label, alt, "high", ("measurements_match",), False)
                return SizeAdvice(chosen.label, alt, "medium",
                                  ("measurements_match", "measurements_disagree_height_weight"), False)
            return SizeAdvice(chosen.label, alt, "medium", ("measurements_match",), False)

    if height_cm is None or weight_kg is None or not hw_rows:
        return _none("missing_height_weight")

    if len(in_range) == 1:
        return SizeAdvice(in_range[0].label, None, "medium", ("height_weight_match",), False)
    if in_range:
        if fit_pref == "fitted":
            ordered = in_range
        elif fit_pref == "loose":
            ordered = list(reversed(in_range))
        else:
            mid = {r.label: _from_mid(height_cm, _hw(r)[0]) + _from_mid(weight_kg, _hw(r)[1])
                   for r in in_range}
            ordered = _best(in_range, mid)
        return SizeAdvice(ordered[0].label, ordered[1].label, "medium", ("multiple_matches_fit_pref",), False)

    # F-P4-16 (Task 9 property test): the nearest size is chosen by WEIGHT first
    # (kg outside the range), height only breaking a tie (cm outside), then the
    # larger size. The earlier sum of width-normalised distances was not monotonic:
    # a 40 kg / 176 cm customer got M while a 47 kg one got S (§5.1: more weight
    # must never give a smaller size). Weight leads because it is what the hard
    # guard protects; the answer stays flagged out_of_range either way.
    nearest = sorted(hw_rows, key=lambda r: (
        _outside_kg_cm(weight_kg, _hw(r)[1]), _outside_kg_cm(height_cm, _hw(r)[0]), -r.sort_order,
    ))[0]
    return SizeAdvice(nearest.label, None, "low", ("nearest_out_of_range",), True)