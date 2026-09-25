process.env.ALLOW_FAKE_WA = '1';
process.env.NODE_ENV = 'test';

import test, { before } from 'node:test';
import assert from 'node:assert/strict';
import { createRedisClient, closeRedisClient } from '../redis.js';
import crypto from 'node:crypto';
import { enqueueSend, startOutboundWorker, recoverInflight, processOne, queueDepth, _keys } from '../outbound/queue.js';
import { makeFakeWaSocket } from '../../test-support/fakeWaDriver.js';

// Real redis-durable (never a mock - H7), via the project's own connection
// helper (env-configured: REDIS_DURABLE_HOST/PORT). createRedisClient() uses
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

function sid() { return `s-${crypto.randomUUID()}`; }
// Unique destination per call, never a shared literal: the token bucket is
// real and keyed by `to` in real Redis, so a fixed literal number reused
// across many repeated runs of this file (as happens while iterating on it)
// would carry stale bucket depletion between otherwise-independent runs.
function dest() { return `${crypto.randomUUID()}@s.whatsapp.net`; }

test('enqueueSend + worker: a single message reaches the FakeWa socket', async () => {
  const sessionId = sid();
  const sock = makeFakeWaSocket({ sendLatencyMs: 1 });
  const ctx = { getSocket: () => sock };
  const worker = startOutboundWorker(redis, sessionId, ctx);

  const res = await enqueueSend(redis, { sessionId, clientMsgId: 'cm-1', to: dest(), text: 'hello' });
  assert.equal(res.status, 'queued');

  await new Promise((resolve) => {
    const iv = setInterval(() => {
      if (sock._fake.sentMessages.length >= 1) { clearInterval(iv); resolve(); }
    }, 20);
  });
  worker.stop();
  await worker.done;

  assert.equal(sock._fake.sentMessages.length, 1);
  assert.equal(sock._fake.sentMessages[0].content.text, 'hello');
  const marker = await redis.get(_keys.sentMarkerKey(sessionId, 'cm-1'));
  assert.ok(marker, 'sent_marker should be written after a successful send');
});

test('enqueueSend: 1000 requests with the SAME client_msg_id -> exactly one queued, rest duplicate', async () => {
  const sessionId = sid();
  const clientMsgId = 'dup-1';
  const results = await Promise.all(
    Array.from({ length: 1000 }, () => enqueueSend(redis, { sessionId, clientMsgId, to: dest(), text: 'x' })),
  );
  const queued = results.filter((r) => r.status === 'queued').length;
  const duplicate = results.filter((r) => r.status === 'duplicate').length;
  assert.equal(queued, 1, `exactly one of 1000 concurrent identical client_msg_id requests should be queued, got ${queued}`);
  assert.equal(duplicate, 999);

  // and only one item actually made it into the queue list
  const depth = await queueDepth(redis, sessionId);
  assert.equal(depth, 1);
});

test('full queue -> status "full", never silently drops or over-fills', async () => {
  const sessionId = sid();
  // Fill to config.outbound.queueMax (500) using distinct client_msg_ids, no worker draining.
  const { config } = await import('../config.js');
  for (let i = 0; i < config.outbound.queueMax; i++) {
    const res = await enqueueSend(redis, { sessionId, clientMsgId: `f-${i}`, to: dest(), text: 'x' });
    assert.equal(res.status, 'queued');
  }
  const overflow = await enqueueSend(redis, { sessionId, clientMsgId: 'overflow', to: dest(), text: 'x' });
  assert.equal(overflow.status, 'full');
  const depth = await queueDepth(redis, sessionId);
  assert.equal(depth, config.outbound.queueMax, 'queue must not grow past its cap (H4)');
});

test('processOne: session_down (no socket) requeues the item rather than dropping it', async () => {
  const sessionId = sid();
  await enqueueSend(redis, { sessionId, clientMsgId: 'down-1', to: dest(), text: 'x' });
  const raw = await redis.blmove(_keys.queueKey(sessionId), _keys.inflightKey(sessionId), 'LEFT', 'RIGHT', 1);
  assert.ok(raw);
  const result = await processOne(redis, sessionId, raw, { getSocket: () => null });
  assert.equal(result.outcome, 'requeued');
  const depth = await queueDepth(redis, sessionId);
  assert.equal(depth, 1, 'item must be back on the queue, not lost');
  const inflightLen = await redis.llen(_keys.inflightKey(sessionId));
  assert.equal(inflightLen, 0);
});

test('recoverInflight: unconfirmed inflight item is requeued; confirmed (has sent_marker) is just cleaned up', async () => {
  const sessionId = sid();
  const itemA = JSON.stringify({ client_msg_id: 'rec-unconfirmed', to: 'x', text: 'x', kind: 'interactive', enqueued_at: Date.now() });
  const itemB = JSON.stringify({ client_msg_id: 'rec-confirmed', to: 'x', text: 'x', kind: 'interactive', enqueued_at: Date.now() });
  await redis.rpush(_keys.inflightKey(sessionId), itemA, itemB);
  await redis.set(_keys.sentMarkerKey(sessionId, 'rec-confirmed'), 'wa-123', 'EX', 60);

  const res = await recoverInflight(redis, sessionId);
  assert.equal(res.confirmedSent, 1);
  assert.equal(res.requeued, 1);

  const inflightLen = await redis.llen(_keys.inflightKey(sessionId));
  assert.equal(inflightLen, 0, 'inflight must be fully drained after recovery');
  const depth = await queueDepth(redis, sessionId);
  assert.equal(depth, 1, 'only the unconfirmed item should be back on the queue');
  const requeuedRaw = await redis.lrange(_keys.queueKey(sessionId), 0, -1);
  assert.equal(JSON.parse(requeuedRaw[0]).client_msg_id, 'rec-unconfirmed');
});

test('expired item (past its kind TTL) is failed(expired), not sent, not stuck', async () => {
  const sessionId = sid();
  const oldItem = JSON.stringify({
    client_msg_id: 'exp-1', to: 'x', text: 'x', kind: 'interactive',
    enqueued_at: Date.now() - (11 * 60 * 1000), // 11 min ago > 10 min interactive TTL
  });
  await redis.rpush(_keys.inflightKey(sessionId), oldItem);
  const result = await processOne(redis, sessionId, oldItem, { getSocket: () => { throw new Error('must not be called'); } });
  assert.equal(result.outcome, 'expired');
  const inflightLen = await redis.llen(_keys.inflightKey(sessionId));
  assert.equal(inflightLen, 0);
});
