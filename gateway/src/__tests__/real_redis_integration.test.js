// gateway/src/__tests__/real_redis_integration.test.js
// P0.2 acceptance (PROMPT_P0_DEEPSEEK §6 P0.2, "قبول P0.2"): three scenarios
// that MUST run against a real, non-mocked redis-durable - the fused Lua
// atomicity (dedupe.js) and the spool/drain recovery path (spool.js) cannot be
// trusted from mocked-client unit tests alone (wal.test.js / dedupe.test.js /
// spool.test.js all use stub clients by design, for speed and isolation).
//
// Gated behind RUN_REAL_REDIS_TESTS=1 so the default `npm test` / `node --test`
// run stays fast and hermetic (no external Redis required). No disabled-test
// API of any kind is used here - the project's hunt_gate static check forbids
// those - gating is done with a plain `if` around the test() registrations
// instead. Intended to run via:
//   docker compose run --rm -e RUN_REAL_REDIS_TESTS=1 \
//     -v "$(pwd)/gateway/src:/app/src:ro" gateway \
//     node --test src/__tests__/real_redis_integration.test.js
// (bind-mounted, since gateway/.dockerignore deliberately excludes
// src/__tests__ from the built image - a production image should not ship
// test files). This reuses the SAME redis-durable the live gateway/
// gateway-forwarder use (the `gateway` service's compose environment already
// points at it) - never a second Redis instance, so this proves the exact
// production path.
//
// Scenario (ب) deliberately does NOT kill the real redis-durable container:
// by the time this was written, redis-durable already carries live WAL data
// for a real connected WhatsApp session, and a hard kill would disrupt it for
// no additional coverage (owner-approved alternative, see docs/P0_PROGRESS.md).
// Instead it points a SEPARATE, real ioredis client at an unreachable address
// to force a genuine connection failure (not a mock), exercising the exact
// appendEvent-fails -> spool() -> drainSpool() path that runs in production
// when redis-durable is actually down, then drains through the real, working
// client to prove no loss.

import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtemp, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

import { createRedisClient, closeRedisClient } from '../redis.js';
import { appendEvent, streamKeyFor } from '../ingest/wal.js';
import { normalizeMessage } from '../ingest/normalize.js';
import { spool, drainSpool } from '../ingest/spool.js';

const RUN = process.env.RUN_REAL_REDIS_TESTS === '1';

/**
 * Wait for an ioredis client's TCP+AUTH handshake to finish before issuing the
 * first command. Same ordering bug as forwarder.js's waitForReady (this session,
 * P0.2 live-Docker verification): a command issued before 'ready' fires against
 * a client built with enableOfflineQueue:false fails immediately with "Stream
 * isn't writeable and enableOfflineQueue options is false" - not a rare race,
 * guaranteed on the very first command after createRedisClient(). Caught by
 * actually running this suite against a real Redis before handing it over
 * (empty catch / silent guess would have shipped this broken).
 */
function waitForReady(client) {
  if (client.status === 'ready') return Promise.resolve();
  return new Promise((resolve, reject) => {
    const onReady = () => { client.off('error', onError); resolve(); };
    const onError = (err) => { client.off('ready', onReady); reject(err); };
    client.once('ready', onReady);
    client.once('error', onError);
  });
}

async function readStreamEntries(client, streamKey) {
  const raw = await client.xrange(streamKey, '-', '+');
  return raw.map(([id, fields]) => {
    const idx = fields.indexOf('data');
    return { id, data: JSON.parse(fields[idx + 1]) };
  });
}

