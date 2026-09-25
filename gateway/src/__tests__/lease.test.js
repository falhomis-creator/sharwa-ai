import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';

import { acquireLease, renewLease, releaseLease, isLeaseOwner, getLeaseHolder } from '../lease.js';
import { createRedisClient, closeRedisClient } from '../redis.js';

// Same ordering fix as every other real-Redis suite in this project
// (createRedisClient() uses lazyConnect:false + enableOfflineQueue:false).
function waitForReady(client) {
  if (client.status === 'ready') return Promise.resolve();
  return new Promise((resolve, reject) => {
    const onReady = () => { client.off('error', onError); resolve(); };
    const onError = (err) => { client.off('ready', onReady); reject(err); };
    client.once('ready', onReady);
    client.once('error', onError);
  });
}

const redis = createRedisClient();
before(async () => { await waitForReady(redis); });
after(async () => { await closeRedisClient(redis); });

test('acquireLease: first caller wins, second caller (different instance) is refused', async () => {
  const sid = `lease-race-${crypto.randomUUID()}`;
  const a = await acquireLease(redis, sid, 'instance-a', 30000);
  assert.equal(a.acquired, true);
  assert.equal(typeof a.fencingToken, 'number');

  const b = await acquireLease(redis, sid, 'instance-b', 30000);
  assert.equal(b.acquired, false, 'a live, unexpired lease must refuse a second holder');
});

test('renewLease: succeeds for the real owner, fails (and does not extend) for a non-owner', async () => {
  const sid = `lease-renew-${crypto.randomUUID()}`;
  const { acquired, fencingToken } = await acquireLease(redis, sid, 'instance-a', 30000);
  assert.equal(acquired, true);

  const ok = await renewLease(redis, sid, 'instance-a', fencingToken, 30000);
  assert.equal(ok, true);

  // A stale/foreign fencing token (as if a delayed renew from a PRIOR holder
  // arrived after someone else re-acquired the lease) must be rejected.
  const stale = await renewLease(redis, sid, 'instance-a', fencingToken - 1, 30000);
  assert.equal(stale, false);

  const wrongInstance = await renewLease(redis, sid, 'instance-b', fencingToken, 30000);
  assert.equal(wrongInstance, false);
});

test('releaseLease: only the real owner can release; a stale releaser is refused and the lease survives', async () => {
  const sid = `lease-release-${crypto.randomUUID()}`;
  const { fencingToken } = await acquireLease(redis, sid, 'instance-a', 30000);

  const refused = await releaseLease(redis, sid, 'instance-a', fencingToken - 1);
  assert.equal(refused, false);
  assert.equal(await getLeaseHolder(redis, sid), `instance-a:${fencingToken}`, 'a refused release must not touch the real lease');

  const released = await releaseLease(redis, sid, 'instance-a', fencingToken);
  assert.equal(released, true);
  assert.equal(await getLeaseHolder(redis, sid), null);

  // Immediately re-acquirable by someone else once released - this is the
  // whole point of a graceful release (fast failover, no TTL wait).
  const b = await acquireLease(redis, sid, 'instance-b', 30000);
  assert.equal(b.acquired, true);
});

test('a released/expired lease can be re-acquired with a strictly greater fencing token (monotonic, never reused)', async () => {
  const sid = `lease-fencing-monotonic-${crypto.randomUUID()}`;
  const first = await acquireLease(redis, sid, 'instance-a', 30000);
  await releaseLease(redis, sid, 'instance-a', first.fencingToken);
  const second = await acquireLease(redis, sid, 'instance-b', 30000);
  assert.ok(second.fencingToken > first.fencingToken, 'fencing token must never go backwards or repeat');
});

test('isLeaseOwner: true only for the exact current holder', async () => {
  const sid = `lease-owner-${crypto.randomUUID()}`;
  const { fencingToken } = await acquireLease(redis, sid, 'instance-a', 30000);
  assert.equal(await isLeaseOwner(redis, sid, 'instance-a', fencingToken), true);
  assert.equal(await isLeaseOwner(redis, sid, 'instance-a', fencingToken + 1), false);
  assert.equal(await isLeaseOwner(redis, sid, 'instance-b', fencingToken), false);
});

test('acquireLease: an EXPIRED lease (short PX) can be acquired by a different instance', async () => {
  const sid = `lease-expiry-${crypto.randomUUID()}`;
  const a = await acquireLease(redis, sid, 'instance-a', 50); // 50ms TTL
  assert.equal(a.acquired, true);
  await new Promise((resolve) => setTimeout(resolve, 150));
  const b = await acquireLease(redis, sid, 'instance-b', 30000);
  assert.equal(b.acquired, true, 'an expired lease must be freely acquirable (this is the failover mechanism itself)');
});
