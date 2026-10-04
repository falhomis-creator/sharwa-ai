#!/usr/bin/env bash
# scripts/migrate.sh - apply core/migrations/*.sql via the api container.
#
# H62: migrations are NEVER auto-run by the image. This is the one sanctioned
# operator step, and it distinguishes the TWO bootstrap states EXPLICITLY:
#
#   * FRESH local database      -> scripts/migrate.sh
#       (applies all migrations in order, no adoption flag)
#   * VPS existing schema       -> scripts/migrate.sh --adopt-existing-schema 0001_baseline
#       (records 0001_baseline as already-applied WITHOUT running its SQL, then
#        applies the rest normally - see core/Dockerfile's own header)
#
# Do not mix the two states: the flag is explicit, never guessed.
set -euo pipefail

# P4.1: --entrypoint python bypasses core/entrypoint.sh on purpose. The image's entrypoint
# VERIFIES migrations before running any command (H62), so routing the migrate command
# through it would fail with "migrations missing" - the very state we are fixing.

show_pending() {
    echo "migrate.sh: pending migrations (to be applied):"
    docker compose run -T --rm --no-deps --entrypoint python api -c '
import os, glob, psycopg
exp = sorted(os.path.basename(p)[:-4] for p in glob.glob("migrations/*.sql"))
c = psycopg.connect(os.environ["CORE_MIGRATION_DATABASE_URL"])
try:
    applied = {r[0] for r in c.execute("SELECT version FROM schema_migrations").fetchall()}
except Exception:
    applied = set()
print("  pending:", [v for v in exp if v not in applied])
'
}

show_applied() {
    echo "migrate.sh: applied list:"
    docker compose run -T --rm --no-deps --entrypoint python api -c '
import os, psycopg
c = psycopg.connect(os.environ["CORE_MIGRATION_DATABASE_URL"])
print("  " + ", ".join(r[0] for r in c.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()))
'
}

case "${1:-}" in
    "")
        echo "migrate.sh: FRESH database path (applying all migrations in order)"
        ADOPT_ARGS=()
        ;;
    --adopt-existing-schema)
        VERSION="${2:-}"
        if [ -z "$VERSION" ]; then
            echo "migrate.sh: --adopt-existing-schema requires a VERSION (e.g. 0001_baseline)" >&2
            exit 2
        fi
        echo "migrate.sh: VPS existing-schema path - adopting ${VERSION} as already-applied (its SQL will NOT be run)"
        ADOPT_ARGS=(--adopt-existing-schema "$VERSION")
        ;;
    *)
        echo "migrate.sh: unknown argument '${1:-}' (use --adopt-existing-schema VERSION for a VPS with a hand-applied 0001_baseline)" >&2
        exit 2
        ;;
esac

show_pending

echo "migrate.sh: applying..."
docker compose run --rm --no-deps --entrypoint python api -m app.cli migrate "${ADOPT_ARGS[@]}"

show_applied
