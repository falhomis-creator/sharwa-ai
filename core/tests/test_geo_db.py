"""core/tests/test_geo_db.py - the three db-marked address-geo tests from
P2.2 §9 that were never written (flagged in docs/P2_02_AUDIT.md §7).

Each runs on real PostgreSQL + PostGIS against the `address_resolutions` /
`geo_gazetteer` tables and their RLS / CHECK rules - the exact tests that would
have caught F-P2-04 (accepted-without-a-point) before it shipped.
"""
from __future__ import annotations

import os
import uuid

import pytest
from psycopg import errors as pg_errors

pytestmark = pytest.mark.db

from app import db as core_db
from app.db import repos_geo
from app.db import testsupport as db_testsupport

TENANT_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = uuid.UUID("22222222-2222-2222-2222-222222222222")

_GOVERNORATE_WKT = "POLYGON((44.0 15.2,44.4 15.2,44.4 15.6,44.0 15.6,44.0 15.2))"


@pytest.fixture()
def tenants():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.delete_tenant_full(dsn, TENANT_A)
    db_testsupport.delete_tenant_full(dsn, TENANT_B)
    db_testsupport.seed_two_tenants(dsn, TENANT_A, TENANT_B)
    yield
    db_testsupport.delete_tenant_full(dsn, TENANT_A)
    db_testsupport.delete_tenant_full(dsn, TENANT_B)


def test_point_governorate_returns_governorate_and_null(tenants):
    """§1.2 item 1: app.point_governorate returns the governorate for a point
    inside its polygon, and NULL for a point outside every known governorate."""
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.clean_geo(dsn)
    gid = db_testsupport.seed_gazetteer_governorate(
        dsn, name_ar="محافظة الاختبار", name_norm="محافظه الاختبار", wkt=_GOVERNORATE_WKT,
    )
    try:
        with core_db.tenant_tx(TENANT_A) as conn:
            inside = repos_geo.point_governorate(conn, lat=15.35, lng=44.20)
            outside = repos_geo.point_governorate(conn, lat=15.35, lng=40.00)
        assert inside is not None
        assert outside is None
    finally:
        db_testsupport.delete_gazetteer_row(dsn, gid)


def test_insert_address_resolution_accepted_without_location_rejected(tenants):
    """§1.2 item 2: insert_address_resolution(decision='accepted', location=None)
    is rejected with CheckViolation by the schema's own `accepted ⇒ location
    IS NOT NULL` CHECK - the exact test that would have caught F-P2-04."""
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.clean_geo(dsn)
    with core_db.tenant_tx(TENANT_A) as conn:
        with pytest.raises(pg_errors.CheckViolation):
            with conn.transaction():  # SAVEPOINT, so the outer tx stays usable
                repos_geo.insert_address_resolution(
                    conn,
                    tenant_id=TENANT_A,
                    conversation_id=None,
                    input={"query": "حي التحرير"},
                    candidates=[],
                    decision="accepted",
                    confidence=1.0,
                    source="gazetteer_centroid",
                    location=None,
                )


def test_gazetteer_read_exposes_shared_and_own_hides_other(tenants):
    """§1.2 item 3: the gazetteer_read RLS policy exposes shared (NULL) rows and
    the current tenant's rows, and hides another tenant's rows. Name-targeted (not
    a total count) so a loaded real gazetteer cannot skew the assertion."""
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.clean_geo(dsn)
    shared_gid = db_testsupport.seed_gazetteer_governorate(
        dsn, name_ar="محافظة مشتركة", name_norm="محافظه مشتركه", wkt=_GOVERNORATE_WKT,
    )
    own_gid = db_testsupport.seed_tenant_gazetteer(
        dsn, tenant_id=TENANT_A, level="landmark", name_ar="مطعمي", name_norm="مطعمي",
        wkt="POINT(44.2 15.3)",
    )
    other_gid = db_testsupport.seed_tenant_gazetteer(
        dsn, tenant_id=TENANT_B, level="landmark", name_ar="مطعم الآخر", name_norm="مطعم الاخر",
        wkt="POINT(44.3 15.4)",
    )
    try:
        with core_db.tenant_tx(TENANT_A) as conn:
            shared_rows = repos_geo.search_gazetteer(conn, tenant_id=TENANT_A, name_norm="محافظه مشتركه", limit=5)
            own_rows = repos_geo.search_gazetteer(conn, tenant_id=TENANT_A, name_norm="مطعمي", limit=5)
            other_rows = repos_geo.search_gazetteer(conn, tenant_id=TENANT_A, name_norm="مطعم الاخر", limit=5)
        assert any(r["gazetteer_id"] == shared_gid for r in shared_rows)   # shared visible
        assert any(r["gazetteer_id"] == own_gid for r in own_rows)         # own visible
        assert all(r["gazetteer_id"] != other_gid for r in other_rows)     # other hidden
    finally:
        db_testsupport.delete_gazetteer_row(dsn, shared_gid)
        db_testsupport.delete_gazetteer_row(dsn, own_gid)
        db_testsupport.delete_gazetteer_row(dsn, other_gid)
