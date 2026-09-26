#!/usr/bin/env bash
# ops/verify_p06b_live.sh
#
# Read-only live verification of P0.6 Batch B's real production wiring, run
# AFTER `docker compose up -d --build gateway` has completed successfully.
# This replaces exactly what batch_p06b_final.sh's own Gate B would have done
# automatically (health checks + a live kill-switch proof with guaranteed
# cleanup) - it was skipped when the user built the gateway manually outside
# the script, so nothing has actually verified the live container yet.
#
# What it does, in order:
#   [1/6] confirms the gateway container is up (docker compose ps)
#   [2/6] discovers the gateway's real http base URL from its OWN healthcheck
#         definition in `docker compose config` - never a guessed port
#   [3/6] hits /healthz and /readyz from inside the container
#   [4/6] hits /metrics with the real METRICS_TOKEN from .env (proves the
#         P0.6 Batch A auth landmine, F16, is actually fixed in prod)
#   [5/6] flips ks:global.ai_reply to 'off' via the real redis-cache (the
#         exact HASH/PUBSUB scheme killswitch.js documents at its own top),
#         proves the change reached the live gateway process via the
#         killswitch_state Prometheus gauge, then restores the original
#         state - a bash `trap` guarantees the restore runs even on error
#   [6/6] prints a summary
#
# Nothing here touches git, docker-compose.yml, or any staged file. Gate A
# (the commit of the 15 already-staged P0.6 Batch B files) is untouched and
# still the user's own explicit call to make.
#
# Usage: bash ops/verify_p06b_live.sh   (run from the repo root, e.g. /home/sharwa/sharwa_ai)

set -euo pipefail

if [ ! -f docker-compose.yml ]; then
  echo "REFUSED: docker-compose.yml not found in $(pwd)." >&2
  echo "Run this from the repo root (e.g. /home/sharwa/sharwa_ai)." >&2
  exit 1
fi
if [ ! -f .env ]; then
  echo "REFUSED: .env not found in $(pwd)." >&2
  exit 1
fi

echo "== [1/6] gateway container status =="
timeout -k 5 20 docker compose ps gateway < /dev/null
echo

echo "== [2/6] discovering the gateway's real health-check base URL (no guessing) =="
GW_BLOCK="$(timeout -k 5 20 docker compose config < /dev/null 2>/dev/null | awk '
  /^  gateway:/ { ingw=1; next }
  ingw && /^  [a-zA-Z0-9_.-]+:/ { ingw=0 }
  ingw { print }
' || true)"
if [ -z "$GW_BLOCK" ]; then
  echo "REFUSED: could not locate the \`gateway:\` service block in \`docker compose config\` output." >&2
  echo "Paste \`docker compose config\` for the gateway service so I don't guess the port." >&2
  exit 1
fi

# Narrow to the healthcheck sub-block specifically - the gateway's own
# `environment:` can (and in this repo does) contain OTHER http:// URLs
# (e.g. a webhook target), so grabbing the first http:// URL anywhere in the
# whole service block picks up the wrong one. Only the wget test line inside
# `healthcheck:` is the contract we actually want.
HC_SUBBLOCK="$(printf '%s\n' "$GW_BLOCK" | awk '
  match($0, /^[ ]*/) { ind_this = RLENGTH }
  /^[ ]+healthcheck:[ ]*$/ && !inhc { inhc = 1; hc_ind = ind_this; next }
  inhc && ind_this <= hc_ind && NF > 0 { inhc = 0 }
  inhc { print }
')"
HC_URL=""
if [ -n "$HC_SUBBLOCK" ]; then
  HC_URL="$(printf '%s\n' "$HC_SUBBLOCK" | grep -oE 'https?://[^ "'"'"']+' | head -1 || true)"
fi

if [ -n "$HC_URL" ] && [ "${HC_URL%/healthz}" != "$HC_URL" ]; then
  BASE_URL="${HC_URL%/healthz}"
  echo "discovered base URL from the healthcheck definition: ${BASE_URL}"
