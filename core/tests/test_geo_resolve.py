"""Pure address-decision tests (P2.2 §9): the decision table, by name."""
from __future__ import annotations

from app.geo.resolve import AddressCandidate, ResolveConfig, resolve_address


def _c(gid, level, name_norm, match_kind="exact", parent_id=None):
    return AddressCandidate(
        gazetteer_id=gid, level=level, name_norm=name_norm,
        parent_id=parent_id, match_kind=match_kind, tenant_scoped=False,
    )


def test_pin_accepted():
    d = resolve_address([], pin=(15.3, 44.2), pin_in_coverage=True)
    assert d.decision == "accepted"
    assert d.source == "pin"


def test_pin_outside_coverage_rejected():
    d = resolve_address([], pin=(15.3, 44.2), pin_in_coverage=False)
    assert d.decision == "rejected"
    assert d.reason_code == "pin_outside_coverage"


def test_single_exact_district_accepted():
    d = resolve_address([_c(1, "district", "التحرير")])
    assert d.decision == "accepted"
    assert d.source == "gazetteer_centroid"
    assert d.gazetteer_id == 1


def test_three_close_disambiguate():
    d = resolve_address([
        _c(1, "district", "التحرير"),
        _c(2, "district", "الستين"),
        _c(3, "district", "حي الجامعة"),
    ])
    assert d.decision == "disambiguate"
    assert d.candidates == ("التحرير", "الستين", "حي الجامعة")


def test_single_medium_confirm():
    d = resolve_address([_c(1, "district", "التحرير", match_kind="prefix")])
    assert d.decision == "confirm_with_customer"


def test_zero_ask_for_pin():
    d = resolve_address([])
    assert d.decision == "ask_for_pin"


def test_governorate_only_ask_for_pin():
    d = resolve_address([_c(1, "governorate", "صنعاء")])
    assert d.decision == "ask_for_pin"


def test_model_coords_rejected():
    d = resolve_address([_c(1, "district", "التحرير")], model_has_coords=True)
    assert d.decision == "rejected"
    assert d.reason_code == "coord_from_model"


def test_purity_ten_times():
    cs = [_c(1, "district", "التحرير")]
    first = resolve_address(cs)
    for _ in range(9):
        assert resolve_address(cs) == first


def test_threshold_boundary():
    # exact district, n=1: confidence = 0.85 * 1.0 * 1.0 = 0.85 (no parent bonus).
    c = _c(1, "district", "التحرير")
    assert resolve_address([c], cfg=ResolveConfig(accept_threshold=0.85)).decision == "accepted"
    assert resolve_address([c], cfg=ResolveConfig(accept_threshold=0.86)).decision == "confirm_with_customer"