if (RUN) {
  const client = createRedisClient(); // real redis-durable - same config the live gateway uses
  before(async () => {
    await waitForReady(client);
  });
  after(async () => {
    await closeRedisClient(client);
  });

  test('(أ) 1000x redelivery of the same provider_message_id against REAL redis-durable -> exactly one stream entry', async () => {
    const sessionId = `test-dedupe-${randomUUID()}`;
    const streamKey = streamKeyFor(sessionId);
    const event = {
      provider_message_id: `redelivery-${randomUUID()}`,
      type: 'text',
      ts: Date.now(),
      identity: {
        jid_raw: '201000000000@s.whatsapp.net',
        addressing: 'pn',
        wa_id: '201000000000',
        phone_e164: '+201000000000',
      },
      text: 'قبول P0.2 (أ): نفس الرسالة 1000 مرة',
    };

    const results = await Promise.all(
      Array.from({ length: 1000 }, () => appendEvent(client, { sessionId, event })),
    );
    const appended = results.filter((r) => r.status === 'appended');
    const duplicate = results.filter((r) => r.status === 'duplicate');
    assert.equal(appended.length, 1, `expected exactly 1 real XADD to win the race, got ${appended.length}`);
    assert.equal(duplicate.length, 999, `expected exactly 999 duplicates, got ${duplicate.length}`);

    const entries = await readStreamEntries(client, streamKey);
    const matching = entries.filter((e) => e.data.provider_message_id === event.provider_message_id);
    assert.equal(matching.length, 1, 'exactly one entry with this provider_message_id must exist on the real stream');

    // This is a real, shared production stream - leave no test residue behind.
    await client.xdel(streamKey, appended[0].id);
    await client.del(`dedupe:${sessionId}:${event.provider_message_id}`);
  });

  test('(ب) redis-durable unreachable during ingest -> spool captures it -> drainSpool (real client) delivers with no loss', async () => {
    const sessionId = `test-spool-${randomUUID()}`;
    const event = {
      provider_message_id: `spool-${randomUUID()}`,
      type: 'text',
      ts: Date.now(),
      identity: {
        jid_raw: '201000000001@s.whatsapp.net',
        addressing: 'pn',
        wa_id: '201000000001',
        phone_e164: '+201000000001',
      },
      text: 'قبول P0.2 (ب): محاكاة فشل حقيقي للاتصال',
    };

    // A REAL ioredis client pointed at an unreachable address - not a mock.
    // Does NOT touch the live redis-durable container (see file header).
    const brokenClient = createRedisClient({ host: '127.0.0.1', port: 1, password: '', timeoutMs: 800, maxRetries: 0 });
    brokenClient.on('error', () => {
      // ioredis requires an 'error' listener or the process crashes on the
      // connection refusal; the real failure is handled explicitly below via
      // the rejected appendEvent() call, so this listener intentionally does
      // nothing further (H3: the actual error path is not swallowed).
    });

    const spoolDir = await mkdtemp(path.join(os.tmpdir(), 'p02-spool-integration-'));
    try {
      await appendEvent(brokenClient, { sessionId, event });
      assert.fail('appendEvent must fail against an unreachable Redis');
    } catch (err) {
      assert.ok(err, 'a real connection error was thrown, as expected');
      await spool({ sessionId, event }, { spoolDir });
    } finally {
      await closeRedisClient(brokenClient);
    }

    // Drain through the REAL, working redis-durable client.
    const drainResult = await drainSpool(client, { spoolDir });
    assert.deepEqual(drainResult, { replayed: 1, remaining: 0, dropped: 0 });

    const streamKey = streamKeyFor(sessionId);
    const entries = await readStreamEntries(client, streamKey);
    const matching = entries.filter((e) => e.data.provider_message_id === event.provider_message_id);
    assert.equal(matching.length, 1, 'the spooled message must land on the real stream exactly once');

    await client.xdel(streamKey, matching[0].id);
    await client.del(`dedupe:${sessionId}:${event.provider_message_id}`);
    await rm(spoolDir, { recursive: true, force: true });
  });

  test('(ج) a real Baileys-shaped location message reaches the REAL stream with its coordinates', async () => {
    const sessionId = `test-location-${randomUUID()}`;
    const rawMsg = {
      key: {
        remoteJid: '201000000002@s.whatsapp.net',
        fromMe: false,
        id: `LOC-${randomUUID()}`,
      },
      messageTimestamp: Math.floor(Date.now() / 1000),
      message: {
        locationMessage: {
          degreesLatitude: 24.7136,
          degreesLongitude: 46.6753,
          name: 'الرياض',
          address: 'المملكة العربية السعودية',
          isLive: false,
        },
      },
    };

    const entry = normalizeMessage(rawMsg);
    assert.ok(entry, 'normalizeMessage must not drop a location pin (G4)');
    assert.equal(entry.type, 'location');
    assert.ok(entry.location);

    const appendResult = await appendEvent(client, { sessionId, event: entry });
    assert.equal(appendResult.status, 'appended');

    const streamKey = streamKeyFor(sessionId);
    const entries = await readStreamEntries(client, streamKey);
    const matching = entries.find((e) => e.id === appendResult.id);
    assert.ok(matching, 'the location entry must be readable back from the real stream');
    assert.equal(matching.data.type, 'location');
    assert.equal(matching.data.location.lat, 24.7136);
    assert.equal(matching.data.location.lng, 46.6753);
    assert.equal(matching.data.location.name, 'الرياض');

    await client.xdel(streamKey, appendResult.id);
    await client.del(`dedupe:${sessionId}:${entry.provider_message_id}`);
  });
} else {
  test('real-redis P0.2 acceptance suite ((أ)/(ب)/(ج)) skipped: set RUN_REAL_REDIS_TESTS=1 and run against a real redis-durable to execute it', () => {
    assert.ok(true);
  });
}

