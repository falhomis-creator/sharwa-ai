import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

import { createRedisClient, closeRedisClient } from '../redis.js';

// P0.7 (spec, literal - added at the user's explicit request once the real
// gap was found: core/'s POST /v1/channels/{id}/reconnect calls
// GatewayClient.session_reconnect(), which called a gateway route that never
// existed). Real FakeWa driver (H7), real redis-durable (lease acquisition),
// isolated tmp AUTH_SESSIONS_DIR (own file = own node --test worker process).

process.env.WA_DRIVER = 'fake';
process.env.NODE_ENV = 'test';
process.env.ALLOW_FAKE_WA = '1';

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

before(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'p07-reconnect-'));
  process.env.AUTH_SESSIONS_DIR = tmpDir;

  sessionsMod = await import('../sessions.js');
  realRedis = createRedisClient();
  await waitForReady(realRedis);
  sessionsMod.setRedisClient(realRedis);
});

after(async () => {
  await sessionsMod.releaseAllOwnedLeases();
  sessionsMod.setRedisClient(null);
  await closeRedisClient(realRedis);
  await fs.rm(tmpDir, { recursive: true, force: true });
});

test('reconnectSession is a no-op (already_active) when the session already has a live socket', async () => {
  const sessionId = 'p07-reconnect-already-active';
  await sessionsMod.createSession(sessionId);
  const before1 = sessionsMod.getSession(sessionId);
  assert.ok(before1.sock, 'sanity: createSession must have opened a real (fake) socket');

  const result = await sessionsMod.reconnectSession(sessionId);
  assert.deepEqual(result, { reconnected: false, reason: 'already_active' });
  assert.equal(sessionsMod.getSession(sessionId).sock, before1.sock, 'must not replace an already-live socket');
});

test('reconnectSession cancels a pending backoff timer and opens a fresh socket immediately (CONFLICT/DISCONNECTED case)', async () => {
  const sessionId = 'p07-reconnect-disconnected';
  await sessionsMod.createSession(sessionId);
  const session = sessionsMod.getSession(sessionId);

  // Simulate P0.5's own disconnected-with-pending-backoff-timer state: close
  // the socket and arm a long-lived timer the way scheduleReconnect() would
  // (see sessions.js's own comment: scheduleReconnect's callback calls
  // openSocketForSession() UNCONDITIONALLY, so a stale timer left armed after
  // a manual reconnect already opened a new socket would open a SECOND one).
  session.sock = null;
  let staleTimerFired = false;
  session.reconnectTimer = setTimeout(() => { staleTimerFired = true; }, 60_000);
  session.reconnectTimer.unref?.();

  const result = await sessionsMod.reconnectSession(sessionId);
  assert.deepEqual(result, { reconnected: true });

  const after1 = sessionsMod.getSession(sessionId);
  assert.ok(after1.sock, 'reconnectSession must open a fresh socket immediately, not wait for the backoff timer');
  assert.equal(after1.reconnectTimer, null, 'the stale backoff timer must be cancelled, never left armed');
  assert.equal(staleTimerFired, false);
});

test('reconnectSession starts a fresh session (new QR) when nothing is tracked in-memory (LOGGED_OUT already wiped it)', async () => {
  const sessionId = 'p07-reconnect-untracked';
  assert.equal(sessionsMod.getSession(sessionId), null, 'sanity: nothing tracked yet');

  const result = await sessionsMod.reconnectSession(sessionId);
  assert.deepEqual(result, { reconnected: true });

  const session = sessionsMod.getSession(sessionId);
  assert.ok(session, 'reconnectSession must have started tracking this session');
  assert.ok(session.sock, 'must have opened a real (fake) socket, producing a fresh QR just like a brand-new channel');
});
