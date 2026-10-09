"""P4 Task 14: the gift request reader, the reply composition, the currency
rule and the settings - pure tests."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.tools.gift_extract import extract_gift_request
from app.workers import compose, gift
from app.workers.config import ConfigError, WorkerSettings
from tests.test_workers_config import _base_env


@pytest.mark.parametrize(("text", "budget", "currency"), [
    ("ابي هدية بحدود 20 الف", Decimal("20000"), None),
    ("هدية لامي ب 50,000 ريال يمني", Decimal("50000"), "YER"),
    ("هديه بـ ٣٠٠٠٠", Decimal("30000"), None),
    ("هدية ب 300 ريال سعودي", Decimal("300"), "SAR"),
    ("هدية بـ 20000 ريال", Decimal("20000"), "RIYAL"),
    ("gift for my wife under 100$", Decimal("100"), "USD"),
    ("هدية لبنتي عمرها 10 سنوات بـ 20 الف", Decimal("20000"), None),   # the age is not a budget
])
def test_budget_and_currency_are_read(text, budget, currency):
    req = extract_gift_request((text,))
    assert req.is_gift and (req.budget_major, req.currency_hint) == (budget, currency)


@pytest.mark.parametrize("text", [
    "ابي هديه", "هدية لبنتي عمرها 10 سنوات", "عندي 2 بنات ابي هدية ب 5000 و 7000",
])
def test_missing_or_ambiguous_budget_is_none(text):
    req = extract_gift_request((text,))
    assert req.is_gift and req.budget_major is None


@pytest.mark.parametrize("text", ["كم سعر القميص", "ابي عطر ب 5000", "مرحبا", "هديل"])
def test_non_gift_messages(text):
    assert not extract_gift_request((text,)).is_gift


def test_query_keeps_only_the_interest_words():
    assert extract_gift_request(("هدية لامي تحب العطور ب 50,000 ريال",)).query == "لامي تحب العطور"


def test_reply_lists_titles_and_links_without_any_number_of_ours():
    text = compose.compose_gift_baskets(
        [("c-1", ("عطر عود", "شال صوف")), ("c-2", ("ساعة يد",)), ("c-3", ("كوب",)), ("c-4", ("زائد",))],
        "https://sharwaah.com/checkout/gift/",
    )
    assert "1) عطر عود + شال صوف\nhttps://sharwaah.com/checkout/gift/c-1" in text
    assert "https://sharwaah.com/checkout/gift/c-3" in text
    assert "c-4" not in text and "زائد" not in text                      # at most 3 baskets


@pytest.mark.parametrize(("present", "hint", "expected"), [
    ({"YER"}, None, "YER"),
    ({"YER", "USD"}, None, None),          # mixed store, no currency named => hand off
    ({"YER"}, "USD", None),                # never converted
    ({"YER", "USD"}, "USD", "USD"),
    ({"YER"}, "RIYAL", "YER"),
    ({"SAR", "YER"}, "RIYAL", None),       # a bare riyal is ambiguous here
])
def test_currency_rule(present, hint, expected):
    assert gift._pick_currency(present, hint) == expected


def test_gift_is_off_by_default_and_the_link_base_is_validated(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.delenv("GIFT_ENABLED", raising=False)
    monkeypatch.delenv("GIFT_CHECKOUT_BASE_URL", raising=False)
    s = WorkerSettings.load()
    assert (s.gift_enabled, s.gift_checkout_base_url) == (
        False, "https://{tenant_ref}.sharwaah.com/checkout/gift/",  # store-scoped (2026-10-09)
    )
    for bad in ("http://sharwaah.com/checkout/gift/", "https://sharwaah.com/checkout/gift"):
        monkeypatch.setenv("GIFT_CHECKOUT_BASE_URL", bad)
        with pytest.raises(ConfigError, match="GIFT_CHECKOUT_BASE_URL"):
            WorkerSettings.load()
