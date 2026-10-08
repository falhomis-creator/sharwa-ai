"""F-P4-10: with SIZE_ADVICE_ENABLED on, a product search that merely MENTIONS a
size must still reach the router (product_list), while a genuine size question -
one that carries a plausible body measurement - takes the size path. Real turns
on PostgreSQL (process_turn + FakeLlmProvider + the real Verifier rules)."""
from __future__ import annotations

import pytest

from app import db as core_db
from app.db import repos_summary
from app.db import testsupport as db_testsupport
from tests.test_router_turn_db import TENANT_A, _routed_turn, ctx  # noqa: F401  (fixture)

pytestmark = pytest.mark.db

_SML = [
    {"size_label": "S", "sort_order": 1, "height_cm": "[150,165]", "weight_kg": "[45,60]"},
    {"size_label": "M", "sort_order": 2, "height_cm": "[165,175]", "weight_kg": "[60,75]"},
    {"size_label": "L", "sort_order": 3, "height_cm": "[175,190]", "weight_kg": "[75,95]"},
]


def _seed_shirt(dsn: str) -> None:
    db_testsupport.seed_catalog_product(
        dsn, tenant_id=TENANT_A, platform_product_id="P-SHIRT", category=None, title="منتج قميص قطني",
    )


@pytest.mark.parametrize("body", ["منتج قميص مقاس L", "ابي منتج قميص مقاسي 42"])
def test_product_search_mentioning_a_size_still_shows_products(ctx, body):  # noqa: F811
    dsn, conv = ctx
    _seed_shirt(dsn)
    outcome, payload, calls = _routed_turn(dsn, conv, body, size_advice_enabled=True)
    assert len(calls) == 1                         # the router was consulted
    assert (outcome, payload["template"]) == ("product_list", "product_list")


def test_size_word_without_a_measurement_goes_to_the_router(ctx):  # noqa: F811
    dsn, conv = ctx
    _seed_shirt(dsn)
    outcome, _payload, calls = _routed_turn(dsn, conv, "عندكم القميص مقاس L؟", size_advice_enabled=True)
    assert len(calls) == 1
    assert outcome != "size_no_product"


def test_a_genuine_size_question_takes_the_size_path(ctx):  # noqa: F811
    dsn, conv = ctx
    db_testsupport.seed_size_chart(
        dsn, tenant_id=TENANT_A, scope_type="product", scope_ref="P-1", rows=_SML,
    )
    with core_db.tenant_tx(TENANT_A) as conn:
        repos_summary.record_shown_products(
            conn, tenant_id=TENANT_A, conversation_id=conv, platform_product_ids=["P-1"],
        )
    outcome, payload, calls = _routed_turn(
        dsn, conv, "طولي 170 ووزني 65 ايش مقاسي", size_advice_enabled=True,
    )
    assert calls == []                             # deterministic: no model
    assert (outcome, payload["text"]) == ("size_recommend", "المقاس المناسب لك: مقاس M ✅")


def test_flag_off_is_untouched(ctx):  # noqa: F811
    dsn, conv = ctx
    _seed_shirt(dsn)
    outcome, _payload, calls = _routed_turn(
        dsn, conv, "طولي 170 ووزني 65 منتج قميص", size_advice_enabled=False,
    )
    assert len(calls) == 1
    assert outcome == "product_list"

