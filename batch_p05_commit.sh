#!/usr/bin/env bash
# batch_p05_commit.sh
#
# Explicit commit step for P0.5 (session lifecycle state machine: G6
# reconnect/DisconnectReason handling, G7 lease + fencing for multi-
# instance coordination, G8 human-takeover-signal detection) + the F8
# dedicated-Redis-connection fix for the outbound worker's BLMOVE loop.
#
# Stages ONLY the exact P0.5 file set (source + tests + docs) — never a
# blanket `git add -A`/`git add .` — so nothing stray (old delivery
# scripts left in the repo root, *_run.log scratch files, etc.) gets
# swept into this commit. Prints the full diff --stat and status for you
# to read BEFORE committing, and pauses for confirmation.
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

# The exact P0.5 file set - source, tests, docs. Deliberately explicit (no
# globs like gateway/src/**) so a stray file never gets swept in silently
# (in particular: the batch_p0*.sh delivery scripts and *_run.log files
# sitting in the repo root from earlier phases must NOT be committed here).
P05_PATHS=(
  gateway/src/config.js
  gateway/src/lease.js
  gateway/src/sessions.js
  gateway/src/index.js
  gateway/src/forwarder.js
  gateway/src/driver/waDriver.js
  gateway/src/ingest/spool.js
  gateway/src/__tests__/forwarder.test.js
  gateway/src/__tests__/webhook.test.js
  gateway/src/__tests__/lease.test.js
  gateway/src/__tests__/session_state.test.js
  gateway/src/__tests__/human_takeover.test.js
  gateway/src/__tests__/rehydrate_staging.test.js
  gateway/src/__tests__/dual_instance_chaos.test.js
  gateway/src/__tests__/_dual_instance_chaos_child.mjs
  gateway/.env.example
  docs/P0_DEVIATIONS.md
  docs/P0_FINDINGS.md
)

echo "=== [1/4] Sanity check: every P0.5 path exists ==="
for p in "${P05_PATHS[@]}"; do
  if [ ! -e "$p" ]; then
    echo "ERROR: expected path missing: $p (did batch_p05_session_lifecycle.sh and batch_p05_dual_instance_timing_fix.sh both run successfully first?)" >&2
    exit 1
  fi
  echo "  OK: $p"
done

echo ""
echo "=== [2/4] git add (scoped to the P0.5 paths above ONLY) ==="
git add -- "${P05_PATHS[@]}"

echo ""
echo "=== [3/4] Review what is about to be committed ==="
echo "--- git status --short (staged vs. not) ---"
git status --short
echo ""
echo "--- git diff --cached --stat (exactly what this commit will contain) ---"
git diff --cached --stat
echo ""
echo "--- anything else still unstaged/untracked in the repo (NOT part of this commit, left alone) ---"
git status --short | grep -v '^A \|^M ' || echo "  (nothing else)"

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
P0.5: session lifecycle state machine (G6 reconnect, G7 lease/fencing, G8 human takeover)

- gateway/src/lease.js: Redis-backed distributed lease + fencing.
  SET key value PX <ttl> NX for atomic acquisition; INCR fence:{sid}
  for a monotonic fencing token; renew/release use compare-then-act
  Lua scripts (GET then PEXPIRE/DEL only on an exact value match) so a
  stale/expired owner can never renew or release a lease another
  instance has since acquired.
- gateway/src/sessions.js (major rework): sockets are now opened via
  openSocketForSession on both first start AND every reconnect, gated
  by acquireLease. classifyDisconnect maps the 4 real DisconnectReason
  codes (restartRequired/loggedOut/forbidden/connectionReplaced) to
  restart/logged_out/banned/conflict, each driving the correct
  reconnect/exit path with exponential backoff + jitter
  (scheduleReconnect). Losing the lease during renewal
  (handleLeaseLost) closes the socket immediately via sock.end() -
  explicitly WITHOUT calling logout(), which would deauthorize the
  real WhatsApp session. rehydrateSessions() boots registered sessions
  through a bounded worker pool (REHYDRATE_CONCURRENCY, default 2)
  with jittered spacing. startLeaseSweep() periodically retries
  sessions this instance does not currently hold, the failover
  mechanism. getSessionStatus/mapConnectionStatus's existing external
  contract (consumed by Django) is UNCHANGED - a regression test
  (session_state.test.js) proves it explicitly; all new P0.5 state is
  exposed only via the new, additive GET /sessions/:id/health.
  processFromMeMessage (G8): a fromMe:true message whose WhatsApp id
  is NOT already in sent_ids:{sid} (written by outbound/queue.js
  BEFORE sendMessage() is called) is a genuine human reply typed on
  the merchant's own phone - recorded as a human_takeover_signal WAL
  entry, direction outbound_human, never routed through the ordinary
  inbound pipeline.
