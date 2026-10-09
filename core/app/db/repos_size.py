"""app/db/repos_size.py - read a tenant's size chart for SizeAdvisor (P4 Task 10).

Read-only. Runs inside tenant_tx(): RLS (tenant_isolation on size_charts,
size_chart_rows and catalog_products) is the ONLY tenant filter - no tenant_id
parameter is passed, so a chart is invisible to every other tenant by the
schema itself, not by a WHERE clause this module could forget.

Lookup: the product's own chart (scope_type='product', scope_ref =
platform_product_id) wins; otherwise the chart of the product's category
(scope_type='category', scope_ref = catalog_products.category). No chart, or a
chart with zero rows => None (H75: a chart without data is no chart - the
advisor answers `no_chart` and the customer is asked).

Ranges: the advisor takes closed [min, max] Decimal ranges. A stored numrange
that is empty, unbounded on either side, or not inclusive on BOTH bounds is a
data defect: SizeChartDataError is raised (fail closed, H47) - it is never
widened, narrowed or dropped (dropping a dimension would silently skip a check,
H49). A NULL column means "this dimension is not in the chart" (None).

allow_under_weight is always False: the schema has no merchant opt-out column
for the weight guard (OQ-P4-18).
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import psycopg

from app.fit.size_advisor import Range, SizeChart, SizeRow

_RANGE_COLUMNS = ("height_cm", "weight_kg", "chest_cm", "waist_cm", "hips_cm")


class SizeChartDataError(ValueError):
    """A stored chart cannot be read as closed ranges - fail closed (H47)."""


def _closed_range(value: Any, *, chart_id: uuid.UUID, column: str) -> Range | None:
    if value is None:
        return None
    if (
        value.isempty
        or value.lower is None
        or value.upper is None
        or not value.lower_inc
        or not value.upper_inc
    ):
        raise SizeChartDataError(f"size chart {chart_id}: {column} is not a closed [min, max] range")
    return (Decimal(value.lower), Decimal(value.upper))


def read_size_chart(conn: psycopg.Connection, *, platform_product_id: str) -> SizeChart | None:
    """The chart for one product under the CURRENT tenant (RLS), or None."""
    head = conn.execute(
        "SELECT c.id, c.fit_type, c.stretch_pct "
        "FROM size_charts c "
        "WHERE (c.scope_type = 'product' AND c.scope_ref = %s) "
        "   OR (c.scope_type = 'category' AND c.scope_ref = ("
        "         SELECT p.category FROM catalog_products p WHERE p.platform_product_id = %s)) "
        "ORDER BY CASE c.scope_type WHEN 'product' THEN 0 ELSE 1 END "
        "LIMIT 1",
        (platform_product_id, platform_product_id),
    ).fetchone()
    if head is None:
        return None
    chart_id, fit_type, stretch_pct = head
    rows = conn.execute(
        "SELECT size_label, sort_order, height_cm, weight_kg, chest_cm, waist_cm, hips_cm "
        "FROM size_chart_rows WHERE chart_id = %s "
        "ORDER BY sort_order, size_label",
        (chart_id,),
    ).fetchall()
    if not rows:
        return None
    size_rows: list[SizeRow] = []
    for r in rows:
        ranges = [
            _closed_range(v, chart_id=chart_id, column=col)
            for col, v in zip(_RANGE_COLUMNS, r[2:7], strict=True)
        ]
        size_rows.append(SizeRow(
            label=str(r[0]), sort_order=int(r[1]),
            height_cm=ranges[0], weight_kg=ranges[1],
            chest_cm=ranges[2], waist_cm=ranges[3], hips_cm=ranges[4],
        ))
    return SizeChart(
        rows=tuple(size_rows),
        fit_type=str(fit_type),
        stretch_pct=Decimal(stretch_pct),
        allow_under_weight=False,
    )
