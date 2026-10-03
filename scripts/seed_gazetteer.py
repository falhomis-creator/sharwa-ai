#!/usr/bin/env python3
"""scripts/seed_gazetteer.py - operator tool: load the Yemen gazetteer (P2.4, H75).

The ONLY sanctioned writer of SHARED (tenant_id IS NULL) geo_gazetteer rows
(H68/S17-d). Loads data/geo/yemen_admin.json, verifies geometry fail-fast (a
wrong coordinate is worse than an absent one - H75), inserts idempotently
(check-before-insert on (level, name_norm, parent)), and loads geo_synonyms.

Geometric verification (§3.1.4):
  * ST_IsValid on every geom
  * the centroid of every geom inside the written Yemen bbox (GEO_BBOX_*)
  * every `parent` resolves to a row already in the batch or the DB
  * every child's centre inside its parent's shape: ST_Covers when the parent
    carries an areal geometry, ST_DWithin within GEO_MAX_PARENT_DISTANCE_M when
    the parent is a point centre (the point-equivalent of ST_Covers - precise
    governorate polygons are OQ-P2-04).
Any failure aborts the WHOLE batch (no partial load).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg

LEVELS = ("country", "governorate", "district", "area", "neighborhood", "landmark")

DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data" / "geo" / "yemen_admin.json"

GEO_BBOX = {
    "min_lat": float(os.environ.get("GEO_BBOX_MIN_LAT", "12.0")),
    "max_lat": float(os.environ.get("GEO_BBOX_MAX_LAT", "18.5")),
    "min_lng": float(os.environ.get("GEO_BBOX_MIN_LNG", "42.0")),
    "max_lng": float(os.environ.get("GEO_BBOX_MAX_LNG", "54.5")),
}
GEO_MAX_PARENT_DISTANCE_M = float(os.environ.get("GEO_MAX_PARENT_DISTANCE_M", "120000"))


def load_data(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def verify(conn: psycopg.Connection, rows: list[dict]) -> list[str]:
    """Fail-fast geometric verification (F-P2-07). Parents are referenced by the
    FILE's stable `parent_key`, never by `name_norm` (a duplicate name - e.g.
    «التحرير» in two governorates - must not let one row swallow the other).
    Returns the list of failure messages."""
    errors: list[str] = []
    by_key: dict[str, dict] = {}
    seen_tuples: set[tuple[str, str, str | None]] = set()

    for r in rows:
        label = f"{r['level']} {r['name_ar']!r}"
        key = r.get("key")
        if not key:
            errors.append(f"{label}: missing key")
            continue
        if key in by_key:
            errors.append(f"{label}: duplicate key {key!r}")
            continue
        by_key[key] = r

        tup = (r["level"], r["name_norm"], r.get("parent_key"))
        if tup in seen_tuples:
            errors.append(f"{label}: duplicate (level, name_norm, parent_key) {tup!r}")
        seen_tuples.add(tup)

        if r["level"] not in LEVELS:
            errors.append(f"{label}: level {r['level']!r} outside {LEVELS}")
            continue

        ok, cx, cy = conn.execute(
            "SELECT ST_IsValid(ST_GeomFromText(%s, 4326)), "
            "       ST_X(ST_Centroid(ST_GeomFromText(%s, 4326))), "
            "       ST_Y(ST_Centroid(ST_GeomFromText(%s, 4326)))",
            (r["geom"], r["geom"], r["geom"]),
        ).fetchone()
        if not ok:
            errors.append(f"{label}: geom is not valid (ST_IsValid)")
            continue
        if not (GEO_BBOX["min_lng"] <= cx <= GEO_BBOX["max_lng"]):
            errors.append(f"{label}: centroid lng {cx} outside Yemen bbox")
        if not (GEO_BBOX["min_lat"] <= cy <= GEO_BBOX["max_lat"]):
            errors.append(f"{label}: centroid lat {cy} outside Yemen bbox")

    # Second pass: parent resolution + child-inside-parent (parents are always in
    # the batch - the data file is self-contained).
    for r in rows:
        parent_key = r.get("parent_key")
        if parent_key is None:
            continue
        label = f"{r['level']} {r['name_ar']!r}"
        parent = by_key.get(parent_key)
        if parent is None:
            errors.append(f"{label}: parent_key {parent_key!r} does not resolve to a row")
            continue
        covered = conn.execute(
            "SELECT ST_Covers(ST_GeomFromText(%s, 4326), ST_GeomFromText(%s, 4326)) "
            "OR ST_DWithin(ST_GeomFromText(%s, 4326)::geography, ST_GeomFromText(%s, 4326)::geography, %s)",
            (parent["geom"], r["geom"], parent["geom"], r["geom"], GEO_MAX_PARENT_DISTANCE_M),
        ).fetchone()[0]
        if not covered:
            errors.append(f"{label}: centre not inside parent {parent['name_ar']!r} (ST_Covers/ST_DWithin)")

    return errors


def upsert(dsn: str, rows: list[dict], synonyms: dict) -> tuple[int, int]:
    """Idempotent: parents-first (key-depth order) with a key -> db_id map, so the
    parent FK is resolved by KEY (F-P2-07) never by a possibly-duplicate name.
    Returns (rows_written, synonyms_written)."""
    written = 0
    ordered = sorted(rows, key=lambda r: r["key"].count("/"))
    with psycopg.connect(dsn, autocommit=True) as conn:
        id_by_key: dict[str, int] = {}
        for r in ordered:
            parent_key = r.get("parent_key")
            pid = id_by_key.get(parent_key) if parent_key else None
            row = conn.execute(
                "SELECT id FROM geo_gazetteer WHERE tenant_id IS NULL "
                "AND level = %s AND name_norm = %s AND parent_id IS NOT DISTINCT FROM %s LIMIT 1",
                (r["level"], r["name_norm"], pid),
            ).fetchone()
            if row is None:
                ins = conn.execute(
                    "INSERT INTO geo_gazetteer (tenant_id, level, name_ar, name_norm, parent_id, geom) "
                    "VALUES (NULL, %s, %s, %s, %s, ST_GeomFromText(%s, 4326)) RETURNING id",
                    (r["level"], r["name_ar"], r["name_norm"], pid, r["geom"]),
                ).fetchone()
                assert ins is not None
                id_by_key[r["key"]] = int(ins[0])
                written += 1
            else:
                id_by_key[r["key"]] = int(row[0])

        syn_written = 0
        for term_norm, canonical in synonyms.items():
            exists = conn.execute("SELECT 1 FROM geo_synonyms WHERE term_norm = %s", (term_norm,)).fetchone()
            if exists is None:
                conn.execute(
                    "INSERT INTO geo_synonyms (term_norm, canonical, kind) VALUES (%s, %s, 'dialect')",
                    (term_norm, canonical),
                )
                syn_written += 1
    return written, syn_written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seed_gazetteer.py")
    parser.add_argument("data_file", nargs="?", default=str(DEFAULT_DATA), help="JSON gazetteer file")
    args = parser.parse_args(argv)

    dsn = os.environ.get("CORE_MIGRATION_DATABASE_URL")
    if not dsn:
        print("CORE_MIGRATION_DATABASE_URL is not set", file=sys.stderr)
        return 2

    data_file = Path(args.data_file).resolve()
    if not data_file.is_file():
        print(f"data file not found: {data_file}", file=sys.stderr)
        return 2

    data = load_data(data_file)
    rows: list[dict] = data["rows"]
    synonyms: dict = data.get("synonyms", {})

    with psycopg.connect(dsn) as conn:
        errors = verify(conn, rows)

    if errors:
        print(f"SEED ABORTED - {len(errors)} geometric verification failure(s):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    written, syn_written = upsert(dsn, rows, synonyms)

    by_level: dict[str, int] = {}
    for r in rows:
        by_level[r["level"]] = by_level.get(r["level"], 0) + 1
    print(f"seed_gazetteer: loaded {written} new rows (idempotent), {syn_written} new synonyms")
    print("rows per level (in batch):")
    for level in LEVELS:
        if level in by_level:
            print(f"  {level}: {by_level[level]}")
    print(f"synonyms in batch: {len(synonyms)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
