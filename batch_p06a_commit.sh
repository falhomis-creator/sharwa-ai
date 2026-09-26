#!/usr/bin/env bash
# batch_p06a_commit.sh
#
# Explicit commit step for P0.6 Batch A (G10, disasters 19/21 baseline) -
# operational readiness: GET /readyz, GET /metrics (Bearer METRICS_TOKEN-
# protected, prom-client), main() refusing to start with an empty
# METRICS_TOKEN (H5), an extended graceful-shutdown sequence, H12 structured
# logging + phone-number redaction, plus the real test-runner fix (F10:
# node:test's file-level concurrency has no visibility into a file that
# spawns its own real OS child processes - the three files that do are now
# run sequentially among themselves via gateway/scripts/run-tests.mjs, while
# everything else keeps node --test's normal default concurrency).
#
# Stages ONLY the exact P0.6 Batch A file set (source + tests + docs +
# scripts) - never a blanket `git add -A`/`git add .` - so nothing stray (the
# batch_p0*.sh delivery scripts and *_run.log scratch files sitting in the
# repo root from earlier phases) gets swept into this commit. Prints the full
# diff --stat and status for you to read BEFORE committing, and pauses for
# confirmation.
#
# Run from the repository root: /home/sharwa/sharwa_ai
set -euo pipefail

REPO_ROOT="/home/sharwa/sharwa_ai"
if [ ! -d "$REPO_ROOT/gateway" ]; then
  echo "ERROR: $REPO_ROOT/gateway not found. Run this script from a machine where the repo lives at $REPO_ROOT, or edit REPO_ROOT at the top of this script." >&2
  exit 1
fi
cd "$REPO_ROOT"

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "ERROR: not a git repository" >&2; exit 1; }

# The exact P0.6 Batch A file set - source, tests, scripts, docs, deliberately
# explicit (no globs) so nothing stray is swept in silently.
P06A_PATHS=(
  gateway/src/config.js
  gateway/src/sessions.js
  gateway/src/index.js
  gateway/src/metrics.js
  gateway/src/logger.js
  gateway/src/__tests__/dual_instance_chaos.test.js
  gateway/src/__tests__/p06_operational_readiness.test.js
  gateway/scripts/run-tests.mjs
  gateway/package.json
  gateway/package-lock.json
  gateway/.env.example
  docs/P0_DEVIATIONS.md
  docs/P0_FINDINGS.md
)

echo "=== [1/4] Sanity check: every P0.6 Batch A path exists ==="
for p in "${P06A_PATHS[@]}"; do
  if [ ! -e "$p" ]; then
    echo "ERROR: expected path missing: $p (did batch_p06a_operational_readiness.sh, batch_p06a_timing_fix.sh, batch_p06a_timing_fix2.sh, and batch_p06a_timing_fix3.sh all run successfully first, in that order?)" >&2
    exit 1
  fi
  echo "  OK: $p"
done

echo ""
echo "=== [1b/4] Confirm the now-superseded gateway/scripts/run-tests.sh is gone (removed by batch_p06a_timing_fix3.sh) ==="
if [ -e gateway/scripts/run-tests.sh ]; then
  echo "ERROR: gateway/scripts/run-tests.sh still exists - batch_p06a_timing_fix3.sh should have removed it. Remove it manually (git rm gateway/scripts/run-tests.sh) before committing, or re-run that script." >&2
  exit 1
fi
echo "  OK: gateway/scripts/run-tests.sh is gone (only run-tests.mjs remains)"

echo ""
echo "=== [2/4] git add (scoped to the P0.6 Batch A paths above ONLY) ==="
git add -- "${P06A_PATHS[@]}"

echo ""
echo "=== [3/4] Review what is about to be committed ==="
echo "--- git status --short (staged vs. not) ---"
git status --short
echo ""
echo "--- git diff --cached --stat (exactly what this commit will contain) ---"
git diff --cached --stat
echo ""
echo "--- anything else still unstaged/untracked in the repo (NOT part of this commit, left alone) ---"
git status --short | grep -v '^A \|^M \|^D ' || echo "  (nothing else)"

