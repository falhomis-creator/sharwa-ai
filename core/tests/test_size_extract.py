"""P4 Task 11: the deterministic height/weight extractor (H51) - pure.

A number counts only when a keyword hugs it (a unit from either side, a field
word from the left); a bare number is dropped, two different values for one
field make it None, and plausibility bounds are NOT checked here (they stay in
size_advisor.advise). Conversions are exact Decimals: 1 lb = 0.45359237 kg,
1 inch = 2.54 cm, 1 foot = 30.48 cm, 1 m = 100 cm.
"""
from __future__ import annotations

from decimal import Decimal

from app.tools import size_extract
from app.tools.size_extract import extract_size_inputs

D = Decimal


def _h(texts: tuple[str, ...]) -> Decimal | None:
    return extract_size_inputs(texts).height_cm


def _w(texts: tuple[str, ...]) -> Decimal | None:
    return extract_size_inputs(texts).weight_kg


def test_cm_and_kg():
    assert _h(("طولي 175 سم",)) == D("175")
    assert _h(("175 سم",)) == D("175")
    assert _w(("وزني 95 كيلو",)) == D("95")
    assert _w(("وزني 95 كجم",)) == D("95")


def test_meter():
    assert _h(("طولي 1.75 متر",)) == D("175")
    assert _h(("طولي ١٫٧٥ متر",)) == D("175")  # Arabic decimal separator


def test_feet_and_inches_compound():
    assert _h(("طولي 5 قدم و7 انش",)) == D("170.18")
    assert _h(("5 feet 7 inches",)) == D("170.18")
    assert _h(("5 قدم 7 انش",)) == D("170.18")


def test_number_then_unit_belongs_to_that_number():
    # R1: a unit AFTER a number is that number's - never borrowed as the next
    # number's left unit ("175cm 80kg" must read BOTH numbers).
    assert _h(("175cm 80kg",)) == D("175")
    assert _w(("175cm 80kg",)) == D("80")
    assert _h(("175 سم 80 كيلو",)) == D("175")
    assert _w(("175 سم 80 كيلو",)) == D("80")
    assert _h(("80kg 175cm",)) == D("175")
    assert _w(("80kg 175cm",)) == D("80")
    assert _h(("طولي 175 ووزني 80",)) == D("175")
    assert _w(("طولي 175 ووزني 80",)) == D("80")
    assert _w(("وزني 80 كيلو وطولي 175 سم",)) == D("80")
    assert _h(("وزني 80 كيلو وطولي 175 سم",)) == D("175")


def test_unit_before_number_without_preceding_number():
    # R1: "سم 175" (unit-first) stays supported when no number precedes the unit.
    assert _h(("سم 175",)) == D("175")


def test_meter_shorthand_only_for_small_values():
    # R2: m/م mean meters only when the value is <= 3 (else they collide with
    # the size M); then the field word, if any, decides.
    assert _h(("175 M",)) is None
    assert _w(("وزني 80 M",)) == D("80")
    assert _h(("1.75 م",)) == D("175")
    assert _h(("1.75 m",)) == D("175")
    assert _h(("الساعة 5 م",)) is None
    assert _h(("1.75 متر",)) == D("175")


def test_ambiguous_feet_and_fractional_feet_ask():
    # R3: feet followed (after an optional connector) by a bare trailing
    # number, or fractional feet ("5.7 قدم" = 5'7"), contribute nothing.
    assert _h(("5 قدم و7",)) is None
    assert _h(("5.7 قدم",)) is None
    assert _h(("5.5 قدم",)) is None


def test_feet_followed_by_own_unit_number_is_not_ambiguous():
    # R6: the trailing number after feet is "bare" only when it has NO known
    # unit of its own - "6 قدم 80 كيلو" / "6 feet 180 lbs" read BOTH numbers.
    assert _h(("6 قدم 80 كيلو",)) == D("182.88")
    assert _w(("6 قدم 80 كيلو",)) == D("80")
    assert _h(("6 feet 180 lbs",)) == D("182.88")
    assert _w(("6 feet 180 lbs",)) == D("180") * D("0.45359237")


def test_meter_shorthand_excludes_integers():
    # R7: m/م read as meters only for a NON-integer value in [1.00, 2.50]
    # (a body height like 1.75) - "ابغى 2 M" is a quantity, never 200 cm.
    assert _h(("2 M",)) is None
    assert _h(("1 M",)) is None
    assert _h(("ابغى 2 M",)) is None
    assert _h(("1.75 م",)) == D("175")
    assert _h(("1.75 m",)) == D("175")
    assert _h(("1 متر",)) == D("100")
    assert _h(("طولي 175 M",)) == D("175")


def test_clean_feet_still_read():
    assert _h(("5 قدم و7 انش",)) == D("170.18")
    assert _h(("6 قدم",)) == D("182.88")


def test_meter_shorthand_value_gate_boundaries():
    # R8: the m/م gate IS the value gate - pin its boundaries directly (M11
    # counts the shorthand without this check, so this test is its teeth).
    gate = size_extract._is_meter_shorthand_value
    assert gate(D("1.75"))
    assert gate(D("1.25"))
    assert gate(D("2.49"))
    assert gate(D("2.50"))  # 2.5 m is a non-integer meter reading
    assert not gate(D("0.99"))
    assert not gate(D("2.51"))
    assert not gate(D("1"))
    assert not gate(D("2"))
    assert not gate(D("1.00"))
    # through the extractor: outside the window the shorthand is not a unit
    assert _h(("2.6 M",)) is None
    assert _h(("3 M",)) is None
    assert _h(("0.5 م",)) is None
    assert _h(("1.25 م",)) == D("125")
    assert _h(("2.49 م",)) == D("249")


def test_pounds():
    assert _w(("وزني 150 رطل",)) == D("150") * D("0.45359237")


def test_arabic_indic_digits():
    assert _h(("طولي ١٧٥ سم",)) == D("175")
    assert _w(("وزني ٩٥ كيلو",)) == D("95")


def test_field_word_binds_left_only_so_waw_pairs_read():
    # the common phrasing: both numbers must survive the shared waw prefix
    assert _w(("وزني 95 وطولي 175",)) == D("95")
    assert _h(("وزني 95 وطولي 175",)) == D("175")


def test_number_without_keyword_is_dropped():
    assert _h(("عندي 175",)) is None
    assert _w(("رقم 95",)) is None
    assert _h(("175",)) is None


def test_contradiction_drops_the_field():
    assert _h(("طولي 175 وطولي 170",)) is None
    assert _h(("طولي 175 سم", "طولي 170 سم")) is None


def test_same_value_twice_stays_one_value():
    assert _h(("طولي 175 سم", "وطولي 175")) == D("175")


def test_two_conflicting_units_drop_the_number():
    assert _w(("كيلو 95 رطل",)) is None


def test_unrelated_talk_is_none():
    assert extract_size_inputs(("مرحبا كيف حالك", "طلبي وصل؟")).height_cm is None
    assert extract_size_inputs(("مرحبا كيف حالك",)).weight_kg is None


def test_plausibility_is_not_checked_here():
    # 5 cm and 400 kg pass straight through - the bounds live in advise.
    assert _h(("طولي 5 سم",)) == D("5")
    assert _w(("وزني 400 كيلو",)) == D("400")
