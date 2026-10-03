"""app/db/repos_geo.py - the address resolver's raw SQL (P2.2, H64/H68).

Same discipline as repos_catalog / repos_outbox: every SQL string for the geo
layer lives here and ONLY here. Reads (gazetteer search, point_governorate) and
the one write (address_resolutions). Coordinates are touched HERE and in
app/geo/** only (S17-a); the model never produces them (H64).
"""
from __future__ import annotations

import uuid
from typing import Any

import psycopg
from psycopg.types.json import Jsonb


def search_gazetteer(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, name_norm: str, limit: int,
) -> list[dict[str, Any]]:
    """Exact then prefix lookup over shared (tenant_id IS NULL) + tenant-scoped
    rows; the gazetteer_read RLS policy exposes exactly those two sets. Returns
    rows with a `tenant_scoped` flag the decision layer never uses for ranking,
    plus the shape centroid as a real (lat, lng) point (F-P2-04: a gazetteer
    match must be able to carry a validated point, never `location=None`).

    N1 (P2.2 audit): the `ORDER BY sort_key, id` makes the candidate list
    deterministic - exact matches before prefix matches, then by id - so two
    equal-confidence candidates yield the same `disambiguate` list every run."""
    rows = conn.execute(
        "SELECT id, level, name_norm, parent_id, tenant_scoped, lat, lng "
        "FROM ("
        "  SELECT id, level, name_norm, parent_id, (tenant_id IS NOT NULL) AS tenant_scoped, "
        "         ST_Y(ST_Centroid(geom)) AS lat, ST_X(ST_Centroid(geom)) AS lng, 0 AS sort_key "
        "  FROM geo_gazetteer WHERE name_norm = %s "
        "  UNION ALL "
        "  SELECT id, level, name_norm, parent_id, (tenant_id IS NOT NULL), "
        "         ST_Y(ST_Centroid(geom)), ST_X(ST_Centroid(geom)), 1 "
        "  FROM geo_gazetteer WHERE name_norm LIKE %s AND name_norm <> %s"
        ") q "
        "ORDER BY q.sort_key, q.id "
        "LIMIT %s",
        (name_norm, name_norm + "%", name_norm, limit),
    ).fetchall()
    return [
        {
            "gazetteer_id": int(r[0]), "level": r[1], "name_norm": r[2],
            "parent_id": r[3], "tenant_scoped": bool(r[4]),
            "lat": float(r[5]), "lng": float(r[6]),
        }
        for r in rows
    ]


def point_governorate(conn: psycopg.Connection, *, lat: float, lng: float) -> int | None:
    """The governorate id whose geometry covers (lat, lng); None outside any
    governorate (pin_outside_coverage)."""
    row = conn.execute("SELECT app.point_governorate(%s, %s)", (lat, lng)).fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def insert_address_resolution(
    conn: psycopg.Connection,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    input: dict[str, Any],
    candidates: list[dict[str, Any]],
    decision: str,
    confidence: float,
    source: str | None,
    location: tuple[float, float] | None,
    structured: dict[str, Any] | None = None,
) -> uuid.UUID:
    """The one write to address_resolutions. `location` is (lat, lng) or None;
    the SQL CHECK (decision <> 'accepted' OR location IS NOT NULL) is the
    constitution written in the schema - an accepted address without a verified
    point is rejected by the database itself (H64)."""
    geom = None
    if location is not None:
        lat, lng = location
        # F-P2-05: plain OGC WKT (no SRID= prefix) - ST_GeomFromText expects OGC
        # WKT and would otherwise emit `WARNING: OGC WKT expected` for the EWKT
        # `SRID=4326;POINT(...)` form. The SRID is supplied explicitly (4326).
        geom = f"POINT({lng} {lat})"
    row = conn.execute(
        "INSERT INTO address_resolutions "
        "(tenant_id, conversation_id, input, candidates, decision, confidence, source, location, structured) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, "
        "CASE WHEN %s::text IS NULL THEN NULL ELSE ST_GeomFromText(%s, 4326) END, %s) "
        "RETURNING id",
        (tenant_id, conversation_id, Jsonb(input), Jsonb(candidates), decision,
         confidence, source, geom, geom, Jsonb(structured) if structured is not None else None),
    ).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING id produced no row")
    return row[0]


def fetch_synonyms(conn: psycopg.Connection) -> dict[str, str]:
    """The geo_synonyms map (term_norm -> canonical) for the dialect layer
    (جوله -> دوار، تقاطع -> مفرق ...). Read once per resolution."""
    rows = conn.execute("SELECT term_norm, canonical FROM geo_synonyms").fetchall()
    return {r[0]: r[1] for r in rows}

