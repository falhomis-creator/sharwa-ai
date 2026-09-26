import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

import { createRedisClient, closeRedisClient } from '../redis.js';

// P0.7 (spec, literal): "تُحفظ بجانب بيانات المصادقة (meta.json) ليعيدها
// rehydrate" - a session that was created with tenant_id/channel_account_id/
// engine must come back with the SAME routing decision after a process
// restart, even though rehydrateSessions() only ever has a bare sessionId
// (never the original POST /sessions body). Own file/tmp AUTH_SESSIONS_DIR
// (own node --test worker process) so rehydrateSessions()'s own
// listRegisteredSessionIds() orphan-folder cleanup never interacts with
// session_metadata.test.js's own directories - same isolation rationale
// rehydrate_staging.test.js already documents for itself.

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
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'p07-rehydrate-meta-'));
  process.env.AUTH_SESSIONS_DIR = tmpDir;
  process.env.REHYDRATE_CONCURRENCY = '2';
  process.env.REHYDRATE_JITTER_MIN_MS = '5';
  process.env.REHYDRATE_JITTER_MAX_MS = '15';

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

test('rehydrateSessions restores a registered session\'s tenant_id/channel_account_id/engine from its meta.json', async () => {
  const sessionId = 'p07-rehydrated-with-meta';
  const dir = path.join(tmpDir, sessionId);
  await fs.mkdir(dir, { recursive: true });

  const { useMultiFileAuthState } = await import('../driver/waDriver.js');
  const { state, saveCreds } = await useMultiFileAuthState(dir);
  state.creds.registered = true;
  await saveCreds();

  await sessionsMod.writeSessionMeta(sessionId, {
    tenantId: 'tenant-xyz',
    channelAccountId: 'chan-999',
    engine: 'ai_core',
  });

  await sessionsMod.rehydrateSessions();

  const session = sessionsMod.getSession(sessionId);
  assert.ok(session, 'rehydrateSessions must have started this registered session');
  assert.equal(session.tenantId, 'tenant-xyz');
  assert.equal(session.channelAccountId, 'chan-999');
  assert.equal(session.engine, 'ai_core');
});

test('rehydrateSessions restores a registered LEGACY session (no meta.json) with every field null', async () => {
  const sessionId = 'p07-rehydrated-legacy';
  const dir = path.join(tmpDir, sessionId);
  await fs.mkdir(dir, { recursive: true });

  const { useMultiFileAuthState } = await import('../driver/waDriver.js');
  const { state, saveCreds } = await useMultiFileAuthState(dir);
  state.creds.registered = true;
  await saveCreds();
  // Deliberately no writeSessionMeta call here - this is the pre-P0.7 /
  // legacy-Django shape (spec: "غيابها لا يكسر شيئاً").

  await sessionsMod.rehydrateSessions();

  const session = sessionsMod.getSession(sessionId);
  assert.ok(session, 'rehydrateSessions must have started this registered session');
  assert.equal(session.tenantId, null);
  assert.equal(session.channelAccountId, null);
  assert.equal(session.engine, null);
});
