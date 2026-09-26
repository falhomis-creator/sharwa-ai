#!/usr/bin/env bash
# batch_p06b_diag_and_verify.sh
#
# Replaces batch_p06b_startup_fix.sh's steps [3/8] onward. That script's file
# writes and syntax checks ([1/8], [2/8]) ALREADY SUCCEEDED on your VPS, so
# nothing is re-written here - this script only verifies those files are in
# place, then runs the diagnostics and tests.
#
# WHY THE LAST ONE HUNG (my mistake, fixed here):
#   `docker compose run` allocates a pseudo-TTY and attaches to stdin unless
#   you pass -T. I used -T on the `compose exec` call and forgot it on the
#   `compose run` diagnostics - hence "failed to resize tty, using default
#   size" followed by a hang that even `timeout` could not clear (Compose in
#   attached-TTY mode does not tear down cleanly on SIGTERM).
#
# HOW THIS SCRIPT CANNOT HANG:
#   1. The diagnostics create NO containers at all any more. `docker compose
#      config` resolves the compose file HOST-SIDE, which answers the real
#      question (what REDIS_* the gateway service actually gets, and whether a
#      redis-cache service exists) without running anything.
#   2. Every docker invocation that remains passes -T, redirects stdin from
#      /dev/null, and is wrapped in `timeout -k 30 --foreground` so a SIGTERM
#      that is ignored is followed by a SIGKILL 30s later.
#   3. No step can abort the script; each records PASS/FAIL and a summary
#      prints at the end.
#
# PASSWORD SAFETY: `docker compose config` prints resolved .env values, which
# would include your real passwords. Every line matching
# password/secret/token/key is filtered out BEFORE anything is printed, so it
# is safe to paste this output back to me. Nothing secret is displayed.
#
# Run from the repository root: /home/sharwa/sharwa_ai
set -uo pipefail

REPO_ROOT="/home/sharwa/sharwa_ai"
cd "$REPO_ROOT" || { echo "ERROR: $REPO_ROOT not found" >&2; exit 1; }

DC="docker compose"
$DC version >/dev/null 2>&1 || DC="docker-compose"

# Every line printed from compose config passes through this first.
strip_secrets() { grep -viE 'password|secret|token|_key|apikey|credential'; }

echo "=== [1/7] Confirm the three files from the previous script are in place ==="
check_marker() {
  local file="$1" marker="$2"
  if [ -f "$file" ] && grep -q "$marker" "$file"; then
    echo "  OK: $file (contains '$marker')"
  else
    echo "  MISSING/STALE: $file does not contain '$marker' - re-run batch_p06b_startup_fix.sh" >&2
  fi
}
check_marker gateway/src/killswitch.js startTimeoutMs
check_marker gateway/src/__tests__/outbound_chaos.test.js childEvidence
check_marker docs/P0_FINDINGS.md F13
node --check gateway/src/killswitch.js && echo "  OK: killswitch.js parses"
node --check gateway/src/__tests__/outbound_chaos.test.js && echo "  OK: outbound_chaos.test.js parses"

echo ""
echo "=== [2/7] DIAGNOSTIC (host-side, no container): which services does compose define? ==="
timeout -k 10 --foreground 60 $DC config --services 2>&1 | sed 's/^/  /' || echo "  (could not list services)"

echo ""
echo "=== [3/7] DIAGNOSTIC (host-side, no container): every REDIS_* the compose file resolves ==="
echo "  (secrets filtered out; this is what killswitch.js will really connect to)"
timeout -k 10 --foreground 60 $DC config 2>/dev/null \
  | grep -iE 'REDIS_(DURABLE|CACHE)_(HOST|PORT)|^[[:space:]]*redis-(cache|durable):|container_name:.*redis' \
  | strip_secrets | sed 's/^/  /' || echo "  (no REDIS_* host/port entries found in the resolved compose file)"
echo "  --- is REDIS_CACHE_HOST mentioned anywhere in the resolved compose file at all? ---"
if timeout -k 10 --foreground 60 $DC config 2>/dev/null | grep -qi 'REDIS_CACHE_HOST'; then
  echo "  YES - REDIS_CACHE_HOST is set somewhere in compose"
