"""Real test of app.db.migrate.run_migrations() against a FRESH, throwaway
Postgres database (sharwa_ai_p07_migtest) - never the already-migrated
sharwa_ai_p07 - because this test needs to observe the runner's own
idempotency and tamper-detection starting from a genuinely clean slate
(H7: real infrastructure only, no mocks)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import psycopg
import pytest

from app import cli
from app.db.migrate import MigrationError, run_migrations

_MIGTEST_DB = "sharwa_ai_p07_migtest"
_MIGTEST_DSN = f"postgresql://p07_migration:p07_migration_pw@127.0.0.1:5432/{_MIGTEST_DB}"
_REAL_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _psql_superuser(*args: str) -> None:
    subprocess.run(["sudo", "-u", "postgres", "psql", *args], check=True, capture_output=True)


@pytest.fixture()
def clean_migtest_db():
    """A REAL full DROP DATABASE / CREATE DATABASE cycle (via the postgres
    superuser role, exactly as ops originally provisioned this throwaway DB)
    so each test starts from a genuinely clean slate - extensions included,
    since p07_migration itself has no privilege to create postgis/vector
    (superuser-only extensions), only to use them once present."""
    _psql_superuser("-c", f"DROP DATABASE IF EXISTS {_MIGTEST_DB}")
    _psql_superuser("-c", f"CREATE DATABASE {_MIGTEST_DB} OWNER p07_migration")
    _psql_superuser(
        "-d", _MIGTEST_DB,
        "-c", "CREATE EXTENSION IF NOT EXISTS pg_trgm",
        "-c", "CREATE EXTENSION IF NOT EXISTS vector",
        "-c", "CREATE EXTENSION IF NOT EXISTS postgis",
    )
    yield


@pytest.fixture()
def migrations_dir(tmp_path):
    """A real, throwaway copy of the actual migrations directory, so a test
    that tampers with a file's content never touches the real repo files."""
    d = tmp_path / "migrations"
    shutil.copytree(_REAL_MIGRATIONS_DIR, d)
    return d


def _applied_versions() -> list[tuple[str, str]]:
    with psycopg.connect(_MIGTEST_DSN) as conn:
        rows = conn.execute(
            "SELECT version, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]


def test_first_run_applies_both_migrations_in_order(clean_migtest_db, migrations_dir):
    applied = run_migrations(_MIGTEST_DSN, migrations_dir)
    assert applied == ["0001_baseline", "0002_p0_api"]

    recorded = _applied_versions()
    assert [v for v, _ in recorded] == ["0001_baseline", "0002_p0_api"]

    # Real, independent proof the schema actually landed - not just that
    # schema_migrations says so: query a table 0001 creates and a function
    # 0002 creates.
    with psycopg.connect(_MIGTEST_DSN) as conn:
        conn.execute("SELECT count(*) FROM tenants").fetchone()
        conn.execute(
            "SELECT proname FROM pg_proc WHERE proname = 'set_kill_switch'"
        ).fetchone()


def test_adopt_existing_schema_records_without_rerunning_sql(clean_migtest_db, migrations_dir):
    """Simulates core/'s real situation on the actual VPS: 0001_baseline's SQL
    (= docs/reference/schema.sql, byte-identical) was already applied by hand,
    long before this migration runner existed. Running it again verbatim
    would collide with the tables it already created - adoption must record
    it as applied WITHOUT executing its SQL a second time."""
    baseline_sql = (migrations_dir / "0001_baseline.sql").read_text()
    with psycopg.connect(_MIGTEST_DSN, autocommit=True) as conn:
        conn.execute(baseline_sql)  # the "applied by hand" step, outside any migration runner

    applied = run_migrations(_MIGTEST_DSN, migrations_dir, adopt_existing_schema_version="0001_baseline")
    assert applied == ["0002_p0_api"], "0001 must be ADOPTED (recorded, not re-run), only 0002 actually runs"

    recorded = dict(_applied_versions())
    assert set(recorded) == {"0001_baseline", "0002_p0_api"}
    real_checksum = _sha256_of(migrations_dir / "0001_baseline.sql")
    assert recorded["0001_baseline"] == real_checksum, (
        "the adopted row must carry 0001's REAL checksum, so a later edit to "
        "the file is still caught as tampering, exactly like a normally-applied migration"
    )

    # A normal run afterwards is a real no-op, same as if it had gone through
    # run_migrations from the start.
    assert run_migrations(_MIGTEST_DSN, migrations_dir) == []

    # And tampering with the ADOPTED migration is still caught.
    baseline_file = migrations_dir / "0001_baseline.sql"
    baseline_file.write_text(baseline_file.read_text() + "\n-- tampered after adoption\n")
    with pytest.raises(MigrationError, match="checksum mismatch"):
        run_migrations(_MIGTEST_DSN, migrations_dir)


def test_adopt_existing_schema_is_a_noop_once_something_is_already_recorded(clean_migtest_db, migrations_dir):
    """Adoption must never fire once schema_migrations has ANY row - it's a
    one-time bootstrap, not a standing override."""
    run_migrations(_MIGTEST_DSN, migrations_dir)  # normal first run: applies both for real
    applied = run_migrations(_MIGTEST_DSN, migrations_dir, adopt_existing_schema_version="0001_baseline")
    assert applied == [], "adoption must be ignored once the table is no longer empty"


def _sha256_of(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_text().encode("utf-8")).hexdigest()


def test_second_run_is_a_noop(clean_migtest_db, migrations_dir):
    first = run_migrations(_MIGTEST_DSN, migrations_dir)
    assert first == ["0001_baseline", "0002_p0_api"]

    second = run_migrations(_MIGTEST_DSN, migrations_dir)
    assert second == [], "re-running against an up-to-date database must be a real no-op"

    # Still exactly two recorded rows - nothing re-applied, nothing duplicated.
    recorded = _applied_versions()
    assert len(recorded) == 2


def test_tampering_with_an_applied_migration_is_rejected(clean_migtest_db, migrations_dir):
    run_migrations(_MIGTEST_DSN, migrations_dir)

    baseline_file = migrations_dir / "0001_baseline.sql"
    original = baseline_file.read_text()
    baseline_file.write_text(original + "\n-- tampered after being applied\n")

    with pytest.raises(MigrationError, match="checksum mismatch"):
        run_migrations(_MIGTEST_DSN, migrations_dir)

    # The rejection must not have silently recorded the new checksum.
    recorded = dict(_applied_versions())
    with psycopg.connect(_MIGTEST_DSN) as conn:
        untampered_checksum = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE version = %s",
            ("0001_baseline",),
        ).fetchone()[0]
    assert recorded["0001_baseline"] == untampered_checksum


