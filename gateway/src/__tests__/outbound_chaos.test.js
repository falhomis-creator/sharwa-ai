// P0.4 real chaos acceptance test (spec §"قبول P0.4"):
//   "50 رسالة في الطابور ⇒ kill -9 للبوابة في المنتصف ⇒ إعادة تشغيل ⇒
//    الـ50 كلها وصلت لـFakeWa بلا فقد؛ عدد المكرَّرات مُقاس ومطبوع"
//
// Real child processes, real SIGKILL, real local Redis (no mocks — H7).
// FakeWa's own in-memory record dies with each killed process by design, so
// every actual sendMessage() call is durably recorded to Redis first (see
// _outbound_chaos_child.mjs) as the external proof of "reached FakeWa".
process.env.ALLOW_FAKE_WA = '1';
process.env.NODE_ENV = 'test';

import test, { before } from 'node:test';
import assert from 'node:assert/strict';
import { createRedisClient, closeRedisClient } from '../redis.js';
import { fork } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import crypto from 'node:crypto';
import { enqueueSend, queueDepth, _keys } from '../outbound/queue.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// Real redis-durable (never a mock - H7), via the project's own connection
// helper (env-configured: REDIS_DURABLE_HOST/PORT) - the child process below
// inherits the same env and connects the same way. createRedisClient() uses
// lazyConnect:false + enableOfflineQueue:false, so the first command must
// wait for 'ready' (same ordering fix as forwarder.js / real_redis_integration.test.js).
const redis = createRedisClient();
function waitForReady(client) {
  if (client.status === 'ready') return Promise.resolve();
  return new Promise((resolve, reject) => {
    const onReady = () => { client.off('error', onError); resolve(); };
    const onError = (err) => { client.off('ready', onReady); reject(err); };
    client.once('ready', onReady);
    client.once('error', onError);
  });
}
before(async () => { await waitForReady(redis); });
test.after(async () => { await closeRedisClient(redis); });

function spawnChild(sessionId) {
  const child = fork(path.join(__dirname, '_outbound_chaos_child.mjs'), [sessionId], {
    stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
    env: {
      ...process.env,
      // Test-only pacing override: the P0.4 default pacing (800-1500ms) is a
      // production anti-spam/anti-ban measure, not something this test is
      // exercising — what's under test here is crash-recovery correctness,
      // not real-time pacing. Faster pacing keeps this chaos test's wall
      // time reasonable without touching the production default anywhere.
      PACE_INTERACTIVE_MIN_MS: '30',
      PACE_INTERACTIVE_MAX_MS: '60',
    },
  });
  const lines = [];
  child.stdout.on('data', (buf) => { lines.push(...buf.toString().split('\n').filter(Boolean)); });
  return { child, lines };
}

async function waitFor(predicate, timeoutMs, intervalMs = 30) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await predicate()) return true;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  return false;
}

test('(P0.4 chaos) 50 queued messages survive 5x kill -9 + restart with zero loss; duplicates measured and bounded by kill count', async () => {
  const sessionId = `chaos-${crypto.randomUUID()}`;
  // Unique destination per test run (not a fixed literal): the token bucket
  // (outbound/tokenBucket.js) is real and keyed by `to` number in real Redis,
  // so reusing the same literal number across repeated runs of this test
  // would carry over bucket depletion between otherwise-independent runs and
  // make the test's own timing flaky for reasons that have nothing to do
  // with the crash-recovery logic actually under test here.
  const to = `chaos-dest-${crypto.randomUUID()}@s.whatsapp.net`;
  const N = 50;
  const KILLS = 5;

  for (let i = 0; i < N; i++) {
    const res = await enqueueSend(redis, {
      sessionId, clientMsgId: `chaos-msg-${i}`, to, text: `message ${i}`, kind: 'interactive',
    });
    assert.equal(res.status, 'queued');
  }
  const initialDepth = await queueDepth(redis, sessionId);
  assert.equal(initialDepth, N);

  let killsPerformed = 0;
  let { child, lines } = spawnChild(sessionId);
  // Bug fixed while writing this test (H8): a ChildProcess's 'exit' event
  // fires exactly once — registering `.once('exit', ...)` AFTER a process has
  // already exited never fires (Node does not replay past events), which
  // made an early "already drained, don't respawn" break() followed by an
  // unconditional final kill+wait hang forever. `childAlive` tracks this
  // explicitly so the final cleanup only kills+waits on a process that is
  // actually still running.
  let childAlive = true;

  for (let round = 0; round < KILLS; round++) {
    // Let it make real progress (at least a couple of confirmed sends) before killing.
    const madeProgress = await waitFor(async () => {
      const confirmed = await redis.llen(`chaos:confirmed:${sessionId}`);
      return confirmed >= 2;
    }, 5000);
    assert.ok(madeProgress, `round ${round}: child should have sent at least 2 messages before being killed`);

    child.kill('SIGKILL');
    await new Promise((resolve) => child.once('exit', resolve));
    childAlive = false;
    killsPerformed += 1;

    // Check whether everything already drained (queue+inflight empty) — if
    // so, stop killing early rather than restarting a child with nothing left to do.
    const depth = await queueDepth(redis, sessionId);
    const inflight = await redis.llen(_keys.inflightKey(sessionId));
    if (depth === 0 && inflight === 0) break;

    ({ child, lines } = spawnChild(sessionId));
    childAlive = true;
  }

  // Final run: let it fully drain (recovery requeues any unconfirmed inflight
  // item from the last kill, then the worker finishes the rest).
  const drained = await waitFor(async () => {
    const depth = await queueDepth(redis, sessionId);
    const inflight = await redis.llen(_keys.inflightKey(sessionId));
    return depth === 0 && inflight === 0;
  }, 30000);
  assert.ok(drained, 'queue and inflight must both fully drain');

  if (childAlive) {
    child.kill('SIGTERM');
    await new Promise((resolve) => child.once('exit', resolve));
  }

  // --- verification -------------------------------------------------------
  let confirmedSentAtLeastOnce = 0;
  for (let i = 0; i < N; i++) {
    const marker = await redis.get(_keys.sentMarkerKey(sessionId, `chaos-msg-${i}`));
    if (marker) confirmedSentAtLeastOnce += 1;
  }
  const totalConfirmedSends = await redis.llen(`chaos:confirmed:${sessionId}`);
  const duplicates = totalConfirmedSends - N;

  console.log(`[P0.4 chaos] kills performed=${killsPerformed}, messages with a confirmed send=${confirmedSentAtLeastOnce}/${N}, total sendMessage() calls=${totalConfirmedSends}, duplicates=${duplicates}`);

  assert.equal(confirmedSentAtLeastOnce, N, `ALL ${N} messages must have reached FakeWa at least once (zero loss) — got ${confirmedSentAtLeastOnce}`);
  assert.ok(duplicates >= 0, 'duplicate count must not be negative (sanity)');
  assert.ok(duplicates <= killsPerformed, `duplicates (${duplicates}) must not exceed the number of kills performed (${killsPerformed})`);
});