else
  echo "  NO - REDIS_CACHE_HOST is never set, so config.js falls back to 127.0.0.1:6379 (this is the F12 root cause)"
fi

echo ""
echo "=== [4/7] DIAGNOSTIC: does redis-durable require a password? ==="
echo "  (NOAUTH in the reply = yes, password-protected; PONG = no password)"
timeout -k 10 --foreground 45 docker exec sharwa_ai-redis-durable-1 redis-cli ping 2>&1 | head -3 | sed 's/^/  /' \
  || echo "  (docker exec failed - is the container named sharwa_ai-redis-durable-1 and running?)"

echo ""
echo "=== [5/7] DIAGNOSTIC: containers (LISTED ONLY - nothing is removed) ==="
docker ps -a --format '  {{.Names}}  |  {{.Status}}  |  {{.Image}}' 2>/dev/null | grep -i sharwa || echo "  (none matching sharwa)"
echo "  If leftover 'sharwa_ai-gateway-run-*' containers appear above (from the interrupted"
echo "  runs, including the two you Ctrl+C'd), they still hold RAM/CPU. I am NOT removing"
echo "  anything. To clean them up yourself when you choose:"
echo "      docker rm -f \$(docker ps -aq --filter 'name=sharwa_ai-gateway-run-')"

# ---------------------------------------------------------------------------
# Tests. -T (no TTY), stdin from /dev/null, hard SIGKILL backstop.
# ---------------------------------------------------------------------------
RUN_ENV=(-e ALLOW_FAKE_WA=1 -e NODE_ENV=test -e LOG_LEVEL=error -e METRICS_TOKEN=test-token -e REDIS_CACHE_HOST=redis-durable -e REDIS_CACHE_PORT=6379)
MOUNTS=(-v "$(pwd)/gateway/src:/app/src:ro" -v "$(pwd)/gateway/scripts:/app/scripts:ro" -v "$(pwd)/gateway/test-support:/app/test-support:ro" -v "$(pwd)/gateway/package.json:/app/package.json:ro")

P06_STATUS="not run"
FULL_STATUS="not run"

echo ""
echo "=== [6/7] The graceful-shutdown test that has never actually run, ISOLATED ==="
echo "  This is the one that exercises main() + shutdown() with the kill-switch wired."
echo "  It is where F13's ~8.5s shutdown stall would have force-exited with code 1."
timeout -k 30 --foreground 300 $DC run --rm -T "${RUN_ENV[@]}" "${MOUNTS[@]}" gateway \
  node --test src/__tests__/p06_operational_readiness.test.js </dev/null
if [ $? -eq 0 ]; then P06_STATUS="PASS"; else P06_STATUS="FAIL/TIMEOUT"; fi
echo "  -> p06_operational_readiness: $P06_STATUS"

echo ""
echo "=== [7/7] Full suite via the real npm test ==="
echo "  (includes outbound_chaos - if it fails again it will now carry the child's own"
echo "   stdout/stderr in the failure message, and fail fast instead of hanging)"
timeout -k 30 --foreground 700 $DC run --rm -T "${RUN_ENV[@]}" "${MOUNTS[@]}" gateway npm test </dev/null
if [ $? -eq 0 ]; then FULL_STATUS="PASS"; else FULL_STATUS="FAIL/TIMEOUT"; fi

echo ""
echo "============================ SUMMARY ============================"
echo "  p06_operational_readiness (isolated): $P06_STATUS"
echo "  full suite (npm test)               : $FULL_STATUS"
echo "================================================================="
echo ""
echo "--- git status --short (NOTHING was committed by this script) ---"
git status --short
echo ""
git diff --stat -- gateway/src/killswitch.js gateway/src/__tests__/outbound_chaos.test.js docs/P0_FINDINGS.md 2>/dev/null || true

echo ""
echo "Paste this whole output. Steps [2/7]-[4/7] settle the D-26 question (does a"
echo "redis-cache service exist, is REDIS_CACHE_HOST set anywhere, does redis-durable"
echo "need a password), and the SUMMARY tells me whether Batch B is verified. With"
echo "those I can wire docker-compose.yml properly and prepare batch_p06b_commit.sh."