def test_advisory_lock_is_always_released(clean_migtest_db, migrations_dir):
    run_migrations(_MIGTEST_DSN, migrations_dir)
    # If run_migrations leaked the advisory lock (e.g. forgot the `finally`),
    # a second, unrelated connection would still see it held.
    with psycopg.connect(_MIGTEST_DSN) as conn:
        held = conn.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "AND classid = 21320 AND objid = 1095915329"
        ).fetchone()[0]
    assert held == 0


def test_cli_migrate_subcommand_applies_and_is_idempotent(
    clean_migtest_db, migrations_dir, monkeypatch, capsys,
):
    monkeypatch.setenv("CORE_MIGRATION_DATABASE_URL", _MIGTEST_DSN)

    rc = cli.main(["migrate", "--migrations-dir", str(migrations_dir)])
    assert rc == 0
    assert "0001_baseline" in capsys.readouterr().out

    rc2 = cli.main(["migrate", "--migrations-dir", str(migrations_dir)])
    assert rc2 == 0
    assert capsys.readouterr().out.strip() == "already up to date"


def test_cli_migrate_subcommand_refuses_on_tamper(clean_migtest_db, migrations_dir, monkeypatch, capsys):
    monkeypatch.setenv("CORE_MIGRATION_DATABASE_URL", _MIGTEST_DSN)
    cli.main(["migrate", "--migrations-dir", str(migrations_dir)])

    baseline_file = migrations_dir / "0001_baseline.sql"
    baseline_file.write_text(baseline_file.read_text() + "\n-- tampered\n")

    rc = cli.main(["migrate", "--migrations-dir", str(migrations_dir)])
    assert rc == 1
    assert "checksum mismatch" in capsys.readouterr().err


def test_cli_migrate_subcommand_reports_missing_dir(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CORE_MIGRATION_DATABASE_URL", os.environ["CORE_MIGRATION_DATABASE_URL"])
    rc = cli.main(["migrate", "--migrations-dir", str(tmp_path / "does-not-exist")])
    assert rc == 2
    assert "not found" in capsys.readouterr().err


def test_cli_migrate_subcommand_adopt_existing_schema(clean_migtest_db, migrations_dir, monkeypatch, capsys):
    monkeypatch.setenv("CORE_MIGRATION_DATABASE_URL", _MIGTEST_DSN)
    baseline_sql = (migrations_dir / "0001_baseline.sql").read_text()
    with psycopg.connect(_MIGTEST_DSN, autocommit=True) as conn:
        conn.execute(baseline_sql)

    rc = cli.main([
        "migrate", "--migrations-dir", str(migrations_dir),
        "--adopt-existing-schema", "0001_baseline",
    ])
    assert rc == 0
    out = capsys.readouterr()
    assert "adopting" in out.err
    assert out.out.strip() == "applied: 0002_p0_api", "only 0002 should show as actually applied this run"

    recorded = dict(_applied_versions())
    assert set(recorded) == {"0001_baseline", "0002_p0_api"}
