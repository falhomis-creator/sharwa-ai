import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

import { spool, drainSpool } from '../ingest/spool.js';

const REC = {
  sessionId: 'session-a',
  event: { provider_message_id: 'ABC123', type: 'text', ts: 1700000000000, text: 'hi' },
};

async function tmpdir() {
  return mkdtemp(path.join(os.tmpdir(), 'spool-test-'));
}

function evalClient(returnValue) {
  return { eval: async () => returnValue };
}

test('spool then drain replays the record and clears the file', async () => {
  const dir = await tmpdir();
  await spool(REC, { spoolDir: dir });
  const client = evalClient([1, '5-0']);
  const res = await drainSpool(client, { spoolDir: dir });
  assert.deepEqual(res, { replayed: 1, remaining: 0, dropped: 0 });
  assert.equal(await readFile(path.join(dir, 'spool.ndjson'), 'utf8'), '');
  await rm(dir, { recursive: true, force: true });
});

test('drain keeps pending entries when redis is down', async () => {
  const dir = await tmpdir();
  await spool(REC, { spoolDir: dir });
  const client = evalClient([2, 'redis down']);
  const res = await drainSpool(client, { spoolDir: dir });
  assert.deepEqual(res, { replayed: 0, remaining: 1, dropped: 0 });
  const content = await readFile(path.join(dir, 'spool.ndjson'), 'utf8');
  assert.ok(content.includes('"ABC123"'), 'record still spooled for a later retry');
  await rm(dir, { recursive: true, force: true });
});

test('drain drops a corrupt line and a permanently-too-large line', async () => {
  const dir = await tmpdir();
  const file = path.join(dir, 'spool.ndjson');
  const { writeFile, mkdir } = await import('node:fs/promises');
  await mkdir(dir, { recursive: true });
  // Two records: one corrupt, one valid-but-permanently-too-large (client still up).
  await writeFile(file, 'not-json\n{"session_id":"session-a","event":{"provider_message_id":"BIG"}}\n', 'utf8');
  const client = evalClient([2, 'WRONGTYPE']);
  // The too-large path is triggered by appendEvent size check; here we make the
  // second record actually too large so PAYLOAD_TOO_LARGE fires.
  await writeFile(file, 'not-json\n' + JSON.stringify({ session_id: 'session-a', event: { provider_message_id: 'BIG', type: 'text', ts: 1, text: 'x'.repeat(70000) } }) + '\n', 'utf8');
  const res = await drainSpool(client, { spoolDir: dir });
  assert.deepEqual(res, { replayed: 0, remaining: 0, dropped: 2 });
  await rm(dir, { recursive: true, force: true });
});

test('spool refuses to grow past the cap (H4)', async () => {
  const dir = await tmpdir();
  await assert.rejects(
    () => spool(REC, { spoolDir: dir, spoolMaxMb: 0 }),
    (err) => err.code === 'SPOOL_FULL',
  );
  await rm(dir, { recursive: true, force: true });
});
