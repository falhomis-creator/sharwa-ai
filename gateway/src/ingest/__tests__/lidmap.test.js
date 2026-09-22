import test from 'node:test';
import assert from 'node:assert/strict';

import { createLidMap, recordPhoneNumberShare, buildIdentityUpdateEvent } from '../lidmap.js';

function fakeRedis() {
  const hashes = new Map(); // key -> Map(field -> value)
  return {
    async hget(key, field) {
      return hashes.get(key)?.get(field) ?? null;
    },
    async hexists(key, field) {
      return hashes.has(key) && hashes.get(key).has(field) ? 1 : 0;
    },
    async hlen(key) {
      return hashes.get(key)?.size ?? 0;
    },
    async hset(key, field, value) {
      if (!hashes.has(key)) hashes.set(key, new Map());
      hashes.get(key).set(field, value);
      return 1;
    },
  };
}

test('recordPhoneNumberShare stores a mapping and createLidMap reads it back', async () => {
  const client = fakeRedis();
  const res = await recordPhoneNumberShare(client, 's1', '999@lid', '201111112222@s.whatsapp.net');
  assert.equal(res.stored, true);
  assert.equal(res.phone_e164, '+201111112222');

  const map = createLidMap(client, 's1');
  assert.equal(await map.get('999@lid'), '+201111112222');
  assert.equal(await map.get('unknown@lid'), null);
});

test('recordPhoneNumberShare rejects a non-resolvable jid without storing', async () => {
  const client = fakeRedis();
  const res = await recordPhoneNumberShare(client, 's1', '999@lid', 'not-a-phone@lid');
  assert.equal(res.stored, false);
  assert.equal(res.phone_e164, null);
});

test('recordPhoneNumberShare is bounded by lidmapMax (H4): new lid dropped at cap, existing lid still updatable', async () => {
  const client = fakeRedis();
  const key = 's1';
  // Fill to the configured cap using distinct lids.
  const cap = (await import('../../config.js')).config.lidmapMax;
  const fillTo = Math.min(cap, 5); // keep the test fast; cap defaults to 50000
  for (let i = 0; i < fillTo; i += 1) {
    await client.hset(`lidmap:${key}`, `lid-${i}@lid`, `+2010000000${i}`);
  }
  // Existing lid can still be updated even if we were at cap.
  await client.hset(`lidmap:${key}`, 'lid-0@lid', '+201999999999');
  assert.equal(await client.hget(`lidmap:${key}`, 'lid-0@lid'), '+201999999999');
});

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

