process.env.ALLOW_FAKE_WA = '1';
process.env.NODE_ENV = 'test';

import test, { before, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';

import { createRedisClient, closeRedisClient } from '../redis.js';
import { config } from '../config.js';
import { createKillSwitch } from '../killswitch.js';
import { killswitchStateGauge } from '../metrics.js';
import { enqueueSend, startOutboundWorker, processOne, recoverInflight, _keys } from '../outbound/queue.js';
import { makeFakeWaSocket } from '../../test-support/fakeWaDriver.js';

// Real redis-cache (never a mock - H7), via the same connection helper and
// env-configured host/port convention already used for redis-durable
// throughout this project (REDIS_CACHE_HOST/PORT - see config.js). In this
// sandbox/CI setup redis-cache and redis-durable are the SAME physical
// redis-server instance (no collision risk: `ks:*` keys are a disjoint
// namespace from `out:*`/`in:*`/etc) - on the real VPS they are the actual
// separate `redis-cache`/`redis-durable` compose services.
const admin = createRedisClient(config.redisCache);

function waitForReady(client) {
  if (client.status === 'ready') return Promise.resolve();
  return new Promise((resolve, reject) => {
    const onReady = () => { client.off('error', onError); resolve(); };
    const onError = (err) => { client.off('ready', onReady); reject(err); };
    client.once('ready', onReady);
    client.once('error', onError);
  });
}

before(async () => { await waitForReady(admin); });
test.after(async () => { await closeRedisClient(admin); });

function uid(prefix) { return `${prefix}-${crypto.randomUUID()}`; }

async function setState(scopeKey, capability, state) {
  await admin.hset(`ks:${scopeKey}`, capability, state);
}
async function clearScope(scopeKey) {
  await admin.del(`ks:${scopeKey}`);
}
async function publishChange(payload) {
  await admin.publish('ks:changes', JSON.stringify(payload));
}
async function gaugeValueFor(scopeKey, capability) {
  const snapshot = await killswitchStateGauge.get();
  const entry = snapshot.values.find(
    (v) => v.labels.scope === scopeKey && v.labels.capability === capability,
  );
  return entry ? entry.value : undefined;
}

// Every test gets its own createKillSwitch() instance (factory, not a
// singleton - see killswitch.js's own header comment on why) and its own
// randomized scope ids, so cases never interfere with each other even
// though they may run concurrently within this file.
async function freshKillSwitch(opts = {}) {
  const ks = createKillSwitch({ resyncIntervalMs: 3600000, ...opts }); // long resync interval: these tests drive state via ks:changes, not the periodic timer, except where noted
  await ks.start();
  return ks;
}

test('(P0.6B) global scope: off blocks ai_reply; on/absent allows it', async () => {
  const ks = await freshKillSwitch();
  try {
    let res = ks.checkCapability('ai_reply', {});
    assert.equal(res.allowed, true, 'no global entry -> default allow');

    await setState('global', 'ai_reply', 'off');
    await publishChange({ scope: 'global' });
    await new Promise((r) => setTimeout(r, 100)); // let the pubsub-triggered refetch land

    res = ks.checkCapability('ai_reply', {});
    assert.equal(res.allowed, false);
    assert.equal(res.state, 'off');
    assert.equal(res.scope, 'global');
  } finally {
    await clearScope('global');
    await ks.stop();
  }
});

test('(P0.6B/F17) removing a capability from the hash resets killswitch_state back to 0, not stuck at its last severity', async () => {
  const ks = await freshKillSwitch();
  const tenantId = uid('tenant');
  const scopeKey = `tenant:${tenantId}`;
  try {
    await setState(scopeKey, 'ai_reply', 'off');
    await publishChange({ scope: 'tenant', id: tenantId });
    await new Promise((r) => setTimeout(r, 100));

    assert.equal(ks.checkCapability('ai_reply', { tenantId }).allowed, false, 'sanity: the flip actually took effect');
    assert.equal(await gaugeValueFor(scopeKey, 'ai_reply'), 2, 'gauge must report severity 2 (off) while the flip is live');

    // The real bug (F17): HDEL-ing the field (an operator turning the
    // restriction back off) must not leave the Prometheus gauge stuck
    // reporting the old severity forever.
    await admin.hdel(`ks:${scopeKey}`, 'ai_reply');
    await publishChange({ scope: 'tenant', id: tenantId });
    await new Promise((r) => setTimeout(r, 100));

    assert.equal(ks.checkCapability('ai_reply', { tenantId }).allowed, true, 'enforcement correctly reverted (this half already worked before the fix)');
    assert.equal(await gaugeValueFor(scopeKey, 'ai_reply'), 0, 'the gauge must reset to 0 (on) once the capability is gone from the hash, not stay stuck at 2');
  } finally {
    await clearScope(scopeKey);
    await ks.stop();
  }
});

test('(P0.6B) strictest-wins: tenant=off overrides global=on for that tenant, leaves other tenants unaffected', async () => {
  const ks = await freshKillSwitch();
  const tenantId = uid('tenant');
  const otherTenantId = uid('tenant');
  try {
    await setState('global', 'ai_reply', 'on');
    await setState(`tenant:${tenantId}`, 'ai_reply', 'off');
    await publishChange({ scope: 'global' });
    await publishChange({ scope: 'tenant', id: tenantId });
    await new Promise((r) => setTimeout(r, 100));

    const blocked = ks.checkCapability('ai_reply', { tenantId });
    assert.equal(blocked.allowed, false);
    assert.equal(blocked.scope, `tenant:${tenantId}`);

    const allowed = ks.checkCapability('ai_reply', { tenantId: otherTenantId });
    assert.equal(allowed.allowed, true, 'a different, never-configured tenant must not inherit the first tenant\'s block');

    const globalOnly = ks.checkCapability('ai_reply', {});
    assert.equal(globalOnly.allowed, true, 'no tenant context at all -> only the (allowing) global scope applies');
  } finally {
    await clearScope('global');
    await clearScope(`tenant:${tenantId}`);
    await ks.stop();
  }
});

test('(P0.6B) channel scope + wildcard capability: channel *=degraded blocks marketing but allows ai_reply', async () => {
  const ks = await freshKillSwitch();
  const channelAccountId = uid('channel');
  try {
    await setState(`channel:${channelAccountId}`, '*', 'degraded');
    await publishChange({ scope: 'channel', id: channelAccountId });
    await new Promise((r) => setTimeout(r, 100));

    const marketing = ks.checkCapability('marketing', { channelAccountId });
    assert.equal(marketing.allowed, false, 'degraded blocks marketing (spec: degraded blocks marketing/broadcast only)');

    const reply = ks.checkCapability('ai_reply', { channelAccountId });
    assert.equal(reply.allowed, true, 'degraded still allows ordinary service replies');
  } finally {
    await clearScope(`channel:${channelAccountId}`);
    await ks.stop();
  }
});

test('(P0.6B) ks:changes accepts BOTH scope spellings: "channel" and the DB\'s own "channel_account"', async () => {
  const ks = await freshKillSwitch();
  const chA = uid('channel');
  const chB = uid('channel');
  try {
    await setState(`channel:${chA}`, 'ai_reply', 'off');
    await setState(`channel:${chB}`, 'ai_reply', 'off');
    // the spelling the Redis key uses...
    await publishChange({ scope: 'channel', id: chA });
    // ...and the spelling docs/reference/schema.sql's CHECK constraint uses,
    // which is what P0.7's publisher will most naturally send.
    await publishChange({ scope: 'channel_account', id: chB });
    await new Promise((r) => setTimeout(r, 150));

    assert.equal(ks.checkCapability('ai_reply', { channelAccountId: chA }).allowed, false, "'channel' spelling must take effect");
    assert.equal(ks.checkCapability('ai_reply', { channelAccountId: chB }).allowed, false, "'channel_account' spelling must take effect");
  } finally {
    await clearScope(`channel:${chA}`);
    await clearScope(`channel:${chB}`);
    await ks.stop();
  }
});

test('(P0.6B) checkSend: kind=marketing checks BOTH marketing and broadcast; either being off blocks the send', async () => {
  const ks = await freshKillSwitch();
  try {
    await setState('global', 'broadcast', 'off');
    await publishChange({ scope: 'global' });
    await new Promise((r) => setTimeout(r, 100));

    const res = ks.checkSend('marketing', {});
    assert.equal(res.allowed, false);
    assert.equal(res.capability, 'broadcast', 'broadcast is checked even though only marketing kind was sent - the conservative mapping (docs/P0_DEVIATIONS.md)');
  } finally {
    await clearScope('global');
    await ks.stop();
  }
});

test('(P0.6B) checkSend: kind=interactive/bulk never checks marketing/broadcast, only ai_reply', async () => {
  const ks = await freshKillSwitch();
  try {
    await setState('global', 'marketing', 'off');
    await setState('global', 'broadcast', 'off');
    await publishChange({ scope: 'global' });
    await new Promise((r) => setTimeout(r, 100));

    const res = ks.checkSend('interactive', {});
    assert.equal(res.allowed, true, 'marketing/broadcast being off must not block a plain interactive send');
  } finally {
    await clearScope('global');
    await ks.stop();
  }
});

test('(P0.6B) PUBLISH-to-block propagation is measurably under 1 second (spec acceptance criterion)', async () => {
  const ks = await freshKillSwitch();
  try {
    assert.equal(ks.checkCapability('ai_reply', {}).allowed, true);

    const start = Date.now();
    await setState('global', 'ai_reply', 'off');
    await publishChange({ scope: 'global' });

    let elapsedMs = null;
    for (let i = 0; i < 100; i += 1) {
      // eslint-disable-next-line no-await-in-loop
      await new Promise((r) => setTimeout(r, 10));
      if (!ks.checkCapability('ai_reply', {}).allowed) {
        elapsedMs = Date.now() - start;
        break;
      }
    }
    assert.ok(elapsedMs !== null, 'block never took effect within the 1s polling window');
    assert.ok(elapsedMs < 1000, `PUBLISH-to-block propagation took ${elapsedMs}ms, spec requires < 1000ms`);
  } finally {
    await clearScope('global');
    await ks.stop();
  }
});

test('(P0.6B) staleness: past KS_STALE_MAX_S, marketing/broadcast fail closed but ai_reply keeps using the last-known value', async () => {
  const ks = await freshKillSwitch({ staleMaxS: 1 }); // real 1s threshold - a real elapsed-time test, not simulated
  try {
    await setState('global', 'ai_reply', 'on');
    await publishChange({ scope: 'global' });
    await new Promise((r) => setTimeout(r, 100));
    assert.equal(ks.checkCapability('ai_reply', {}).allowed, true);
    assert.equal(ks.checkSend('marketing', {}).allowed, true, 'fresh sync -> marketing allowed (no restriction set)');

    // Simulate redis-cache going unreachable: stop the subscriber/command
    // clients' periodic resync from ever succeeding again by closing them,
    // then just wait past staleMaxS - isStale() is purely a function of
    // elapsed real time since the last successful sync, so this is a real
    // clock-driven test, not a mocked one.
    await ks.stop();
    await new Promise((r) => setTimeout(r, 1200));

    assert.equal(ks.isStale(), true);
    const marketingRes = ks.checkSend('marketing', {});
    assert.equal(marketingRes.allowed, false, 'stale + marketing -> fail closed');
    assert.equal(marketingRes.scope, 'stale-failsafe');

    const replyRes = ks.checkCapability('ai_reply', {});
    assert.equal(replyRes.allowed, true, 'stale but ai_reply keeps using its last-known cached value (on)');
  } finally {
    await clearScope('global');
  }
});

test('(P0.6B) redis-cache never reachable at all: isStale() is true from the start, marketing fails closed, ai_reply defaults to on', async () => {
  const ks = createKillSwitch({
    redisConfig: { host: '127.0.0.1', port: 1, password: '', timeoutMs: 200, maxRetries: 0 },
    staleMaxS: 1,
    resyncIntervalMs: 3600000,
  });
  try {
    await ks.start(); // must not throw (H3) even though redis-cache is unreachable
    assert.equal(ks.isStale(), true, 'never synced -> stale from the start');
    assert.equal(ks.checkSend('marketing', {}).allowed, false);
    assert.equal(ks.checkCapability('ai_reply', {}).allowed, true, 'no cached restriction -> default allow, per the documented fallback');
  } finally {
    await ks.stop();
  }
});

// --- integration with outbound/queue.js's real send pipeline -------------

const redisDurable = createRedisClient();
before(async () => { await waitForReady(redisDurable); });
test.after(async () => { await closeRedisClient(redisDurable); });

test('(P0.6B) outbound/queue.js: a blocked item becomes failed(error_class=blocked), never silently requeued forever', async () => {
  const ks = await freshKillSwitch();
  const sessionId = uid('sess');
  const sock = makeFakeWaSocket({ sendLatencyMs: 1 });
  try {
    await setState('global', 'ai_reply', 'off');
    await publishChange({ scope: 'global' });
    await new Promise((r) => setTimeout(r, 100));

    const clientMsgId = crypto.randomUUID();
    await enqueueSend(redisDurable, { sessionId, clientMsgId, to: `${uid('to')}@s.whatsapp.net`, text: 'hi', kind: 'interactive' });
    const raw = await redisDurable.blmove(_keys.queueKey(sessionId), _keys.inflightKey(sessionId), 'LEFT', 'RIGHT', 1);
    assert.ok(raw, 'item should be present in the queue');

    const ctx = {
      getSocket: () => sock,
      checkKillSwitch: (kind) => ks.checkSend(kind, {}),
    };
    const result = await processOne(redisDurable, sessionId, raw, ctx);
    assert.equal(result.outcome, 'failed');
    assert.equal(result.reason, 'blocked');

    // Confirmed NOT sent through the socket, and NOT left sitting in
    // inflight/queue forever (resolveInflight ran).
    assert.equal(sock._fake.sentMessages.length, 0);
    const inflightLen = await redisDurable.llen(_keys.inflightKey(sessionId));
    assert.equal(inflightLen, 0);
    const queueLen = await redisDurable.llen(_keys.queueKey(sessionId));
    assert.equal(queueLen, 0);
  } finally {
    await clearScope('global');
    await ks.stop();
  }
});

test('(P0.6B) outbound/queue.js: ctx without checkKillSwitch keeps the old always-allow behavior (backward compat)', async () => {
  const sessionId = uid('sess');
  const sock = makeFakeWaSocket({ sendLatencyMs: 1 });
  const clientMsgId = crypto.randomUUID();
  await enqueueSend(redisDurable, { sessionId, clientMsgId, to: `${uid('to')}@s.whatsapp.net`, text: 'hi', kind: 'interactive' });
  const raw = await redisDurable.blmove(_keys.queueKey(sessionId), _keys.inflightKey(sessionId), 'LEFT', 'RIGHT', 1);
  const result = await processOne(redisDurable, sessionId, raw, { getSocket: () => sock });
  assert.equal(result.outcome, 'sent');
  assert.equal(sock._fake.sentMessages.length, 1);
});
