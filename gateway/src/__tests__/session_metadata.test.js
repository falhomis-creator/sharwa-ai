import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

import { createRedisClient, closeRedisClient } from '../redis.js';

// P0.7 (spec, literal - prompts/P0_DEEPSEEK_PROMPT.md "ميتاداتا الجلسة"):
// POST /sessions optionally accepts tenant_id/channel_account_id/engine,
// persisted to meta.json alongside the auth credentials. This file exercises
// createSession()/readSessionMeta()/writeSessionMeta() end to end - real
// FakeWa driver (H7's real-infra rule; the WA_DRIVER=fake seam is the only
// mock this project allows), real redis-durable, isolated tmp AUTH_SESSIONS_DIR
// (own file = own node --test worker process, same isolation rehydrate_staging
// .test.js already relies on for its own AUTH_SESSIONS_DIR/env setup).

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
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'p07-session-meta-'));
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

test('createSession persists tenant_id/channel_account_id/engine to meta.json and onto the in-memory session record', async () => {
  const sessionId = 'p07-fresh-with-meta';
  await sessionsMod.createSession(sessionId, {
    tenantId: 'tenant-abc',
    channelAccountId: 'chan-123',
    engine: 'ai_core',
  });

  const session = sessionsMod.getSession(sessionId);
  assert.ok(session);
  assert.equal(session.tenantId, 'tenant-abc');
  assert.equal(session.channelAccountId, 'chan-123');
  assert.equal(session.engine, 'ai_core');

  const metaPath = path.join(tmpDir, sessionId, 'meta.json');
  const onDisk = JSON.parse(await fs.readFile(metaPath, 'utf8'));
  assert.deepEqual(onDisk, { tenant_id: 'tenant-abc', channel_account_id: 'chan-123', engine: 'ai_core' });
});

test('createSession with no metadata never writes meta.json and leaves the session fields null (legacy/default routing)', async () => {
  const sessionId = 'p07-fresh-no-meta';
  await sessionsMod.createSession(sessionId);

  const session = sessionsMod.getSession(sessionId);
  assert.ok(session);
  assert.equal(session.tenantId, null);
  assert.equal(session.channelAccountId, null);
  assert.equal(session.engine, null);

  const metaPath = path.join(tmpDir, sessionId, 'meta.json');
  await assert.rejects(fs.readFile(metaPath, 'utf8'), /ENOENT/);
});

test('createSession called again for an already-existing session short-circuits and does not overwrite its meta.json (G11 short-circuit, unchanged)', async () => {
  const sessionId = 'p07-idempotent-meta';
  await sessionsMod.createSession(sessionId, { tenantId: 't1', channelAccountId: 'c1', engine: 'ai_core' });
  // A second call with DIFFERENT metadata must be ignored - the existing
  // in-memory record short-circuits createSession() before meta is ever
  // touched again (createSession's own preserved pre-P0.5 behavior).
  await sessionsMod.createSession(sessionId, { tenantId: 'DIFFERENT', channelAccountId: 'DIFFERENT', engine: 'django' });

  const session = sessionsMod.getSession(sessionId);
  assert.equal(session.tenantId, 't1');
  assert.equal(session.channelAccountId, 'c1');
  assert.equal(session.engine, 'ai_core');
});

test('readSessionMeta returns {} for a session with no meta.json at all (P0.7: "غيابها لا يكسر شيئاً")', async () => {
  const meta = await sessionsMod.readSessionMeta('no-such-session-ever-existed');
  assert.deepEqual(meta, {});
});

test('writeSessionMeta + readSessionMeta round-trip', async () => {
  const sessionId = 'p07-roundtrip';
  await sessionsMod.writeSessionMeta(sessionId, { tenantId: 't9', channelAccountId: 'c9', engine: 'ai_core' });
  const meta = await sessionsMod.readSessionMeta(sessionId);
  assert.deepEqual(meta, { tenantId: 't9', channelAccountId: 'c9', engine: 'ai_core' });
});

test('createSessionRecord defaults tenantId/channelAccountId/engine to null when no options are given (backward compat with pre-P0.7 test call sites)', () => {
  const session = sessionsMod.createSessionRecord('p07-plain-record');
  assert.equal(session.tenantId, null);
  assert.equal(session.channelAccountId, null);
  assert.equal(session.engine, null);
});
