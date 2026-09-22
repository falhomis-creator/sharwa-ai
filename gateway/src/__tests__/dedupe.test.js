import test from 'node:test';
import assert from 'node:assert/strict';

import { FUSED_APPEND_LUA, fusedAppend, markDone } from '../ingest/dedupe.js';

const KEYS = { dedupeKey: 'dedupe:s1:ABC', streamKey: 'in:2' };

/** Stub client that records its eval calls and returns a fixed value. */
function captureEval(returnValue) {
  const calls = [];
  return {
    calls,
    eval: async (...args) => {
      calls.push(args);
      return returnValue;
    },
    exists: async () => 1,
    psetex: async () => 'OK',
  };
}

test('fusedAppend: flag 0 -> duplicate, nothing thrown', async () => {
  const client = captureEval([0]);
  const out = await fusedAppend(client, {
    dedupeKey: KEYS.dedupeKey,
    streamKey: KEYS.streamKey,
    entry: '{}',
    pendingTtlMs: 60000,
  });
  assert.deepEqual(out, { status: 'duplicate' });
});

test('fusedAppend: flag 1 -> appended with the stream id', async () => {
  const client = captureEval([1, '1234-0']);
  const out = await fusedAppend(client, {
    dedupeKey: KEYS.dedupeKey,
    streamKey: KEYS.streamKey,
    entry: '{"x":1}',
    pendingTtlMs: 60000,
  });
  assert.deepEqual(out, { status: 'appended', id: '1234-0' });
});

test('fusedAppend: wires keys then args with defaults (MAXLEN ~ via streamMaxLen)', async () => {
  const client = captureEval([1, '9-9']);
  await fusedAppend(client, {
    dedupeKey: KEYS.dedupeKey,
    streamKey: KEYS.streamKey,
    entry: '{"x":1}',
    pendingTtlMs: 60000,
  });
  const [script, numKeys, ...rest] = client.calls[0];
  assert.equal(script, FUSED_APPEND_LUA);
  assert.equal(numKeys, 2);
  assert.deepEqual(rest, [
    KEYS.dedupeKey,        // KEYS[1]
    KEYS.streamKey,        // KEYS[2]
    '*',                   // ARGV[1] entry id default
    'data',                // ARGV[2] field
    '{"x":1}',             // ARGV[3] entry value
    '60000',               // ARGV[4] pending TTL
    '100000',              // ARGV[5] streamMaxLen default
  ]);
});

test('fusedAppend: flag 2 -> hard error (throws, code DEDUPE_XADD_FAILED)', async () => {
  const client = captureEval([2, 'WRONGTYPE Operation against a key holding the wrong kind of value']);
  await assert.rejects(
    () => fusedAppend(client, {
      dedupeKey: KEYS.dedupeKey,
      streamKey: KEYS.streamKey,
      entry: '{}',
      pendingTtlMs: 60000,
    }),
    (err) => err.code === 'DEDUPE_XADD_FAILED' && /WRONGTYPE/.test(err.message),
  );
});

test('fusedAppend: xadd-failure then retry succeeds (claim released in between)', async () => {
  const calls = [];
  let n = 0;
  const client = {
    eval: async (...args) => {
      calls.push(args);
      n += 1;
      return n === 1 ? [2, 'ERR XADD exploded'] : [1, '5678-0'];
    },
  };
  await assert.rejects(
    () => fusedAppend(client, { dedupeKey: KEYS.dedupeKey, streamKey: KEYS.streamKey, entry: '{}', pendingTtlMs: 60000 }),
    (err) => err.code === 'DEDUPE_XADD_FAILED',
  );
  const out = await fusedAppend(client, { dedupeKey: KEYS.dedupeKey, streamKey: KEYS.streamKey, entry: '{}', pendingTtlMs: 60000 });
  assert.deepEqual(out, { status: 'appended', id: '5678-0' });
});

test('Lua: claims with SET … NX first, then XADD only on success, MAXLEN ~ applied', () => {
  const setIdx = FUSED_APPEND_LUA.indexOf("redis.call('SET'");
  const xaddIdx = FUSED_APPEND_LUA.indexOf("redis.pcall('XADD'");
  assert.ok(setIdx >= 0, 'contains SET claim');
  assert.ok(xaddIdx >= 0, 'contains XADD');
  assert.ok(setIdx < xaddIdx, 'SET NX claim happens before XADD');
  assert.match(FUSED_APPEND_LUA, /'SET',\s*KEYS\[1\],\s*'pending',\s*'PX',\s*ARGV\[4\],\s*'NX'/);
  assert.match(FUSED_APPEND_LUA, /if not claimed then/);
  assert.match(FUSED_APPEND_LUA, /'XADD',\s*KEYS\[2\],\s*'MAXLEN',\s*'~',\s*ARGV\[5\]/);
  assert.match(FUSED_APPEND_LUA, /type\(id\) == 'table'/);
  assert.match(FUSED_APPEND_LUA, /redis\.call\('DEL',\s*KEYS\[1\]\)/);
});

test('markDone: extends TTL only when the marker exists', async () => {
  const done = [];
  const client = {
    exists: async () => 1,
    psetex: async (k, ttl, v) => done.push([k, ttl, v]),
  };
  await markDone(client, KEYS.dedupeKey, 172800000);
  assert.deepEqual(done, [[KEYS.dedupeKey, 172800000, 'done']]);

  const noOp = { exists: async () => 0, psetex: async () => { throw new Error('must not run'); } };
  await markDone(noOp, KEYS.dedupeKey, 172800000); // no throw, no psetex
});
