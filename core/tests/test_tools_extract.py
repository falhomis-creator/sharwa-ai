"""Pure deterministic extraction tests (P1.7 §10, H51). No DB/network."""
from __future__ import annotations

import re

from app.tools import extract

_REF = re.compile(r"\b\d{4,8}\b")


def test_order_ref_simple():
    assert extract.extract_order_ref(("طلب رقم 12345",), _REF) == "12345"


def test_order_ref_inside_sentence():
    assert extract.extract_order_ref(("أين طلبي رقم 67890 من فضلك",), _REF) == "67890"


def test_order_ref_first_of_two():
    assert extract.extract_order_ref(("طلب 12345 ثم 67890",), _REF) == "12345"


def test_order_ref_none():
    assert extract.extract_order_ref(("أين طلبي",), _REF) is None


def test_phone_arabic_indic():
    assert extract.extract_phone_candidates(("٠١٢٣٤٥٦٧٨٩",)) == ("0123456789",)


def test_phone_with_separators():
    assert extract.extract_phone_candidates(
        ("هاتفي 9671234567، وأيضا 9677654321",), max_candidates=3,
    ) == ("9671234567", "9677654321")


def test_phone_international_prefix_to_e164():
    assert extract.to_e164("9671234567", "967") == "+9671234567"


def test_phone_local_zero_to_e164():
    assert extract.to_e164("0123456789", "967") == "+967123456789"


def test_phone_local_zero_same_e164():
    assert extract.to_e164("0123456789", "967") == extract.to_e164("967123456789", "967")


def test_phone_short_ignored():
    assert extract.extract_phone_candidates(("12345",)) == ()


def test_phone_capped():
    assert extract.extract_phone_candidates(
        ("1111111 2222222 3333333 4444444",), max_candidates=3,
    ) == ("1111111", "2222222", "3333333")


def test_purity_same_output_ten_times():
    texts = ("طلب 12345 وهاتفي 9671234567",)
    ref = extract.extract_order_ref(texts, _REF)
    phones = extract.extract_phone_candidates(texts)
    for _ in range(10):
        assert extract.extract_order_ref(texts, _REF) == ref
        assert extract.extract_phone_candidates(texts) == phones
