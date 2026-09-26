#!/usr/bin/env bash
set -euo pipefail
cd /home/sharwa/sharwa_ai

echo "=== 1) gateway/src/forwarder.js — BLOCK_MS must stay safely below the ioredis commandTimeout ==="
python3 - <<'PYEOF'
path = "gateway/src/forwarder.js"
with open(path, "r", encoding="utf-8") as f:
    src = f.read()

old = """const CONSUMER_NAME = `forwarder-${process.pid}`;
const BLOCK_MS = 5000;
const CLAIM_IDLE_MS = 30_000; // claim entries idle for this long from dead consumers
const BATCH_SIZE = 50;"""
assert old in src, "constants block not found verbatim - aborting"

new = """const CONSUMER_NAME = `forwarder-${process.pid}`;
// XREADGROUP's own BLOCK parameter is a SERVER-side wait; redis.js's
// createRedisClient() also sets a CLIENT-side `commandTimeout` (from
// config.redis.timeoutMs, default 5000ms) that aborts ANY command - including
// this intentionally-blocking one - if it doesn't get a reply in time. Setting
// BLOCK_MS equal to (or above) that timeout means the client-side watchdog
// fires at essentially the same instant the server would naturally return an
// empty result, so on an idle stream EVERY cycle raced and lost: ioredis threw
// "Command timed out", the outer loop logged "[forwarder] loop error; backing
// off" and slept 1s, over and over (observed on real Docker - not a crash, but
// a permanent noisy poll instead of a real long-poll, plus an extra Redis round
// trip every ~6s). Keeping a safety margin below the command timeout lets the
// command return (with or without data) before that watchdog can fire.
const BLOCK_MS = Math.max(1000, config.redis.timeoutMs - 1500);
const CLAIM_IDLE_MS = 30_000; // claim entries idle for this long from dead consumers
const BATCH_SIZE = 50;"""

src = src.replace(old, new)

old_export = "export { toWebhookPayload, deliverOne, streamKeys, shouldForwardToLegacy, waitForReady };"
assert old_export in src, "export line not found verbatim - aborting"
new_export = "export { toWebhookPayload, deliverOne, streamKeys, shouldForwardToLegacy, waitForReady, BLOCK_MS };"
src = src.replace(old_export, new_export)

with open(path, "w", encoding="utf-8") as f:
    f.write(src)
print("forwarder.js patched OK")
PYEOF

echo "=== 2) gateway/src/__tests__/forwarder.test.js — regression guard: BLOCK_MS must stay below commandTimeout ==="
python3 - <<'PYEOF'
path = "gateway/src/__tests__/forwarder.test.js"
with open(path, "r", encoding="utf-8") as f:
    src = f.read()

import re
m = re.search(r"import \{([^}]*)\} from '\.\./forwarder\.js';", src)
assert m, "could not parse forwarder.js import list - aborting"
names = [n.strip() for n in m.group(1).split(',') if n.strip()]
if 'BLOCK_MS' not in names:
    names.append('BLOCK_MS')
new_import_line = "import { " + ", ".join(names) + " } from '../forwarder.js';"
src = re.sub(r"import \{[^}]*\} from '\.\./forwarder\.js';", new_import_line, src)

if "from '../config.js'" not in src:
    src = src.replace(
        "import assert from 'node:assert/strict';",
        "import assert from 'node:assert/strict';\nimport { config } from '../config.js';",
    )

addition = """

test('BLOCK_MS stays below the ioredis commandTimeout (regression: they raced and XREADGROUP threw "Command timed out" on every idle cycle)', () => {
  assert.ok(
    BLOCK_MS < config.redis.timeoutMs,
    `BLOCK_MS (${BLOCK_MS}) must be less than config.redis.timeoutMs (${config.redis.timeoutMs})`,
  );
});
"""

assert src.rstrip().endswith("});"), "forwarder.test.js does not end with a test block as expected - aborting"
src = src.rstrip() + addition
with open(path, "w", encoding="utf-8") as f:
    f.write(src)
print("forwarder.test.js patched OK")
PYEOF

echo "=== 3) verify: hunt_gate (repo root) + node --test (gateway/) ==="
set +e
node scripts/hunt_gate.mjs
HUNT_EXIT=$?
set -e
echo "hunt_gate exit: $HUNT_EXIT"

cd gateway
node --test 2>&1 | tail -20

echo "=== 4) rebuild and restart gateway-forwarder, then watch it for 30s (idle-stream cycles should now be silent) ==="
cd /home/sharwa/sharwa_ai
docker compose build gateway
docker compose up -d gateway gateway-forwarder
sleep 3
echo "--- watching gateway-forwarder logs for 30s (Ctrl-C safe, this is a fixed-duration timeout) ---"
timeout 30 docker compose logs -f --tail=5 gateway-forwarder || true
echo "--- final status ---"
docker compose ps gateway gateway-forwarder

echo "=== 5) git status / diff ==="
git status
git diff --stat
