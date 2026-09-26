#!/usr/bin/env bash
# ops/gather_p07b2_context.sh - read-only, no writes/commits/docker up-down.
# Two specific files needed before writing Batch B's docker-compose `api`
# service DSNs correctly (never guessing at pgbouncer's real auth model).
set -euo pipefail
echo "== ops/pgbouncer.ini =="
cat ops/pgbouncer.ini
echo
echo "== ops/postgres/init/10_roles.sh =="
cat ops/postgres/init/10_roles.sh
