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
    db_testsupport.reset_tenants_and_channels(dsn)
    db_testsupport.seed_two_tenants(dsn, TENANT_A, TENANT_B)
    yield
    db_testsupport.reset_tenants_and_channels(dsn)


def test_point_governorate_returns_governorate_and_null(tenants):
    """§1.2 item 1: app.point_governorate returns the governorate for a point
    inside its polygon, and NULL for a point outside every known governorate."""
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.clean_geo(dsn)
    db_testsupport.seed_gazetteer_governorate(
        dsn, name_ar="أمانة العاصمة", name_norm="امانه العاصمه", wkt=_GOVERNORATE_WKT,
    )
    with core_db.tenant_tx(TENANT_A) as conn:
        inside = repos_geo.point_governorate(conn, lat=15.35, lng=44.20)
        outside = repos_geo.point_governorate(conn, lat=15.35, lng=40.00)
    assert inside is not None
    assert outside is None


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
    the current tenant's rows, and hides another tenant's rows."""
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.clean_geo(dsn)
    db_testsupport.seed_gazetteer_governorate(
        dsn, name_ar="أمانة العاصمة", name_norm="امانه العاصمه", wkt=_GOVERNORATE_WKT,
    )
    db_testsupport.seed_tenant_gazetteer(
        dsn, tenant_id=TENANT_A, level="landmark", name_ar="مطعمي", name_norm="مطعمي",
        wkt="POINT(44.2 15.3)",
    )
    db_testsupport.seed_tenant_gazetteer(
        dsn, tenant_id=TENANT_B, level="landmark", name_ar="مطعم الآخر", name_norm="مطعم الاخر",
        wkt="POINT(44.3 15.4)",
    )
    with core_db.tenant_tx(TENANT_A) as conn:
        visible = db_testsupport.count_gazetteer_rows_on_conn(conn)
    # shared governorate + tenant A's own landmark = 2; tenant B's row is hidden.
    assert visible == 2
