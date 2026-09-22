import test from 'node:test';
import assert from 'node:assert/strict';

import { FUSED_APPEND_LUA } from '../ingest/dedupe.js';
import { shardFor, streamKeyFor, dedupeKeyFor, appendEvent } from '../ingest/wal.js';

const SESSION = 'session-a';
const EVENT = {
  provider_message_id: 'ABC123',
  type: 'text',
  ts: 1700000000000,
  identity: { jid_raw: '201234567890@s.whatsapp.net', addressing: 'pn', wa_id: '201234567890', phone_e164: '+201234567890' },
  text: 'أين طلبي؟',
};

function evalClient(returnValue) {
  const calls = [];
  return {
    calls,
    eval: async (...a) => { calls.push(a); return returnValue; },
  };
}

test('shardFor is deterministic and in range', () => {
  const s1 = shardFor(SESSION, 4);
  const s2 = shardFor(SESSION, 4);
  assert.equal(s1, s2);
  assert.ok(s1 >= 0 && s1 < 4);
});

test('streamKeyFor / dedupeKeyFor formats', () => {
  assert.match(streamKeyFor(SESSION), /^in:\d+$/);
  assert.equal(dedupeKeyFor(SESSION, 'ABC123'), 'dedupe:session-a:ABC123');
});

test('appendEvent serializes v1 entry and routes via fused dedupe+XADD', async () => {
  const client = evalClient([1, '999-0']);
  const out = await appendEvent(client, { sessionId: SESSION, event: EVENT });
  assert.deepEqual(out, { status: 'appended', id: '999-0' });

  const [script, numKeys, k1, k2, argId, argField, argValue, argTtl, argMaxlen] = client.calls[0];
  assert.equal(script, FUSED_APPEND_LUA);
  assert.equal(numKeys, 2);
  assert.equal(k1, 'dedupe:session-a:ABC123');
  assert.equal(k2, `in:${shardFor(SESSION)}`);
  assert.equal(argId, '*');
  assert.equal(argField, 'data');
  assert.equal(argTtl, '60000'); // dedupePendingTtlS=60 -> 60000ms
  assert.equal(argMaxlen, '100000');

  const entry = JSON.parse(argValue);
  assert.equal(entry.v, 1);
  assert.equal(entry.session_id, SESSION);
  assert.equal(entry.provider_message_id, 'ABC123');
  assert.equal(entry.text, 'أين طلبي؟');
});

test('appendEvent surfaces a duplicate as success (nothing written)', async () => {
  const client = evalClient([0]);
  const out = await appendEvent(client, { sessionId: SESSION, event: EVENT });
  assert.deepEqual(out, { status: 'duplicate' });
});

test('appendEvent propagates XADD failure as DEDUPE_XADD_FAILED (spool+retry)', async () => {
  const client = evalClient([2, 'WRONGTYPE Operation against a key holding the wrong kind of value']);
  await assert.rejects(
    () => appendEvent(client, { sessionId: SESSION, event: EVENT }),
    (err) => err.code === 'DEDUPE_XADD_FAILED',
  );
});

test('appendEvent hard-rejects an over-cap value as PAYLOAD_TOO_LARGE (F4)', async () => {
  const client = evalClient([1, 'x']);
  const big = { ...EVENT, text: 'x'.repeat(70000) }; // 70KB > 64KB cap
  await assert.rejects(
    () => appendEvent(client, { sessionId: SESSION, event: big }),
    (err) => err.code === 'PAYLOAD_TOO_LARGE',
  );
  assert.equal(client.calls.length, 0, 'must reject before touching Redis');
});

test('appendEvent falls back to a synthetic id when provider_message_id is null', async () => {
  const client = evalClient([1, '1-1']);
  const noId = { ...EVENT, provider_message_id: null };
  await appendEvent(client, { sessionId: SESSION, event: noId });
  const k1 = client.calls[0][2];
  assert.match(k1, /^dedupe:session-a:ts:\d+:[0-9a-f-]{36}$/);
});
