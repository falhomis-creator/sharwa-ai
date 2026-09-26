#!/usr/bin/env bash
# NOTE: set -e is on so a python3 patch failure (asserts below) hard-stops the
# script before any later, dependent patch runs against a half-changed file.
# The one step allowed to fail without aborting is hunt_gate.mjs, whose exit
# code just reports violation count - see the explicit `|| true` there (this
# fixes the "batch script exits before node --test runs" bug from the P0.2
# integration batch).
set -euo pipefail
cd /home/sharwa/sharwa_ai

echo "=== 1) gateway/src/ingest/lidmap.js — add buildIdentityUpdateEvent ==="
python3 - <<'PYEOF'
import re

path = "gateway/src/ingest/lidmap.js"
with open(path, "r", encoding="utf-8") as f:
    src = f.read()

old = """export async function recordPhoneNumberShare(client, sessionId, lid, jid) {
  const phone_e164 = toE164(jid);
  if (!lid || phone_e164 === null) return { stored: false, phone_e164: null };

  const key = lidmapKey(sessionId);
  const alreadyPresent = await client.hexists(key, lid);
  if (!alreadyPresent) {
    const size = await client.hlen(key);
    if (size >= config.lidmapMax) {
      return { stored: false, phone_e164 }; // bounded (H4)
    }
  }
  await client.hset(key, lid, phone_e164);
  return { stored: true, phone_e164 };
}"""

assert old in src, "recordPhoneNumberShare block not found verbatim - aborting"

new = old + """

/**
 * Build the WAL entry recording a lid -> phone_e164 resolution (R3_DIRECTIVE:
 * "lidmap على redis-durable ومدخل identity_update"). This is bookkeeping for a
 * future P1 core consumer reading the stream directly - the legacy Django
 * webhook has no field for it, so forwarder.js recognizes type ===
 * 'identity_update' and ACKs it without calling postInboundMessage (see
 * forwarder.js::shouldForwardToLegacy).
 *
 * provider_message_id is DETERMINISTIC (lid + phone_e164, not random/ts-based)
 * so a repeated `chats.phoneNumberShare` for an already-known pair dedupes via
 * the normal wal.js::dedupeKeyFor path instead of appending a fresh stream
 * entry every time Baileys re-emits the event (which it does on reconnects).
 *
 * @param {string} lid
 * @param {string} phone_e164
 * @returns {object} normalized entry, ready for wal.js::appendEvent
 */
export function buildIdentityUpdateEvent(lid, phone_e164) {
  return {
    type: 'identity_update',
    provider_message_id: `identity:${lid}:${phone_e164}`,
    ts: Date.now(),
    lid,
    phone_e164,
  };
}"""

src = src.replace(old, new)
with open(path, "w", encoding="utf-8") as f:
    f.write(src)
print("lidmap.js patched OK")
PYEOF

echo "=== 2) gateway/src/ingest/__tests__/lidmap.test.js — add buildIdentityUpdateEvent test ==="
python3 - <<'PYEOF'
path = "gateway/src/ingest/__tests__/lidmap.test.js"
with open(path, "r", encoding="utf-8") as f:
    src = f.read()

old_import = "import { createLidMap, recordPhoneNumberShare } from '../lidmap.js';"
assert old_import in src, "import line not found verbatim - aborting"
new_import = "import { createLidMap, recordPhoneNumberShare, buildIdentityUpdateEvent } from '../lidmap.js';"
src = src.replace(old_import, new_import)

addition = """

test('buildIdentityUpdateEvent produces a deterministic provider_message_id for the same lid/phone pair', () => {
  const a = buildIdentityUpdateEvent('999@lid', '+201111112222');
  const b = buildIdentityUpdateEvent('999@lid', '+201111112222');
  assert.equal(a.type, 'identity_update');
  assert.equal(a.provider_message_id, b.provider_message_id);
  assert.equal(a.provider_message_id, 'identity:999@lid:+201111112222');
  assert.equal(a.lid, '999@lid');
  assert.equal(a.phone_e164, '+201111112222');
  assert.equal(typeof a.ts, 'number');
});

test('buildIdentityUpdateEvent gives different lids different provider_message_ids', () => {
  const a = buildIdentityUpdateEvent('111@lid', '+201111112222');
  const b = buildIdentityUpdateEvent('222@lid', '+201111112222');
  assert.notEqual(a.provider_message_id, b.provider_message_id);
});
"""

