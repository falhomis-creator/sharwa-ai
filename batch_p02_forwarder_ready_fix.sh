#!/usr/bin/env bash
set -euo pipefail
cd /home/sharwa/sharwa_ai

echo "=== 1) gateway/src/forwarder.js — wait for the redis-durable client to be ready before the first command ==="
python3 - <<'PYEOF'
path = "gateway/src/forwarder.js"
with open(path, "r", encoding="utf-8") as f:
    src = f.read()

old_import = "import { createRedisClient, closeRedisClient } from './redis.js';"
assert old_import in src, "redis.js import line not found verbatim - aborting"

old_ensure_group = """async function ensureGroup(client, stream) {
  try {
    await client.xgroup('CREATE', stream, config.legacyForwarderGroup, '0', 'MKSTREAM');
  } catch (err) {
    if (!String(err.message).includes('BUSYGROUP')) throw err;
  }
}"""
assert old_ensure_group in src, "ensureGroup not found verbatim - aborting"

addition = """

/**
 * Wait for the redis-durable client to finish its TCP+AUTH handshake before
 * issuing the first command.
 *
 * redis.js's createRedisClient() deliberately uses `lazyConnect: false` +
 * `enableOfflineQueue: false` (H3: fail fast rather than silently buffer a
 * command in memory). That is the right choice for a per-message operation
 * with a spool fallback (sessions.js's defaultAppendToWal) - but ensureGroup()
 * below is this process's FIRST command, called synchronously right after
 * createRedisClient(). The handshake can never complete before that next line
 * of synchronous code runs, so without this wait the command was NOT racing
 * occasionally - it was guaranteed to fail on every single startup with
 * "Stream isn't writeable and enableOfflineQueue options is false", crash the
 * process (main().catch -> process.exit(1)), and crash-loop forever under
 * `restart: unless-stopped` (observed on real Docker: every restart, no
 * exceptions, regardless of backoff delay - confirming it was ordering, not
 * timing).
 */
function waitForReady(client) {
  if (client.status === 'ready') return Promise.resolve();
  return new Promise((resolve, reject) => {
    const onReady = () => {
      client.off('error', onError);
      resolve();
    };
    const onError = (err) => {
      client.off('ready', onReady);
      reject(err);
    };
    client.once('ready', onReady);
    client.once('error', onError);
  });
}"""

src = src.replace(old_ensure_group, old_ensure_group + addition)

old_main_body = """async function main() {
  const client = createRedisClient();
  client.on('error', (err) => {
    logger.error({ err: err.message }, '[forwarder] redis-durable client error');
  });

  for (const stream of streamKeys()) {
    await ensureGroup(client, stream);
  }"""
assert old_main_body in src, "main() startup block not found verbatim - aborting"

new_main_body = """async function main() {
  const client = createRedisClient();
  client.on('error', (err) => {
    logger.error({ err: err.message }, '[forwarder] redis-durable client error');
  });

  await waitForReady(client);

  for (const stream of streamKeys()) {
    await ensureGroup(client, stream);
  }"""

src = src.replace(old_main_body, new_main_body)

with open(path, "w", encoding="utf-8") as f:
    f.write(src)
print("forwarder.js patched OK")
PYEOF

echo "=== 2) gateway/src/__tests__/forwarder.test.js — add a unit test for the ordering bug itself ==="
python3 - <<'PYEOF'
path = "gateway/src/__tests__/forwarder.test.js"
with open(path, "r", encoding="utf-8") as f:
    src = f.read()

import re
m = re.search(r"import \{([^}]*)\} from '\.\./forwarder\.js';", src)
assert m, "could not parse forwarder.js import list - aborting"
names = [n.strip() for n in m.group(1).split(',') if n.strip()]
# waitForReady is intentionally NOT exported (internal helper) - test it
# indirectly isn't practical without a fake ioredis client with EventEmitter
# semantics, so we cover it with a minimal fake emitter here and import it
# by adding it to the export list, matching the project's existing pattern of
# exporting small pure/testable helpers from forwarder.js.
if 'waitForReady' not in names:
    names.append('waitForReady')
new_import_line = "import { " + ", ".join(names) + " } from '../forwarder.js';"
src = re.sub(r"import \{[^}]*\} from '\.\./forwarder\.js';", new_import_line, src)

addition = """

test('waitForReady resolves immediately when the client is already ready', async () => {
  const client = { status: 'ready' };
  await waitForReady(client); // must not hang/throw
});

test('waitForReady waits for the ready event before resolving (the ordering bug this fixes)', async () => {
  const handlers = {};
  const client = {
    status: 'connecting',
    once(event, fn) { handlers[event] = fn; },
    off(event, fn) { if (handlers[event] === fn) delete handlers[event]; },
  };
  const p = waitForReady(client);
  let resolved = false;
  p.then(() => { resolved = true; });
  await Promise.resolve(); // let the promise executor run
  assert.equal(resolved, false); // must NOT resolve before 'ready' fires
  handlers.ready();
  await p;
  assert.equal(resolved, true);
});

test('waitForReady rejects if the client errors before becoming ready', async () => {
  const handlers = {};
  const client = {
    status: 'connecting',
    once(event, fn) { handlers[event] = fn; },
    off(event, fn) { if (handlers[event] === fn) delete handlers[event]; },
  };
  const p = waitForReady(client);
  const boom = new Error('ECONNREFUSED');
  handlers.error(boom);
  await assert.rejects(p, /ECONNREFUSED/);
});
"""

assert src.rstrip().endswith("});"), "forwarder.test.js does not end with a test block as expected - aborting"
src = src.rstrip() + addition
with open(path, "w", encoding="utf-8") as f:
    f.write(src)
print("forwarder.test.js patched OK")
PYEOF

echo "=== 3) also export waitForReady from forwarder.js (test needs it) ==="
python3 - <<'PYEOF'
path = "gateway/src/forwarder.js"
with open(path, "r", encoding="utf-8") as f:
    src = f.read()

old_export = "export { toWebhookPayload, deliverOne, streamKeys, shouldForwardToLegacy };"
assert old_export in src, "export line not found verbatim - aborting"
new_export = "export { toWebhookPayload, deliverOne, streamKeys, shouldForwardToLegacy, waitForReady };"
src = src.replace(old_export, new_export)
with open(path, "w", encoding="utf-8") as f:
    f.write(src)
print("forwarder.js export list patched OK")
PYEOF

echo "=== 4) verify: hunt_gate (repo root) + node --test (gateway/) ==="
set +e
node scripts/hunt_gate.mjs
HUNT_EXIT=$?
set -e
echo "hunt_gate exit: $HUNT_EXIT"

cd gateway
node --test 2>&1 | tail -30

echo "=== 5) rebuild and restart just gateway-forwarder (same image as gateway - both get the fix) ==="
cd /home/sharwa/sharwa_ai
docker compose build gateway
docker compose up -d gateway gateway-forwarder
sleep 5
docker compose ps gateway gateway-forwarder
echo "--- gateway-forwarder logs (last 30 lines, should show '[forwarder] started', no fatal error) ---"
docker compose logs --tail=30 gateway-forwarder

echo "=== 6) git status / diff ==="
git status
git diff --stat