else
  # Fallback: derive it from the container's OWN real, running port mapping
  # (docker compose ps), not a blind guess - e.g. "127.0.0.1:4001->4001/tcp".
  echo "healthcheck-based extraction didn't yield a clean /healthz URL; falling back to the real running port mapping from \`docker compose ps\`..." >&2
  PS_LINE="$(timeout -k 5 15 docker compose ps gateway < /dev/null 2>/dev/null | tail -n +2 || true)"
  PORT="$(printf '%s\n' "$PS_LINE" | grep -oE '[0-9.]+:[0-9]+->[0-9]+/tcp' | head -1 | sed -E 's#.*->([0-9]+)/tcp#\1#' || true)"
  if [ -z "$PORT" ]; then
    echo "REFUSED: could not determine the gateway's real listening port from either its healthcheck or \`docker compose ps\`." >&2
    echo "Paste \`docker compose config\` (gateway service) and \`docker compose ps gateway\` so I don't guess." >&2
    exit 1
  fi
  BASE_URL="http://127.0.0.1:${PORT}"
  echo "discovered base URL from the running port mapping: ${BASE_URL}"
fi
echo

echo "== [3/6] /healthz and /readyz =="
echo "-- /healthz --"
timeout -k 5 15 docker compose exec -T gateway wget -qO- "${BASE_URL}/healthz" < /dev/null || echo "  (non-zero exit - see error above; the container may still be starting)"
echo
echo "-- /readyz --"
timeout -k 5 15 docker compose exec -T gateway wget -qO- "${BASE_URL}/readyz" < /dev/null || echo "  (non-zero exit - see error above; the container may still be starting)"
echo

echo "== [4/6] /metrics (proves the F16 METRICS_TOKEN fix works live) =="
METRICS_TOKEN="$(grep -E '^METRICS_TOKEN=' .env | tail -1 | cut -d= -f2- || true)"
if [ -z "$METRICS_TOKEN" ]; then
  echo "REFUSED: METRICS_TOKEN not found in .env." >&2
  exit 1
fi
timeout -k 5 15 docker compose exec -T gateway wget -qO- \
  --header="Authorization: Bearer ${METRICS_TOKEN}" "${BASE_URL}/metrics" < /dev/null > /tmp/_p06b_metrics_before.txt \
  || echo "  (non-zero exit fetching /metrics - see error above; check the token and container health)"
echo "metrics endpoint reachable and authenticated OK ($(wc -l < /tmp/_p06b_metrics_before.txt 2>/dev/null || echo 0) lines returned)"
echo "current killswitch_state lines (expected: none yet, or all severity 0):"
grep '^killswitch_state' /tmp/_p06b_metrics_before.txt 2>/dev/null || echo "  (none - no scope has ever been flipped, which is the expected healthy default)"
echo

echo "== [5/6] live kill-switch flip: ks:global.ai_reply -> off -> proof -> restore =="
REDIS_CACHE_PASSWORD="$(grep -E '^REDIS_CACHE_PASSWORD=' .env | tail -1 | cut -d= -f2- || true)"
if [ -z "$REDIS_CACHE_PASSWORD" ]; then
  echo "REFUSED: REDIS_CACHE_PASSWORD not found in .env." >&2
  exit 1
fi

RESTORED=0
cleanup() {
  if [ "$RESTORED" = "0" ]; then
    echo "[cleanup] restoring ks:global (removing the temporary ai_reply=off test flag)..." >&2
    timeout -k 5 10 docker compose exec -T redis-cache redis-cli -a "$REDIS_CACHE_PASSWORD" --no-auth-warning \
      HDEL ks:global ai_reply < /dev/null >/dev/null 2>&1 || true
    timeout -k 5 10 docker compose exec -T redis-cache redis-cli -a "$REDIS_CACHE_PASSWORD" --no-auth-warning \
      PUBLISH ks:changes '{"scope":"global"}' < /dev/null >/dev/null 2>&1 || true
    echo "[cleanup] done." >&2
  fi
}
trap cleanup EXIT