assert src.rstrip().endswith("});"), "test file does not end with a test block as expected - aborting"
src = src.rstrip() + addition + "\n"

with open(path, "w", encoding="utf-8") as f:
    f.write(src)
print("lidmap.test.js patched OK")
PYEOF

echo "=== 3) gateway/src/sessions.js — import identity_update deps + rewrite phoneNumberShare handler ==="
python3 - <<'PYEOF'
path = "gateway/src/sessions.js"
with open(path, "r", encoding="utf-8") as f:
    src = f.read()

# 3a. imports
old_imports = """import { appendEvent } from './ingest/wal.js';
import { spool } from './ingest/spool.js';
import { createLidMap, recordPhoneNumberShare } from './ingest/lidmap.js';"""
assert old_imports in src, "import block not found verbatim - aborting"
new_imports = """import { appendEvent, dedupeKeyFor } from './ingest/wal.js';
import { markDone } from './ingest/dedupe.js';
import { spool } from './ingest/spool.js';
import { createLidMap, recordPhoneNumberShare, buildIdentityUpdateEvent } from './ingest/lidmap.js';
import { config } from './config.js';"""
src = src.replace(old_imports, new_imports)

# 3b. handler
old_handler = """  // G12: record lid -> phone_e164 mappings as WhatsApp discloses them.
  sock.ev.on('chats.phoneNumberShare', ({ lid, jid } = {}) => {
    if (!redisClient) return;
    recordPhoneNumberShare(redisClient, sessionId, lid, jid).catch((err) => {
      logger.error({ sessionId, err: err.message }, '[sessions] failed to record phoneNumberShare (G12)');
    });
  });"""
assert old_handler in src, "phoneNumberShare handler not found verbatim - aborting"

new_handler = """  // G12: record lid -> phone_e164 mappings as WhatsApp discloses them, and
  // (R3_DIRECTIVE) append an identity_update WAL entry so a future P1 core
  // consumer can react to the resolution without re-deriving it from raw
  // messages. Mirrors processInboundMessage's spool-on-failure path (F2) via
  // the same defaultAppendToWal helper.
  sock.ev.on('chats.phoneNumberShare', ({ lid, jid } = {}) => {
    if (!redisClient) return;
    (async () => {
      const res = await recordPhoneNumberShare(redisClient, sessionId, lid, jid);
      if (!res.stored) return; // non-resolvable jid, or bounded out at lidmapMax (H4)

      const event = buildIdentityUpdateEvent(lid, res.phone_e164);
      const appendRes = await defaultAppendToWal({ sessionId, event });
      if (appendRes.status === 'appended') {
        // No separate "delivery confirmed" step exists for identity_update
        // (unlike messages, which wait for forwarder.js to reach Django) -
        // the entry is fully durable the moment it lands on the stream, so
        // extend its dedupe marker to the long TTL right away (G2-equivalent).
        await markDone(redisClient, dedupeKeyFor(sessionId, event.provider_message_id), config.dedupeDoneTtlS * 1000);
      }
    })().catch((err) => {
      logger.error({ sessionId, err: err.message }, '[sessions] failed to record phoneNumberShare (G12)');
    });
  });"""

src = src.replace(old_handler, new_handler)

with open(path, "w", encoding="utf-8") as f:
    f.write(src)
print("sessions.js patched OK")
PYEOF

echo "=== 4) gateway/src/forwarder.js — skip identity_update entries (legacy webhook has no field for them) ==="
python3 - <<'PYEOF'
path = "gateway/src/forwarder.js"
with open(path, "r", encoding="utf-8") as f:
    src = f.read()

old_fn = """/** Build the Django webhook payload from a normalized WAL entry. */
function toWebhookPayload(sessionId, entry) {"""
assert old_fn in src, "toWebhookPayload not found verbatim - aborting"