- gateway/src/config.js: new P0.5 config block (instanceId,
  lease.ttlMs/renewMs/sweepMs, rehydrate.concurrency/jitterMinMs/
  jitterMaxMs, maxSessions, reconnect.baseMs/maxMs/jitterMs/
  conflictBackoffMs), all env-overridable with documented defaults and
  startup validation.
- gateway/src/index.js: new GET /sessions/:id/health route; POST
  /sessions now returns 503 on CAPACITY (MAX_SESSIONS reached) and 409
  on LEASE_HELD; shutdown now stops the lease sweep and releases every
  lease this instance still owns (releaseAllOwnedLeases) before
  closing Redis.
- gateway/src/forwarder.js: shouldForwardToLegacy now also skips
  human_takeover_signal entries (G8 - never forwarded to Django as an
  ordinary customer message).
- gateway/src/driver/waDriver.js: fetchLatestBaileysVersion() moved
  here from sessions.js and now only runs on the real-driver branch -
  it used to fire unconditionally on every socket open, including
  under FakeWaDriver, a real gap this phase's own tests were the first
  to exercise (see docs/P0_DEVIATIONS.md D-19).
- gateway/src/ingest/spool.js: new spoolStatus() export, surfaced by
  GET /sessions/:id/health's spool field.
- F8 fix (docs/P0_FINDINGS.md): every session's outbound send-queue
  worker used to share the ONE shared Redis connection for its
  blocking BLMOVE poll loop - ioredis multiplexes all commands over a
  single TCP connection per client, so N sessions' concurrent BLMOVE
  loops starved every other command (lease ops, WAL appends) queued
  behind them, eventually causing real "Command timed out" errors.
  Each outbound worker now gets its own dedicated Redis connection
  (startSessionOutboundWorker, stopOutboundWorker), closed alongside
  the worker at every stop site (finalizeSessionExit, handleLeaseLost,
  logoutSession, releaseAllOwnedLeases). Measured impact: the P0.5
  rehydrate-concurrency acceptance test dropped from ~35-44s to ~9s
  locally, and a P0.4 test that had unexplainably taken 5-10s dropped
  to ~2.5s in the same run - the same architectural bug, present since
  P0.4, just not severe enough with 1-2 sessions to cause an outright
  failure until P0.5's own tests ran enough concurrent sessions.
- F9 fix (docs/P0_FINDINGS.md): dual_instance_chaos.test.js's initial-
  lease-acquisition wait was widened from 5000ms to 20000ms (and the
  failover margin from +4000ms to +10000ms) after a real VPS run
  showed it timing out under full-suite CPU contention (two real OS
  child processes booting while several other CPU/timing-sensitive
  test files ran concurrently) - reproduced deliberately under induced
  CPU contention locally to confirm the new margin holds, not guessed.
- Tests: lease.test.js (6 real-Redis unit tests of acquire/renew/
  release/fencing/expiry), session_state.test.js (classifyDisconnect +
  the frozen-contract regression guard), human_takeover.test.js (4
  real-Redis tests of processFromMeMessage, including the bot-echo and
  race-safety cases), rehydrate_staging.test.js (the official
  REHYDRATE_CONCURRENCY acceptance test), dual_instance_chaos.test.js
  + _dual_instance_chaos_child.mjs (the official two-real-instance
  kill -9 lease-failover acceptance test), forwarder.test.js
  (human_takeover_signal skip case added), webhook.test.js (updated
  for startSessionOutboundWorker's now-async, dedicated-connection
  API).
- docs/P0_DEVIATIONS.md: D-19 (lease/fencing implementation choices,
  the MAX_SESSIONS real-measurement caveat and its documented 10x
  safety margin, the human-takeover WAL entry shape decision, the
  LEASE_SWEEP_MS >= 1000 floor found while building the dual-instance
  test). docs/P0_FINDINGS.md: F8 (the BLMOVE shared-connection
  starvation bug) and F9 (the dual-instance test's real VPS timing
  failure under full-suite contention, and its fix).

Verified on the VPS: node --check on every changed/new file, hunt_gate
clean of any new violation (only pre-existing, unrelated scratch-script
warnings), and the full real-Redis test suite (104 tests, via docker
compose run against the live redis-durable) passing 104/104 - including
both new P0.5 acceptance tests (staged rehydrate concurrency, and the
real two-process kill -9 lease-failover test) - confirmed clean across
multiple runs on the VPS itself, after fixing one real timing issue (F9)
surfaced by the VPS's own full-suite run.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CkxAKMfgEw8DfNPpm6i7b3
COMMITMSG
)"

echo ""
echo "=== Done. Commit created: ==="
git log -1 --stat
