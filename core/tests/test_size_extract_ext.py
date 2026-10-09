"""P4 Task 18b-1 (OQ-P4-14): size_extract extensions - compound meter, Latin-comma
decimals, body measurements (claimed, never read as height/weight) and the fit
preference. Pure; the approved test_size_extract.py stays untouched."""
from __future__ import annotations

from decimal import Decimal

from app.tools.size_extract import SizeInputs, extract_size_inputs

D = Decimal


def _x(*texts: str) -> SizeInputs:
    return extract_size_inputs(texts)


def test_compound_meter_reads_one_meter_plus_centimeters():
    assert _x("1 متر و75").height_cm == D("175")
    assert _x("متر و 75 سم").height_cm == D("175")
    assert _x("طولي متر و٧٥ ووزني 80") == SizeInputs(D("175"), D("80"))
    assert _x("1 meter and 80").height_cm is None  # English meter word is not in the vocabulary


def test_compound_meter_rejects_what_is_not_a_body_height():
    assert _x("متر و150").height_cm is None          # N must be < 100
    assert _x("متر و7.5").height_cm is None          # N must be an integer
    assert _x("2 متر و10").height_cm == D("200")     # not "1 meter": plain 2 m, 10 unbound
    r = _x("1 متر و 75 كيلو")                        # a non-cm unit ends the compound
    assert (r.height_cm, r.weight_kg) == (D("100"), D("75"))


def test_latin_comma_decimal():
    assert _x("طولي 1,75 م").height_cm == D("175")
    assert _x("وزني 80,5 كيلو").weight_kg == D("80.5")
    assert _x("1,750 ريال") == SizeInputs(None, None)   # thousands, not 1.750


def test_body_measurement_is_never_read_as_height():
    r = _x("صدري 100 سم")
    assert r.height_cm is None
    assert r.body_cm == (("chest", D("100")),)


def test_body_measurements_units_and_combination():
    assert _x("صدري 40 انش").body_cm == (("chest", D("101.60")),)
    r = _x("صدري 100 خصري 85 وطولي 175 ووزني 80")
    assert (r.height_cm, r.weight_kg) == (D("175"), D("80"))
    assert r.body_cm == (("chest", D("100")), ("waist", D("85")))
    assert _x("الورك 98").body_cm == (("hips", D("98")),)


def test_body_measurement_with_a_wrong_unit_is_dropped_and_not_reused():
    r = _x("صدري 100 كيلو")
    assert r.body_cm == ()
    assert r.weight_kg is None


def test_body_measurement_contradiction_drops_that_dimension():
    assert _x("صدري 100", "صدري 104").body_cm == ()
    assert _x("صدري 100", "صدري 100").body_cm == (("chest", D("100")),)


def test_fit_preference_closed_vocabulary():
    assert _x("ابغاه ضيق").fit_pref == "fitted"
    assert _x("المقاس العادي").fit_pref == "regular"
    assert _x("أحبه واسع").fit_pref == "loose"
    assert _x("طولي 175").fit_pref is None


def test_fit_preference_negated_or_conflicting_is_unknown():
    assert _x("ما ابغاه ضيق").fit_pref is None
    assert _x("ابغاه واسع او عادي").fit_pref is None
    assert _x("ابغاه ضيق", "لا لا خليه واسع").fit_pref is None


def test_legacy_fields_keep_their_meaning():
    # the two original fields are unchanged for inputs without the new forms
    assert _x("طولي 175 ووزني 80") == SizeInputs(D("175"), D("80"))
