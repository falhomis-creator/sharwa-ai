#!/usr/bin/env python3
"""scripts/seed_gazetteer.py - operator tool: load the Yemen gazetteer.

The ONLY sanctioned writer of SHARED (tenant_id IS NULL) geo_gazetteer rows
(H68 / S17-d). Any tenant-learned landmark written from a conversation path goes
through the app with a set tenant_id - never here, never NULL from a runtime
path. Reads a committed JSON-lines data file (one row per object) and upserts.

Data source for Yemen is an OPEN item (OQ-P2-04) - the owner approves the source
before any load; this script is generic over the file format.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg

# level CHECK mirrors geo_gazetteer.level in 0001_baseline.sql.
LEVELS = ("country", "governorate", "district", "area", "neighborhood", "landmark")


def load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        obj = json.loads(line)
        if obj.get("level") not in LEVELS:
            raise ValueError(f"bad level {obj.get('level')!r} for {obj.get('name_ar')!r}")
        rows.append(obj)
    return rows


def upsert(dsn: str, rows: list[dict]) -> int:
    written = 0
    with psycopg.connect(dsn, autocommit=True) as conn:
        for obj in rows:
            conn.execute(
                "INSERT INTO geo_gazetteer (tenant_id, level, name_ar, name_norm, parent_id, geom, confidence) "
                "VALUES (NULL, %s, %s, %s, %s, ST_GeomFromText(%s, 4326), %s) "
                "ON CONFLICT (id) DO NOTHING",
                (
                    obj["level"], obj["name_ar"], obj["name_norm"],
                    obj.get("parent_id"), obj["geom"],
                    obj.get("confidence", 1.0),
                ),
            )
            written += 1
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seed_gazetteer.py")
    parser.add_argument("data_file", help="JSON-lines gazetteer file")
    args = parser.parse_args(argv)

    dsn = os.environ.get("CORE_MIGRATION_DATABASE_URL")
    if not dsn:
        print("CORE_MIGRATION_DATABASE_URL is not set", file=sys.stderr)
        return 2

    data_file = Path(args.data_file).resolve()
    if not data_file.is_file():
        print(f"data file not found: {data_file}", file=sys.stderr)
        return 2

    rows = load_rows(data_file)
    written = upsert(dsn, rows)
    print(f"seed_gazetteer: upserted {written} shared rows (tenant_id IS NULL)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
