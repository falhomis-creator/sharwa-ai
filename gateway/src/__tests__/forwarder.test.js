import test from 'node:test';
import assert from 'node:assert/strict';
import { config } from '../config.js';

import { toWebhookPayload, shouldForwardToLegacy, waitForReady, BLOCK_MS } from '../forwarder.js';

test('toWebhookPayload maps a normalized WAL entry to the Django webhook contract', () => {
  const entry = {
    v: 1,
    session_id: 's1',
    provider_message_id: 'AAA5',
    type: 'text',
    ts: 1700000000000,
    identity: { jid_raw: '201234567890@s.whatsapp.net', addressing: 'pn', wa_id: '201234567890', phone_e164: '+201234567890' },
    text: 'hi',
  };
  const payload = toWebhookPayload('s1', entry);
  assert.equal(payload.session_id, 's1');
  assert.equal(payload.from, '201234567890@s.whatsapp.net');
  assert.equal(payload.text, 'hi');
  assert.equal(payload.message_id, 'AAA5');
  assert.equal(payload.media_object_key, null);
  assert.equal(payload.media_type, null);
});

test('toWebhookPayload carries media_object_key/media_type through from a media entry', () => {
  const entry = {
    provider_message_id: 'MEDIA-1',
    identity: { jid_raw: '201234567890@s.whatsapp.net' },
    text: '',
    media: { kind: 'image', object_key: 'sharwa-ai/s1/abc.jpg' },
  };
  const payload = toWebhookPayload('s1', entry);
  assert.equal(payload.media_object_key, 'sharwa-ai/s1/abc.jpg');
  assert.equal(payload.media_type, 'image');
});

test('shouldForwardToLegacy forwards ordinary message entries', () => {
  assert.equal(shouldForwardToLegacy({ type: 'text', text: 'hi' }), true);
  assert.equal(shouldForwardToLegacy({ type: 'image' }), true);
});

test('shouldForwardToLegacy skips identity_update entries (G12: no legacy field for them)', () => {
  assert.equal(shouldForwardToLegacy({ type: 'identity_update', lid: '1@lid', phone_e164: '+201111112222' }), false);
});

test('waitForReady resolves immediately when the client is already ready', async () => {
  const client = { status: 'ready' };
  await waitForReady(client); // must not hang/throw
});

test('waitForReady waits for the ready event before resolving (the ordering bug this fixes)', async () => {
  const handlers = {};
  const client = {
    status: 'connecting',
    once(event, fn) { handlers[event] = fn; },
    off(event, fn) { if (handlers[event] === fn) delete handlers[event]; },
  };
  const p = waitForReady(client);
  let resolved = false;
  p.then(() => { resolved = true; });
  await Promise.resolve(); // let the promise executor run
  assert.equal(resolved, false); // must NOT resolve before 'ready' fires
  handlers.ready();
  await p;
  assert.equal(resolved, true);
});

test('waitForReady rejects if the client errors before becoming ready', async () => {
  const handlers = {};
  const client = {
    status: 'connecting',
    once(event, fn) { handlers[event] = fn; },
    off(event, fn) { if (handlers[event] === fn) delete handlers[event]; },
  };
  const p = waitForReady(client);
  const boom = new Error('ECONNREFUSED');
  handlers.error(boom);
  await assert.rejects(p, /ECONNREFUSED/);
});

test('BLOCK_MS stays below the ioredis commandTimeout (regression: they raced and XREADGROUP threw "Command timed out" on every idle cycle)', () => {
  assert.ok(
    BLOCK_MS < config.redis.timeoutMs,
    `BLOCK_MS (${BLOCK_MS}) must be less than config.redis.timeoutMs (${config.redis.timeoutMs})`,
  );
});
