#!/usr/bin/env bash
# core/entrypoint.sh - shared bootstrap for the `api` and `worker` services.
#
# NOTE: the architect's prompt refers to this as `scripts/entrypoint.sh`; it
# lives inside core/ because the image's build context is ./core (the image
# must be self-contained, and the repo-root scripts/ dir holds HOST-run scripts
# like scripts/migrate.sh and scripts/check_env.py).
#
# Usage (set by the Dockerfile ENTRYPOINT + each service's CMD/command):
#     entrypoint api
#     entrypoint worker
#
# Before starting the role process it:
#   1. waits (exponential backoff, no fixed sleep) for PostgreSQL through
#      PgBouncer (CORE_DATABASE_URL) to answer `SELECT 1`;
#   2. waits for redis-cache (always) and redis-durable (worker only) via PING;
#   3. verifies migrations are APPLIED (never applies them - H62): reads
#      schema_migrations via CORE_MIGRATION_DATABASE_URL and compares it to the
#      migration files, printing what is missing and exiting non-zero. Only the
#      `api` role carries CORE_MIGRATION_DATABASE_URL; the worker has no
#      schema-owner credentials (it processes untrusted inbound data) and skips
#      this read, relying on the operator's ordered bootstrap (migrate before
#      up). On failure it exits non-zero and `restart: unless-stopped` retries.
#
# Ends with `exec` of the role command so signals reach the real process
# (no PID-1 shim), per H61.
set -euo pipefail

role="${1:-api}"
BOOT_WAIT_TIMEOUT_S="${BOOT_WAIT_TIMEOUT_S:-60}"

log() { printf '[entrypoint:%s] %s\n' "$role" "$*"; }
fail() { printf '[entrypoint:%s] %s\n' "$role" "$*" >&2; exit 1; }
now_s() { date +%s; }

wait_until() {
    local label="$1"
    shift
    local deadline=$(( $(now_s) + BOOT_WAIT_TIMEOUT_S ))
    local backoff=1
    while ! "$@" >/dev/null 2>&1; do
        if [ "$(now_s)" -ge "$deadline" ]; then
            fail "timed out after ${BOOT_WAIT_TIMEOUT_S}s waiting for ${label}"
        fi
        sleep "$backoff"
        backoff=$(( backoff * 2 ))
        [ "$backoff" -gt 10 ] && backoff=10
    done
    log "ready: ${label}"
}

pg_ready() {
    python -c "import os, psycopg; c = psycopg.connect(os.environ['CORE_DATABASE_URL']); c.execute('SELECT 1'); c.close()"
}

redis_cache_ready() {
    python -c "import os, redis; r = redis.Redis(host=os.environ['REDIS_CACHE_HOST'], port=int(os.environ['REDIS_CACHE_PORT']), password=os.environ['REDIS_CACHE_PASSWORD']); r.ping()"
}

redis_durable_ready() {
    python -c "import os, redis; r = redis.Redis(host=os.environ['REDIS_DURABLE_HOST'], port=int(os.environ['REDIS_DURABLE_PORT']), password=os.environ['REDIS_DURABLE_PASSWORD']); r.ping()"
}

verify_migrations() {
    if [ -z "${CORE_MIGRATION_DATABASE_URL:-}" ]; then
        log "skip migration check (no CORE_MIGRATION_DATABASE_URL for this role)"
        return 0
    fi
    python - <<'PY'
import os, glob, sys
import psycopg
dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
expected = sorted(os.path.basename(p)[:-len(".sql")] for p in glob.glob("/app/migrations/*.sql"))
with psycopg.connect(dsn, autocommit=True) as conn:
    try:
        applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
    except Exception:
        applied = set()
missing = [v for v in expected if v not in applied]
if missing:
    print("missing migrations: " + ", ".join(missing), file=sys.stderr)
    sys.exit(1)
print("migrations up to date (%d applied)" % len(applied))
PY
}

# 1. PostgreSQL through PgBouncer.
wait_until "postgres (pgbouncer)" pg_ready

# 2. redis - the worker is the only role that talks to redis-durable.
if [ "$role" = "worker" ]; then
    wait_until "redis-durable" redis_durable_ready
fi
wait_until "redis-cache" redis_cache_ready

# 3. migrations applied (api only; read-only, never applies).
verify_migrations || fail "migrations missing - run scripts/migrate.sh first"

# 4. exec the role command.
case "$role" in
    api)
        exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
        ;;
    worker)
        exec python -m app.workers.realtime
        ;;
    *)
        fail "unknown role '${role}' (expected api|worker)"
        ;;
esac
