#!/usr/bin/env bash
# batch_p04_commit.sh
#
# Explicit commit step for P0.4 (durable outbound send queue, disasters
# #4/#5/#17) + the WaDriver/FakeWaDriver abstraction that closes OQ-7.
#
# Stages ONLY the exact P0.4 file set (source + tests + docs) — never a
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

# The exact P0.4 file set - source, tests, driver/test-support, docs.
# Deliberately explicit (no globs like gateway/src/**) so a stray file
# never gets swept in silently.
P04_PATHS=(
  gateway/src/config.js
  gateway/src/sessions.js
  gateway/src/index.js
  gateway/src/__tests__/webhook.test.js
  gateway/.env.example
  gateway/.dockerignore
  gateway/src/driver/waDriver.js
  gateway/test-support/fakeWaDriver.js
  gateway/src/outbound/queue.js
  gateway/src/outbound/tokenBucket.js
  gateway/src/__tests__/tokenBucket.test.js
  gateway/src/__tests__/outbound_queue.test.js
  gateway/src/__tests__/outbound_chaos.test.js
  gateway/src/__tests__/_outbound_chaos_child.mjs
  docs/P0_DEVIATIONS.md
  docs/P0_OPEN_QUESTIONS.md
  docs/P0_FINDINGS.md
)

echo "=== [1/4] Sanity check: every P0.4 path exists ==="
for p in "${P04_PATHS[@]}"; do
  if [ ! -e "$p" ]; then
    echo "ERROR: expected path missing: $p (did batch_p04_outbound_queue.sh and batch_p04_hunt_gate_fix.sh both run successfully first?)" >&2
    exit 1
  fi
  echo "  OK: $p"
done

echo ""
echo "=== [2/4] git add (scoped to the P0.4 paths above ONLY) ==="
git add -- "${P04_PATHS[@]}"

echo ""
echo "=== [3/4] Review what is about to be committed ==="
echo "--- git status --short (staged vs. not) ---"
git status --short
echo ""
echo "--- git diff --cached --stat (exactly what this commit will contain) ---"
git diff --cached --stat
echo ""
echo "--- anything else still unstaged/untracked in the repo (NOT part of this commit, left alone) ---"
git status --short | grep -v '^A ' || echo "  (nothing else)"

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
P0.4: durable outbound send queue (disasters #4/#5/#17) + FakeWaDriver (OQ-7)

- gateway/src/outbound/queue.js: Redis-backed durable send queue.
  Idempotency via SET outidem:{sid}:{client_msg_id} NX EX 604800; a
  bounded per-session queue (OUT_QUEUE_MAX) returning a clean "full"
  status (surfaced as HTTP 429, was 503 under the old in-RAM queue);
  crash-safe delivery via BLMOVE into out:{sid}:inflight; the WhatsApp
  message id is pre-registered in sent_ids:{sid} before sendMessage()
  is called, and sent_marker:{sid}:{client_msg_id} is written only
  after a confirmed send. recoverInflight() replays/cleans up
  in-flight items left behind by a crash before the worker starts.
- gateway/src/outbound/tokenBucket.js: per-number Lua-atomic token
  bucket (separate "service" and "marketing" buckets), plus a
  marketing daily cap.
- gateway/src/driver/waDriver.js + gateway/test-support/fakeWaDriver.js:
  the WaDriver seam (OQ-7) - sessions.js no longer imports Baileys
  directly to build the socket. FakeWaDriver is the only mock allowed
  in this codebase (H7's named exception), protected by a double gate:
  location under test-support/ (now genuinely excluded via
  .dockerignore - a real gap found and fixed, see P0_DEVIATIONS D-18),
  and both drivers independently refusing to activate unless
  WA_DRIVER=fake, NODE_ENV=test and ALLOW_FAKE_WA=1 are all set.
- gateway/src/sessions.js, gateway/src/index.js, gateway/src/config.js:
  wired the new queue into session creation/recovery and the
  POST /sessions/:id/send route (now accepts optional client_msg_id/
  kind, returns 429 on a full queue).
- Tests: tokenBucket.test.js, outbound_queue.test.js,
  outbound_chaos.test.js (+ its child process
  _outbound_chaos_child.mjs) - the official P0.4 acceptance test: 50
  queued messages, 5x real kill -9 + restart, zero loss, duplicate
  count measured and bounded by the kill count. webhook.test.js's
  send-serialization test rewritten against the new async/durable API
  (H8: never weaken/drop an existing test).
- docs/P0_OPEN_QUESTIONS.md: OQ-7 closed. docs/P0_DEVIATIONS.md: D-18
  (FakeWaDriver, the .dockerignore gap, config defaults, the kind
  interpretation decision, the 503->429 contract change).
  docs/P0_FINDINGS.md: F6 (chaos-test-harness 'exit'-fires-once bug,
  test-harness only) and F7 (2 real hunt_gate violations found+fixed
  in _outbound_chaos_child.mjs; one unreproduced flaky chaos-test
  failure under full-suite load, not reproduced across 2 clean reruns).

Verified on the VPS: node --check on every changed/new file, hunt_gate
clean of any new violation, and the full real-Redis test suite (85
tests, via docker compose run against the live redis-durable) passing
including the P0.4 chaos acceptance test, confirmed clean across
multiple runs (isolated and full-suite).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CkxAKMfgEw8DfNPpm6i7b3
COMMITMSG
)"

echo ""
echo "=== Done. Commit created: ==="
git log -1 --stat
