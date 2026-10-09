"""Store-scoped gift link in the REAL turn, on PostgreSQL (owner decision 2026-10-09).

The link names the store (platform_ref). A store whose platform_ref is not a DNS
label cannot get a working link, so no basket is stored and the customer is
handed to a human - checked before any write.
"""
from __future__ import annotations

import psycopg
import pytest

from tests.test_gift_turn_db import TENANT_A, _carts, _turn, ctx  # noqa: F401 (fixture)

pytestmark = pytest.mark.db


def _set_ref(dsn: str, ref: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("UPDATE tenants SET platform_ref = %s WHERE id = %s", (ref, TENANT_A))


def test_the_link_carries_the_store(ctx):  # noqa: F811
    dsn, conv = ctx
    _set_ref(dsn, "store2")
    outcome, payload = _turn(dsn, conv, "ابي هدية بحدود 20 الف", enabled=True)
    assert outcome == "gift_baskets"
    assert "https://store2.sharwaah.com/checkout/gift/" in payload["text"]
    assert "{tenant_ref}" not in payload["text"]


def test_a_store_without_a_valid_label_hands_off_and_stores_nothing(ctx):  # noqa: F811
    dsn, conv = ctx
    _set_ref(dsn, "tenant_a")
    outcome, payload = _turn(dsn, conv, "ابي هدية بحدود 20 الف", enabled=True)
    assert (outcome, payload["template"]) == ("gift_no_basket", "gift_no_basket")
    assert "checkout/gift" not in payload["text"]
    assert _carts(dsn) == {}