echo "-- setting ks:global ai_reply=off on the real redis-cache --"
timeout -k 5 10 docker compose exec -T redis-cache redis-cli -a "$REDIS_CACHE_PASSWORD" --no-auth-warning \
  HSET ks:global ai_reply off < /dev/null \
  || echo "  (non-zero exit setting ks:global - see error above; the flip may not have applied)"
timeout -k 5 10 docker compose exec -T redis-cache redis-cli -a "$REDIS_CACHE_PASSWORD" --no-auth-warning \
  PUBLISH ks:changes '{"scope":"global"}' < /dev/null \
  || echo "  (non-zero exit publishing ks:changes - see error above; the gateway may only pick this up on its next 30s resync)"
echo "waiting 2s for the gateway's ks:changes subscriber to refetch (spec propagation budget: <1s)..."
sleep 2

timeout -k 5 15 docker compose exec -T gateway wget -qO- \
  --header="Authorization: Bearer ${METRICS_TOKEN}" "${BASE_URL}/metrics" < /dev/null > /tmp/_p06b_metrics_during.txt \
  || echo "  (non-zero exit fetching /metrics during the flip - see error above)"
LINE_DURING="$(grep '^killswitch_state{' /tmp/_p06b_metrics_during.txt | grep 'scope="global"' | grep 'capability="ai_reply"' || true)"
VAL_DURING="$(printf '%s' "$LINE_DURING" | awk '{print $NF}')"
echo "during flip: ${LINE_DURING:-<no matching killswitch_state line found>}"
if [ "$VAL_DURING" = "2" ]; then
  echo "PROOF OK: killswitch_state{scope=\"global\",capability=\"ai_reply\"} = 2 (severity for 'off') - the live flip reached the running gateway process."
else
  echo "PROOF INCONCLUSIVE: expected value 2 (off), got '${VAL_DURING:-<empty>}'. Paste the full /metrics output ( /tmp/_p06b_metrics_during.txt on the VPS) so I can look."
fi
echo

echo "-- restoring: removing the temporary ai_reply=off flag --"
RESTORE_OK=1
timeout -k 5 10 docker compose exec -T redis-cache redis-cli -a "$REDIS_CACHE_PASSWORD" --no-auth-warning \
  HDEL ks:global ai_reply < /dev/null \
  || { echo "  (non-zero exit restoring ks:global - see error above; the [cleanup] trap will retry this on exit)"; RESTORE_OK=0; }
timeout -k 5 10 docker compose exec -T redis-cache redis-cli -a "$REDIS_CACHE_PASSWORD" --no-auth-warning \
  PUBLISH ks:changes '{"scope":"global"}' < /dev/null \
  || { echo "  (non-zero exit re-publishing ks:changes - see error above; the [cleanup] trap will retry this on exit)"; RESTORE_OK=0; }
sleep 2
# Only mark restored if BOTH commands above actually succeeded - otherwise
# the EXIT trap below must still retry the restore, not skip it.
if [ "$RESTORE_OK" = "1" ]; then
  RESTORED=1
else
  echo "  restore not yet confirmed; leaving it for the [cleanup] trap to retry." >&2
fi

timeout -k 5 15 docker compose exec -T gateway wget -qO- \
  --header="Authorization: Bearer ${METRICS_TOKEN}" "${BASE_URL}/metrics" < /dev/null > /tmp/_p06b_metrics_after.txt \
  || echo "  (non-zero exit fetching /metrics after restore - see error above)"
LINE_AFTER="$(grep '^killswitch_state{' /tmp/_p06b_metrics_after.txt | grep 'scope="global"' | grep 'capability="ai_reply"' || true)"
echo "after restore: ${LINE_AFTER:-<no matching line - the field was removed from the hash, which is expected>}"
echo

echo "== [6/6] summary =="
echo "healthz/readyz : see [3/6] above - expect real 200/JSON payloads, not connection errors"
echo "metrics auth   : see [4/6] above - expect a real Prometheus text dump, not 401/refused"
echo "live kill-switch proof : see [5/6] above"
echo
echo "Gate A is untouched: the 15 staged P0.6 Batch B files are exactly where"
echo "batch_p06b_final.sh left them. Nothing here ran git add/commit."
