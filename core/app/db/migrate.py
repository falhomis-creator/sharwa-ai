"""core/app/db/migrate.py - forward-only SQL migration runner (H14).

Takes pg_advisory_lock, records schema_migrations(version, checksum,
applied_at), and refuses to proceed if an ALREADY-APPLIED migration file's
checksum no longer matches what was recorded (someone edited history after
the fact - forbidden, H14: "لا يُعدَّل المرجع"). Running twice is always safe
(idempotent): already-applied versions are skipped, not re-run.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import psycopg

ADVISORY_LOCK_KEY = 0x5348_4152_5741  # arbitrary fixed constant ("SHARWA" in hex, truncated to fit bigint)

_MIGRATIONS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     text PRIMARY KEY,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    """Raised when a migration is discovered to be tampered with (checksum
    mismatch against what schema_migrations recorded), or when
    adopt_existing_schema_version names a file that doesn't exist."""


@dataclass(frozen=True)
class MigrationFile:
    version: str
    path: Path
    checksum: str
    sql: str


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def discover_migrations(migrations_dir: Path) -> list[MigrationFile]:
    files = sorted(migrations_dir.glob("*.sql"))
    out = []
    for f in files:
        sql = f.read_text()
        out.append(MigrationFile(version=f.stem, path=f, checksum=_sha256(sql), sql=sql))
    return out


def run_migrations(
    dsn: str, migrations_dir: Path, *, adopt_existing_schema_version: str | None = None,
) -> list[str]:
    """Returns the list of versions actually applied this run (empty if
    everything was already up to date).

    adopt_existing_schema_version: a ONE-TIME, explicit adoption path for a
    database whose schema was already applied by hand before this migration
    runner was ever used on it (exactly core/'s own situation: 0001_baseline
    is a byte-identical copy of the schema.sql the real database already has
    - see docs/P0_FINDINGS.md). When schema_migrations is empty and this is
    set to a real migration version's name, that version is recorded with
    its real checksum WITHOUT running its SQL (which would collide with the
    tables it already created), and every migration after it in sequence
    runs normally. Never used implicitly - the operator names the exact
    version they are vouching for, out loud, every time."""
    applied_now: list[str] = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        try:
            conn.execute(_MIGRATIONS_TABLE_DDL)
            recorded = {
                row[0]: row[1]
                for row in conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
            }
            migrations = discover_migrations(migrations_dir)

            if adopt_existing_schema_version is not None and not recorded:
                target = next((m for m in migrations if m.version == adopt_existing_schema_version), None)
                if target is None:
                    raise MigrationError(
                        f"adopt_existing_schema_version={adopt_existing_schema_version!r} "
                        "does not match any discovered migration file"
                    )
                conn.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                    (target.version, target.checksum),
                )
                recorded[target.version] = target.checksum

            for m in migrations:
                if m.version in recorded:
                    if recorded[m.version] != m.checksum:
                        raise MigrationError(
                            f"checksum mismatch for already-applied migration {m.version}: "
                            "the file was edited after being applied (H14 forbids this)"
                        )
                    continue
                with conn.transaction():
                    conn.execute(m.sql)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                        (m.version, m.checksum),
                    )
                applied_now.append(m.version)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
    return applied_now
