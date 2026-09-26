#!/usr/bin/env bash
# p0_full_diagnostic.sh — ONE consolidated, READ-ONLY diagnostic.
#
# Changes nothing. Creates no containers except two `docker exec` calls into
# containers that are ALREADY running. Every step is bounded by `timeout -k`,
# and no step can abort the script, so it always reaches the end.
#
# SECRET SAFETY: docker-compose.yml and .env are printed with every
# secret-looking VALUE masked (key names are kept, values replaced). Nothing
# secret is displayed, so the whole output is safe to paste back.
#
# It also writes everything to p0_diagnostic_output.txt for easy pasting.
set -uo pipefail
cd /home/sharwa/sharwa_ai || { echo "ERROR: repo not found"; exit 1; }
exec > >(tee p0_diagnostic_output.txt) 2>&1

DC="docker compose"; $DC version >/dev/null 2>&1 || DC="docker-compose"
sec() { echo ""; echo "################ $* ################"; }
# mask VALUES of secret-looking keys, in both `KEY: value` and `KEY=value` forms
mask() {
  sed -E 's/^([[:space:]]*-?[[:space:]]*[A-Za-z0-9_]*(PASSWORD|SECRET|TOKEN|KEY|CREDENTIAL|DSN|URL)[A-Za-z0-9_]*[[:space:]]*[:=]).*/\1 <MASKED>/I' \
  | sed -E '/\$\{/! s/(--requirepass"?,?[[:space:]]*")[^"]*(")/\1<MASKED>\2/g' \
  | sed -E 's/(:\/\/[^:@[:space:]]+):[^@[:space:]]+@/\1:<MASKED>@/g'
}

sec "A. repo tree (depth 2, no node_modules/.git)"
find . -maxdepth 2 \( -name node_modules -o -name .git -o -name auth_sessions \) -prune -o -print 2>/dev/null | sed 's|^\./||' | sort | head -80

sec "B. which P0 components already exist"
for p in core console tests/e2e ops ops/prometheus docker-compose.test.yml scripts/hunt_gate.mjs docs/PHASE_GATE.md docs/P0_PROGRESS.md docs/P0_REPORT.md docs/reference/schema.sql gateway/Dockerfile; do
  [ -e "$p" ] && echo "  EXISTS : $p" || echo "  MISSING: $p"
done

sec "C. docker-compose.yml (SECRET VALUES MASKED)"
mask < docker-compose.yml

sec "D. gateway/Dockerfile"
cat gateway/Dockerfile 2>/dev/null || echo "(none)"

sec "E. .env KEY NAMES ONLY (values never printed)"
[ -f .env ] && sed -E 's/=.*/= <value hidden>/' .env | grep -v '^#' | grep . || echo "(no .env)"

sec "F. does redis-cache require a password? (correct test: clear REDISCLI_AUTH first)"
echo "--- redis-cache, REDISCLI_AUTH cleared ---"
timeout -k 5 20 docker exec sharwa_ai-redis-cache-1 sh -c 'unset REDISCLI_AUTH; redis-cli ping' 2>&1 | head -2
echo "--- redis-durable, REDISCLI_AUTH cleared (re-testing my earlier unreliable PONG) ---"
timeout -k 5 20 docker exec sharwa_ai-redis-durable-1 sh -c 'unset REDISCLI_AUTH; redis-cli ping' 2>&1 | head -2
echo "--- is REDISCLI_AUTH actually set inside those containers? (presence only) ---"
timeout -k 5 20 docker exec sharwa_ai-redis-cache-1   sh -c '[ -n "${REDISCLI_AUTH:-}" ] && echo "redis-cache: REDISCLI_AUTH IS set" || echo "redis-cache: not set"' 2>&1 | head -2
timeout -k 5 20 docker exec sharwa_ai-redis-durable-1 sh -c '[ -n "${REDISCLI_AUTH:-}" ] && echo "redis-durable: REDISCLI_AUTH IS set" || echo "redis-durable: not set"' 2>&1 | head -2

sec "G. git state"
git log --oneline -12
echo "--- current branch / status (short) ---"
git rev-parse --abbrev-ref HEAD
git status --short | grep -v '^?? batch_p0' | grep -v '^?? p0' | grep -v 'run\.log$'
echo "--- any CRLF files? (audit-3 item; empty = good) ---"
git ls-files --eol 2>/dev/null | grep 'w/crlf' | head -5 || echo "  (none)"

sec "H. host resources (Batch C mem_limit decisions need real numbers)"
nproc; free -m; echo "--- docker stats snapshot ---"
timeout -k 10 45 docker stats --no-stream --format '  {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' 2>&1 | head -12

sec "I. containers (note the stray gateway-run-* one)"
docker ps -a --format '  {{.Names}}\t{{.Status}}\t{{.Image}}' | head -15

sec "J. toolchain versions available on the host"
echo "node: $(node -v 2>&1)"; echo "npm: $(npm -v 2>&1)"
echo "python3: $(python3 -V 2>&1)"; echo "pip3: $(pip3 -V 2>&1 | head -1)"
echo "docker: $(docker -v 2>&1)"

sec "K. kill_switches + channel_accounts schema (exact columns - P0.7 core API needs them)"
grep -n -A 22 'CREATE TABLE.*kill_switches' docs/reference/schema.sql 2>/dev/null | head -35 || echo "(not found)"
echo "--- channel_accounts ---"
grep -n -A 18 'CREATE TABLE.*channel_accounts' docs/reference/schema.sql 2>/dev/null | head -28 || echo "(not found)"
echo "--- effective_switch function, if any ---"
grep -n 'effective_switch' docs/reference/schema.sql 2>/dev/null | head -5 || echo "(none)"

sec "L. PHASE_GATE + P0_PROGRESS current content"
cat docs/PHASE_GATE.md 2>/dev/null || echo "(none)"
echo "--- P0_PROGRESS.md (last 25 lines) ---"
tail -25 docs/P0_PROGRESS.md 2>/dev/null || echo "(none)"

sec "M. gateway package.json scripts + deps"
cat gateway/package.json 2>/dev/null

sec "DONE"
echo "Everything above is also saved to: p0_diagnostic_output.txt"
