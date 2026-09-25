import test, { before } from 'node:test';
import assert from 'node:assert/strict';
import { createRedisClient, closeRedisClient } from '../redis.js';
import { tryConsumeToken, tryConsumeMarketingDailyCap } from '../outbound/tokenBucket.js';

// Real redis-durable (never a mock - H7), via the project's own connection
// helper (env-configured: REDIS_DURABLE_HOST/PORT), matching how every other
// real-Redis suite in this project connects (see real_redis_integration.test.js).
//
// createRedisClient() uses lazyConnect:false + enableOfflineQueue:false (H3:
// fail fast rather than silently buffer). A command issued before the
// TCP+AUTH handshake completes fails immediately with "Stream isn't
// writeable..." - the exact ordering bug forwarder.js's own waitForReady
// exists to avoid (see forwarder.js's header comment) - so this suite waits
// for 'ready' in `before()` too, same as real_redis_integration.test.js.
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

test('tryConsumeToken: allows up to capacity, then rejects until refill', async () => {
  const number = `t1-${Date.now()}`;
  let now = 1000000;
  for (let i = 0; i < 5; i++) {
    const res = await tryConsumeToken(redis, { number, kind: 'marketing', capacity: 5, refillPerMin: 60, now });
    assert.equal(res.allowed, true, `token ${i} should be allowed`);
  }
  const sixth = await tryConsumeToken(redis, { number, kind: 'marketing', capacity: 5, refillPerMin: 60, now });
  assert.equal(sixth.allowed, false, 'bucket should be empty after capacity consumed');

  // advance clock by 30s -> refillPerMin=60 => 30 tokens/30s... wait refill=60/min=1/sec, 30s=30 tokens (capped at capacity 5)
  now += 30000;
  const afterRefill = await tryConsumeToken(redis, { number, kind: 'marketing', capacity: 5, refillPerMin: 60, now });
  assert.equal(afterRefill.allowed, true, 'should refill and allow after enough elapsed time');
});

test('tryConsumeToken: real wall-clock timing respects refill rate (20/min marketing default)', async () => {
  const number = `t2-${Date.now()}`;
  // capacity 1, refill 20/min => refill of 1 token takes 3000ms
  const first = await tryConsumeToken(redis, { number, kind: 'marketing', capacity: 1, refillPerMin: 20 });
  assert.equal(first.allowed, true);
  const second = await tryConsumeToken(redis, { number, kind: 'marketing', capacity: 1, refillPerMin: 20 });
  assert.equal(second.allowed, false, 'immediate second request must be rejected (real timing)');
  await new Promise((r) => setTimeout(r, 3100));
  const third = await tryConsumeToken(redis, { number, kind: 'marketing', capacity: 1, refillPerMin: 20 });
  assert.equal(third.allowed, true, 'after >3s (1 token at 20/min) the bucket should have refilled');
});

test('tryConsumeMarketingDailyCap: rejects past the cap, does not corrupt the counter', async () => {
  const number = `t3-${Date.now()}`;
  for (let i = 0; i < 3; i++) {
    const res = await tryConsumeMarketingDailyCap(redis, { number, dailyCap: 3 });
    assert.equal(res.allowed, true);
  }
  const rejected = await tryConsumeMarketingDailyCap(redis, { number, dailyCap: 3 });
  assert.equal(rejected.allowed, false);
  // a further rejection should not keep incrementing forever (H4 - bounded counter)
  const rejected2 = await tryConsumeMarketingDailyCap(redis, { number, dailyCap: 3 });
  assert.equal(rejected2.allowed, false);
  assert.equal(rejected2.countToday, 3);
});

test('concurrent consumers never over-allow beyond capacity (atomicity)', async () => {
  const number = `t4-${Date.now()}`;
  const now = Date.now();
  const results = await Promise.all(
    Array.from({ length: 50 }, () => tryConsumeToken(redis, { number, kind: 'service', capacity: 10, refillPerMin: 0, now })),
  );
  const allowedCount = results.filter((r) => r.allowed).length;
  assert.equal(allowedCount, 10, `exactly capacity (10) of 50 concurrent requests should be allowed, got ${allowedCount}`);
});