echo ""
read -p "Proceed with the commit above? [y/N] " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
  echo "Aborted — nothing committed. Files remain staged; run 'git reset' to unstage if you want to start over."
  exit 1
fi

echo ""
echo "=== [4/4] Committing ==="
git commit -m "$(cat <<'COMMITMSG'
P0.6 Batch A: operational readiness (G10, disasters 19/21 baseline) - readyz, metrics, graceful shutdown, H12 logging

- gateway/src/config.js: new P0.6 config block. metricsToken is read but
  deliberately NOT added to the shared `problems` throw-on-import list
  (config.js is transitively imported by every test file via sessions.js;
  throwing there would force METRICS_TOKEN into every test's environment
  for values that are only a live security control in the real running
  process - see docs/P0_DEVIATIONS.md D-21). readyz.spoolStaleS,
  shutdown.timeoutMs/downloadDrainTimeoutMs added with validation
  (downloadDrainTimeoutMs must stay < shutdownTimeoutMs; shutdownTimeoutMs
  capped at 25000ms per the spec's own stop_grace_period ceiling).
- gateway/src/metrics.js (new): a process-wide prom-client Registry with
  collectDefaultMetrics() (confirmed, not assumed, to emit the exact spec-
  required names: process_resident_memory_bytes, nodejs_heap_size_used_bytes,
  process_open_fds) plus session_state{state} (a gauge, incremented/
  decremented on every real status transition and on session-record
  removal), session_reconnects_total{reason}, and lease_lost_total - all
  three wired to sessions.js's own existing P0.5 transition points, not
  simulated. Every other metric family in the spec's fixed list
  (ingest_*/out_*/forwarder_*/dlq_length/stream_*/media_*/killswitch_*) is
  deliberately left out of this batch and documented as such (D-22) rather
  than registered-but-never-incremented, which would read as a fabricated
  "confirmed zero" to anything watching it.
- gateway/src/index.js: GET /readyz (503 if redis-durable unreachable, spool
  non-empty longer than SPOOL_STALE_S, at/over MAX_SESSIONS, or shutdown
  already started) and GET /metrics (Bearer METRICS_TOKEN, timingSafeEqual,
  same posture as the existing X-API-Key check) added. main() now refuses to
  start at all with an empty/missing METRICS_TOKEN (H5) - the one place this
  value is a live security control rather than a config constant. shutdown()
  extended to the spec's full sequence: stop new HTTP requests first, drain
  in-flight media downloads (bounded, polling media.js's real inflight
  counter), close every session socket WITHOUT logout() + flush the spool
  one final time + release every owned lease (releaseAllOwnedLeases already
  did the socket/lease half), exit within shutdown.timeoutMs with a hard
  force-exit fallback if any step hangs.
- gateway/src/sessions.js: getSessionCount() (readyz's capacity check) added;
  the connection.update handler now calls recordSessionStateTransition on
  every real status change (guarding the synthetic 'UNKNOWN' pre-tracking
  sentinel so it is never itself counted/decremented); scheduleReconnect
  increments session_reconnects_total{reason}; handleLeaseLost increments
  lease_lost_total; both session-record deletion sites (finalizeSessionExit,
  logoutSession) untrack the gauge so a removed session's last-known state
  does not linger in it forever.
- gateway/src/logger.js: logEvent(log, {event, tenant_id, session_id,
  msg_id, duration_ms, outcome, ...extra}) - H12's fixed structured-log
  field set. Phone-number redaction (H5: last 3 digits visible) implemented
  as a pino formatters.log hook that masks any 7-15 digit run wherever it
  appears in a log object, rather than enumerating every field name phone
  numbers are logged under across the codebase (phoneNumber, phone_number,
  connected_phone_number, phone_e164, `to`, ... - grepped, not guessed) -
  deliberately the safer over-masking direction (documented in the file
  itself) since under-masking would leak a real number to disk.
- gateway/scripts/run-tests.mjs (new) + gateway/package.json: the test-runner
  fix for F10 (docs/P0_FINDINGS.md - a real, three-times-confirmed VPS
  finding). The files that spawn real OS child processes
  (dual_instance_chaos.test.js, outbound_chaos.test.js,
  p06_operational_readiness.test.js) are invisible to node --test's own
  CPU-aware file-level concurrency, so left at default concurrency (or even
  a blanket --test-concurrency=2, tried and rejected - it cost ~24.6 minutes
  of suite time AND still produced a false failure in an unrelated heavy
  test paired against another) enough of them land in the same window to
  starve each other under real contention. run-tests.mjs runs those three
  sequentially among themselves (--test-concurrency=1) and everything else
  at normal default concurrency; `npm test` now calls it, so it can never
  drift from what delivery/CI scripts actually run. Written in plain Node
  (child_process.spawnSync) rather than a shell script after a real,
  VPS-confirmed failure of that first attempt: node:24-alpine (the actual
  gateway container image) has no `bash` at all.
- gateway/src/__tests__/p06_operational_readiness.test.js (new): three real-
  process acceptance tests - METRICS_TOKEN startup refusal (spawns a real
  gateway process, asserts non-zero exit + the fatal log naming the reason),
  and a real running gateway's /healthz -> /readyz -> /metrics (401 without
  a token, 401 with a wrong token, 200 + real Prometheus exposition text
  with a correct one) -> SIGTERM graceful-shutdown-within-budget sequence.
- gateway/src/__tests__/dual_instance_chaos.test.js: F10 - waitUntil margins
  widened further (20000->40000ms initial acquisition,
  +10000->+20000ms failover) as a second line of defense on top of
  run-tests.mjs's concurrency isolation, not as the primary fix.
- gateway/.env.example: METRICS_TOKEN (required - never commented out),
  SPOOL_STALE_S/SHUTDOWN_TIMEOUT_MS/DOWNLOAD_DRAIN_TIMEOUT_MS (optional,
  documented defaults) added.
- gateway/package.json/package-lock.json: prom-client@15.1.3 added (exact
  pin), chosen over the newer official @prometheus-io/client after actually
  running collectDefaultMetrics() from both and confirming prom-client's
  output names match the spec's required metric names exactly
  (docs/P0_DEVIATIONS.md D-20).
- docs/P0_DEVIATIONS.md: D-20 (prom-client dependency choice, H11), D-21
  (METRICS_TOKEN startup-refusal enforcement location - index.js's main(),
  not config.js's eager throw-on-import path), D-22 (updates D-15: /metrics
  is now real; records exactly which metric families are wired this batch
  and which are deliberately deferred, and why).
- docs/P0_FINDINGS.md: F10 - the full three-attempt record of the real
  VPS test-runner contention/timing saga (kept in full, including the two
  failed intermediate attempts - a blanket --test-concurrency=2 that cost
  ~24.6 minutes and caused a false failure elsewhere, then a bash script
  that could not run in the actual node:24-alpine container at all - not
  erased, since each was itself a real, confirmed finding).

Out of scope for this batch, deliberately (docs/P0_DEVIATIONS.md D-22): the
gateway-side kill-switch (P0.6 Batch B) and the remaining
ingest/outbound/forwarder/media/dlq/stream Prometheus metric families +
ops/prometheus/*.yml + the docker-compose.yml/Dockerfile memory-limit change
(P0.6 Batch C).

Verified on the VPS: node --check on every changed/new file, hunt_gate clean
of any new violation, and the full real-Redis test suite (106 tests, via
docker compose against the live redis-durable, run through the corrected
gateway/scripts/run-tests.mjs) passing 106/106 - confirmed after fixing two
real, VPS-only test-runner problems (F10) that the local sandbox environment
could not have caught on its own (a container image without bash, and the
true cost of naively capping test concurrency), neither of which was guessed
or assumed away.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CkxAKMfgEw8DfNPpm6i7b3
COMMITMSG
)"

echo ""
echo "=== Done. Commit created: ==="
git log -1 --stat