new_fn = """/**
 * identity_update entries (G12/R3_DIRECTIVE) are bookkeeping for a future P1
 * core consumer reading the stream directly - the legacy Django webhook has
 * no field for a lid/phone resolution, so the forwarder ACKs these without
 * calling postInboundMessage rather than sending a garbage payload (F2: this
 * is a deliberate, logged skip, not a silent drop - see processStream).
 *
 * @param {object} entry
 * @returns {boolean}
 */
function shouldForwardToLegacy(entry) {
  return entry.type !== 'identity_update';
}

/** Build the Django webhook payload from a normalized WAL entry. */
function toWebhookPayload(sessionId, entry) {"""

src = src.replace(old_fn, new_fn)

old_loop = """    let data;
    try {
      const raw = parseFields(fields);
      data = JSON.parse(raw.data);
    } catch (err) {
      logger.error({ stream, id, err: err.message }, '[forwarder] corrupt stream entry; ACKing to avoid poison-pill loop');
      await client.xack(stream, config.legacyForwarderGroup, id);
      continue;
    }
    let ok = false;"""
assert old_loop in src, "processStream parse block not found verbatim - aborting"

new_loop = """    let data;
    try {
      const raw = parseFields(fields);
      data = JSON.parse(raw.data);
    } catch (err) {
      logger.error({ stream, id, err: err.message }, '[forwarder] corrupt stream entry; ACKing to avoid poison-pill loop');
      await client.xack(stream, config.legacyForwarderGroup, id);
      continue;
    }

    if (!shouldForwardToLegacy(data)) {
      logger.info({ stream, id, type: data.type }, '[forwarder] skipping legacy delivery for non-message entry');
      await client.xack(stream, config.legacyForwarderGroup, id);
      continue;
    }

    let ok = false;"""

src = src.replace(old_loop, new_loop)

old_export = "export { toWebhookPayload, deliverOne, streamKeys };"
assert old_export in src, "export line not found verbatim - aborting"
new_export = "export { toWebhookPayload, deliverOne, streamKeys, shouldForwardToLegacy };"
src = src.replace(old_export, new_export)

with open(path, "w", encoding="utf-8") as f:
    f.write(src)
print("forwarder.js patched OK")
PYEOF

echo "=== 5) gateway/src/__tests__/forwarder.test.js — add shouldForwardToLegacy test ==="
python3 - <<'PYEOF'
path = "gateway/src/__tests__/forwarder.test.js"
with open(path, "r", encoding="utf-8") as f:
    src = f.read()

old_import_marker = "from '../forwarder.js';"
assert old_import_marker in src, "forwarder.js import not found - aborting"

import re
m = re.search(r"import \{([^}]*)\} from '\.\./forwarder\.js';", src)
assert m, "could not parse forwarder.js import list - aborting"
names = [n.strip() for n in m.group(1).split(',') if n.strip()]
if 'shouldForwardToLegacy' not in names:
    names.append('shouldForwardToLegacy')
new_import_line = "import { " + ", ".join(names) + " } from '../forwarder.js';"
src = re.sub(r"import \{[^}]*\} from '\.\./forwarder\.js';", new_import_line, src)

addition = """

test('shouldForwardToLegacy forwards ordinary message entries', () => {
  assert.equal(shouldForwardToLegacy({ type: 'text', text: 'hi' }), true);
  assert.equal(shouldForwardToLegacy({ type: 'image' }), true);
});

test('shouldForwardToLegacy skips identity_update entries (G12: no legacy field for them)', () => {
  assert.equal(shouldForwardToLegacy({ type: 'identity_update', lid: '1@lid', phone_e164: '+201111112222' }), false);
});
"""

assert src.rstrip().endswith("});"), "forwarder.test.js does not end with a test block as expected - aborting"
src = src.rstrip() + addition
with open(path, "w", encoding="utf-8") as f:
    f.write(src)
print("forwarder.test.js patched OK")
PYEOF

echo "=== 6) verify ==="
# hunt_gate.mjs lives at the REPO ROOT (scripts/hunt_gate.mjs) and scans the
# whole tree (paths like gateway/src/...) - it must run from
# /home/sharwa/sharwa_ai, NOT from gateway/. node --test, on the other hand,
# must run from gateway/ (that's where its package.json's test script lives).
set +e
node scripts/hunt_gate.mjs
HUNT_EXIT=$?
set -e
echo "hunt_gate exit: $HUNT_EXIT"

cd gateway
node --test 2>&1 | tail -80
echo "=== done (see hunt_gate exit above; node --test summary is the tail above) ==="
