import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

import { createRedisClient, closeRedisClient } from '../redis.js';

// P0.5 (G7, spec literal): "إقلاع 30 جلسة ⇒ لا أكثر من 2 اتصال متزامن" - booting
// N registered sessions must never open more than REHYDRATE_CONCURRENCY
// connections at once. This test uses a SMALLER N (12) than the spec's "30"
// purely for test wall-clock speed - the mechanism under test (the bounded
// worker pool in sessions.js's rehydrateSessions) is identical at any N.
//
// The real gate that must never exceed REHYDRATE_CONCURRENCY is the
// lease-acquisition step (acquireLease's INCR), not the socket itself - so
// concurrency is measured there, via a thin PROXY around a REAL Redis client
// (every call still hits real Redis; the proxy only counts in-flight calls
// and adds a small artificial delay to widen the measurement window enough
// to actually observe overlap). This is instrumentation, not a mock - H7's
// "FakeWaDriver is the only mock allowed" is about the WhatsApp socket, and
// this test still goes through the real driver seam for that (WA_DRIVER=fake
// under the real triple env gate, exactly like every other P0.4/P0.5 test).

process.env.WA_DRIVER = 'fake';
process.env.NODE_ENV = 'test';
process.env.ALLOW_FAKE_WA = '1';

const N = 8;
const CONCURRENCY = 2;

let tmpDir;
let sessionsMod;
let realRedis;

function waitForReady(client) {
  if (client.status === 'ready') return Promise.resolve();
  return new Promise((resolve, reject) => {
    const onReady = () => { client.off('error', onError); resolve(); };
    const onError = (err) => { client.off('ready', onReady); reject(err); };
    client.once('ready', onReady);
    client.once('error', onError);
  });
}

/** Forwards every call to the real client; counts+delays only INCR (acquireLease's first call per attempt). */
function withConcurrencyProbe(realClient, delayMs, counters) {
  return new Proxy(realClient, {
    get(target, prop, receiver) {
      if (prop === 'incr') {
        return async (...args) => {
          counters.active += 1;
          counters.max = Math.max(counters.max, counters.active);
          await new Promise((resolve) => setTimeout(resolve, delayMs));
          try {
            return await target.incr(...args);
          } finally {
            counters.active -= 1;
          }
        };
      }
      const value = Reflect.get(target, prop, receiver);
      return typeof value === 'function' ? value.bind(target) : value;
    },
  });
}

before(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'p05-rehydrate-'));
  process.env.AUTH_SESSIONS_DIR = tmpDir;
  process.env.REHYDRATE_CONCURRENCY = String(CONCURRENCY);
  process.env.REHYDRATE_JITTER_MIN_MS = '10';
  process.env.REHYDRATE_JITTER_MAX_MS = '30';

  sessionsMod = await import('../sessions.js');
  const { useMultiFileAuthState } = await import('../driver/waDriver.js');

  for (let i = 0; i < N; i += 1) {
    const dir = path.join(tmpDir, `sess-${i}`);
    await fs.mkdir(dir, { recursive: true });
    const { state, saveCreds } = await useMultiFileAuthState(dir);
    state.creds.registered = true;
    await saveCreds();
  }

  realRedis = createRedisClient();
  await waitForReady(realRedis);
});

after(async () => {
  // Every session rehydrateSessions() started is still holding a lease-renew
  // timer, an outbound worker (BLMOVE loop) and an open FakeWa socket - all
  // of that must be torn down explicitly, or the real redis-durable
  // connection (and the process itself) never goes idle and `node --test`
  // hangs waiting for this file's process to exit on its own.
  sessionsMod.setRedisClient(realRedis); // restore the unwrapped client for cleanup
  await sessionsMod.releaseAllOwnedLeases();
  sessionsMod.setRedisClient(null);
  await closeRedisClient(realRedis);
  await fs.rm(tmpDir, { recursive: true, force: true });
});

test(`rehydrateSessions: booting ${N} registered sessions never opens more than REHYDRATE_CONCURRENCY=${CONCURRENCY} at once`, async () => {
  const counters = { active: 0, max: 0 };
  sessionsMod.setRedisClient(withConcurrencyProbe(realRedis, 60, counters));

  await sessionsMod.rehydrateSessions();

  assert.ok(counters.max >= 1, 'sanity: the probe must have observed at least one lease-acquisition attempt');
  assert.ok(
    counters.max <= CONCURRENCY,
    `observed ${counters.max} concurrent lease-acquisition attempts, must be <= ${CONCURRENCY}`,
  );
});
