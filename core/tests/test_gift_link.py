"""Store-scoped gift checkout link (owner decision 2026-10-09), pure.

GIFT_CHECKOUT_BASE_URL may carry the store as one `{tenant_ref}` placeholder:
https://{tenant_ref}.sharwaah.com/checkout/gift/ => https://<store>.sharwaah.com/...
The store label is the tenant's platform_ref; one that is not a DNS label gives
no link (the turn hands off instead of sending a link nobody can open).
"""
from __future__ import annotations

import pytest

from app.workers import gift
from app.workers.config import ConfigError, WorkerSettings, validate_gift_checkout_base_url
from tests.test_workers_config import _base_env

TEMPLATE = "https://{tenant_ref}.sharwaah.com/checkout/gift/"


@pytest.mark.parametrize("ref", ["store2", "tenant-a", "a", "x" * 63, "9shop"])
def test_placeholder_takes_the_store_label(ref):
    assert gift.checkout_base_url(TEMPLATE, ref) == f"https://{ref}.sharwaah.com/checkout/gift/"


@pytest.mark.parametrize("ref", [None, "", "tenant_a", "Store2", "-shop", "shop-", "x" * 64,
                                 "a.b", "evil.com/x", "a b", "متجر"])
def test_a_ref_that_is_not_a_dns_label_gives_no_link(ref):
    assert gift.checkout_base_url(TEMPLATE, ref) is None


def test_a_fixed_base_is_used_as_is():
    fixed = "https://sharwaah.com/checkout/gift/"
    assert gift.checkout_base_url(fixed, None) == fixed
    assert gift.checkout_base_url(fixed, "tenant_a") == fixed


@pytest.mark.parametrize("value", [TEMPLATE, "https://sharwaah.com/checkout/gift/",
                                   "https://shop.example.com/{tenant_ref}/gift/"])
def test_valid_bases(value):
    validate_gift_checkout_base_url(value)


@pytest.mark.parametrize("value", [
    "https://{tenant}.sharwaah.com/checkout/gift/",               # wrong placeholder name
    "https://{tenant_ref}.{tenant_ref}.sharwaah.com/checkout/gift/",  # twice
    "https://{tenant_ref.sharwaah.com/checkout/gift/",            # unbalanced
    "https://tenant_ref}.sharwaah.com/checkout/gift/",
    "http://{tenant_ref}.sharwaah.com/checkout/gift/",
    "https://{tenant_ref}.sharwaah.com/checkout/gift",
])
def test_invalid_bases(value):
    with pytest.raises(ConfigError, match="GIFT_CHECKOUT_BASE_URL"):
        validate_gift_checkout_base_url(value)


def test_settings_reject_a_mistyped_placeholder(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("GIFT_CHECKOUT_BASE_URL", "https://{store}.sharwaah.com/checkout/gift/")
    with pytest.raises(ConfigError, match="GIFT_CHECKOUT_BASE_URL"):
        WorkerSettings.load()
    monkeypatch.setenv("GIFT_CHECKOUT_BASE_URL", "")
    assert WorkerSettings.load().gift_checkout_base_url == TEMPLATE
