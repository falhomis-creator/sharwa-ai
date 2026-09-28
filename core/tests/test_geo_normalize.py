"""Pure geo-normalization tests (P2.2 §9)."""
from __future__ import annotations

from app.geo import normalize


def test_normalize_reuses_arabic_diacritics():
    assert normalize.normalize_name("صَنْعاء") == normalize.normalize_name("صنعاء")


def test_normalize_unifies_alef_and_ta_marbuta():
    assert normalize.normalize_name("أمانة") == normalize.normalize_name("امانه")


def test_normalize_arabic_indic_digits():
    assert normalize.normalize_name("الحي ٧") == normalize.normalize_name("الحي 7")


def test_synonyms_dialect_terms():
    synonyms = {"جوله": "دوار", "تقاطع": "مفرق"}
    assert normalize.apply_synonyms("جوله", synonyms) == "دوار"
    assert normalize.apply_synonyms("تقاطع", synonyms) == "مفرق"
    assert normalize.apply_synonyms("دوار", synonyms) == "دوار"  # no synonym


def test_synonyms_roundabout_variants():
    synonyms = {"دوّار": "دوار", "دوار": "دوار"}
    assert normalize.apply_synonyms("دوّار", synonyms) == "دوار"


def test_purity_ten_times():
    expected = normalize.normalize_name("مدينة صنعاء")
    for _ in range(10):
        assert normalize.normalize_name("مدينة صنعاء") == expected
