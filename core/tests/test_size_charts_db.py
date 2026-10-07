"""core/tests/test_size_charts_db.py - P4 Task 10: reading size charts under RLS.

Real PostgreSQL. The repo (app.db.repos_size.read_size_chart) passes no
tenant_id: every isolation claim here is the schema's RLS doing the work.
"""
from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest

from app import db as core_db
from app.db import repos_size
from app.db import testsupport as db_testsupport
from app.fit.size_advisor import advise

pytestmark = pytest.mark.db

TENANT_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = uuid.UUID("22222222-2222-2222-2222-222222222222")
D = Decimal

_SML = [
    {"size_label": "L", "sort_order": 3, "height_cm": "[175,190]", "weight_kg": "[75,95]"},
    {"size_label": "S", "sort_order": 1, "height_cm": "[150,165]", "weight_kg": "[45,60]"},
    {"size_label": "M", "sort_order": 2, "height_cm": "[165,175]", "weight_kg": "[60,75]"},
]


@pytest.fixture()
def tenants():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.delete_tenant_full(dsn, TENANT_A)
    db_testsupport.delete_tenant_full(dsn, TENANT_B)
    db_testsupport.seed_two_tenants(dsn, TENANT_A, TENANT_B)
    yield dsn
    db_testsupport.delete_tenant_full(dsn, TENANT_A)
    db_testsupport.delete_tenant_full(dsn, TENANT_B)


def _read(tenant: uuid.UUID, product: str):
    with core_db.tenant_tx(tenant) as conn:
        return repos_size.read_size_chart(conn, platform_product_id=product)


def test_product_chart_read_in_sort_order_and_feeds_the_advisor(tenants):
    db_testsupport.seed_size_chart(
        tenants, tenant_id=TENANT_A, scope_type="product", scope_ref="P-1",
        rows=_SML, fit_type="slim", stretch_pct="5.0",
    )
    chart = _read(TENANT_A, "P-1")
    assert chart is not None
    assert [r.label for r in chart.rows] == ["S", "M", "L"]
    assert [r.sort_order for r in chart.rows] == [1, 2, 3]
    assert chart.rows[1].height_cm == (D("165"), D("175"))
    assert chart.rows[1].weight_kg == (D("60"), D("75"))
    assert chart.rows[1].chest_cm is None
    assert chart.fit_type == "slim"
    assert chart.stretch_pct == D("5.0")
    assert chart.allow_under_weight is False
    advice = advise(chart, height_cm=D("170"), weight_kg=D("70"), fit_pref="regular")
    assert advice.size == "M"


def test_product_chart_beats_category_chart(tenants):
    db_testsupport.seed_catalog_product(
        tenants, tenant_id=TENANT_A, platform_product_id="P-2", category="shirts",
    )
    db_testsupport.seed_size_chart(
        tenants, tenant_id=TENANT_A, scope_type="category", scope_ref="shirts",
        rows=[{"size_label": "CAT", "sort_order": 1, "height_cm": "[150,190]"}],
    )
    db_testsupport.seed_size_chart(
        tenants, tenant_id=TENANT_A, scope_type="product", scope_ref="P-2",
        rows=[{"size_label": "PROD", "sort_order": 1, "height_cm": "[150,190]"}],
    )
    chart = _read(TENANT_A, "P-2")
    assert chart is not None
    assert [r.label for r in chart.rows] == ["PROD"]


def test_category_chart_found_through_the_catalog_product(tenants):
    db_testsupport.seed_catalog_product(
        tenants, tenant_id=TENANT_A, platform_product_id="P-3", category="shirts",
    )
    db_testsupport.seed_size_chart(
        tenants, tenant_id=TENANT_A, scope_type="category", scope_ref="shirts",
        rows=[{"size_label": "CAT", "sort_order": 1, "height_cm": "[150,190]"}],
    )
    chart = _read(TENANT_A, "P-3")
    assert chart is not None
    assert [r.label for r in chart.rows] == ["CAT"]


def test_no_chart_is_none(tenants):
    db_testsupport.seed_catalog_product(
        tenants, tenant_id=TENANT_A, platform_product_id="P-4", category=None,
    )
    db_testsupport.seed_size_chart(
        tenants, tenant_id=TENANT_A, scope_type="category", scope_ref="shirts",
        rows=[{"size_label": "CAT", "sort_order": 1, "height_cm": "[150,190]"}],
    )
    assert _read(TENANT_A, "P-4") is None          # product without category
    assert _read(TENANT_A, "UNKNOWN") is None      # product not in the catalog


def test_chart_without_rows_is_none(tenants):
    db_testsupport.seed_size_chart(
        tenants, tenant_id=TENANT_A, scope_type="product", scope_ref="P-5", rows=[],
    )
    assert _read(TENANT_A, "P-5") is None


def test_rls_isolates_charts_between_tenants(tenants):
    db_testsupport.seed_size_chart(
        tenants, tenant_id=TENANT_A, scope_type="product", scope_ref="P-6",
        rows=[{"size_label": "A-ONLY", "sort_order": 1, "height_cm": "[150,190]"}],
    )
    db_testsupport.seed_catalog_product(
        tenants, tenant_id=TENANT_A, platform_product_id="P-7", category="shirts",
    )
    db_testsupport.seed_size_chart(
        tenants, tenant_id=TENANT_B, scope_type="category", scope_ref="shirts",
        rows=[{"size_label": "B-ONLY", "sort_order": 1, "height_cm": "[150,190]"}],
    )
    a = _read(TENANT_A, "P-6")
    assert a is not None and [r.label for r in a.rows] == ["A-ONLY"]
    assert _read(TENANT_B, "P-6") is None          # A's product chart is invisible to B
    assert _read(TENANT_A, "P-7") is None          # B's category chart is invisible to A
    db_testsupport.seed_size_chart(
        tenants, tenant_id=TENANT_B, scope_type="product", scope_ref="P-6",
        rows=[{"size_label": "B-OWN", "sort_order": 1, "height_cm": "[150,190]"}],
    )
    b = _read(TENANT_B, "P-6")
    assert b is not None and [r.label for r in b.rows] == ["B-OWN"]
    a_again = _read(TENANT_A, "P-6")
    assert a_again is not None and [r.label for r in a_again.rows] == ["A-ONLY"]


@pytest.mark.parametrize("bad", ["[150,165)", "(150,165]", "[150,)", "(,165]", "empty"])
def test_non_closed_range_fails_closed(tenants, bad):
    db_testsupport.seed_size_chart(
        tenants, tenant_id=TENANT_A, scope_type="product", scope_ref="P-8",
        rows=[{"size_label": "S", "sort_order": 1, "height_cm": bad}],
    )
    with pytest.raises(repos_size.SizeChartDataError):
        _read(TENANT_A, "P-8")
