import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';

import { sign, postInboundMessage, postSessionStatus } from '../webhook.js';
import {
  createSessionRecord,
  enqueueSend,
  processInboundMessage,
  shouldIgnoreInbound,
  extractText,
  setRedisClient,
  startSessionOutboundWorker,
} from '../sessions.js';
import { createRedisClient, closeRedisClient } from '../redis.js';

// createRedisClient() uses lazyConnect:false + enableOfflineQueue:false, so
// the first command must wait for 'ready' (same ordering fix as forwarder.js
// / real_redis_integration.test.js) - otherwise it fails immediately with
// "Stream isn't writeable and enableOfflineQueue options is false".
function waitForRedisReady(client) {
  if (client.status === 'ready') return Promise.resolve();
  return new Promise((resolve, reject) => {
    const onReady = () => { client.off('error', onError); resolve(); };
    const onError = (err) => { client.off('ready', onReady); reject(err); };
    client.once('ready', onReady);
    client.once('error', onError);
  });
}

// P0.4 (deviation, see docs/P0_DEVIATIONS.md): this suite's own send-queue
// test below used to drive sessions.js's OLD in-RAM, non-durable queue
// (enqueueSend was synchronous and createSessionRecord accepted a
// sendDelayMs jitter function). P0.4 replaced that queue entirely with a
// durable, Redis-backed one (gateway/src/outbound/queue.js) - enqueueSend is
// now async and requires a real redis-durable client to be configured
// (REDIS_NOT_CONFIGURED otherwise, by design - H3: never pretend to queue
// something that only lives in RAM again). The real invariant the old test
// existed to prove - two messages for the same session are NEVER sent
// concurrently - still holds under the new architecture (the outbound
// worker processes one item at a time per session via BLMOVE), so the test
// below is rewritten to prove that same invariant through the new API
// (setRedisClient + startSessionOutboundWorker + async enqueueSend) against
// a real local Redis, rather than being dropped (H8: never weaken/skip an
// existing test).

const SECRET = 'sharwa-ai-test-webhook-secret';

function withSecret(fn) {
  const prev = process.env.SHARWA_AI_GATEWAY_WEBHOOK_SECRET;
  process.env.SHARWA_AI_GATEWAY_WEBHOOK_SECRET = SECRET;
  try {
    return fn();
  } finally {
    if (prev === undefined) {
      delete process.env.SHARWA_AI_GATEWAY_WEBHOOK_SECRET;
    } else {
      process.env.SHARWA_AI_GATEWAY_WEBHOOK_SECRET = prev;
    }
  }
}

async function waitUntil(cond, timeoutMs = 5000) {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > timeoutMs) throw new Error('waitUntil timed out');
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}

test('sign() is deterministic and changes completely when the body changes', () => {
  withSecret(() => {
    const rawBody = '{"session_id":"tenant-a"}';
    const timestamp = '1700000000';

    const s1 = sign(rawBody, timestamp);
    const s2 = sign(rawBody, timestamp);
    assert.equal(s1, s2, 'same inputs must produce the same signature');

    const expected = crypto
      .createHmac('sha256', SECRET)
      .update(`${timestamp}.${rawBody}`)
      .digest('hex');
    assert.equal(s1, expected, 'signature must match HMAC-SHA256(timestamp.body)');

    const changed = rawBody.replace('tenant-a', 'tenant-b');
    const s3 = sign(changed, timestamp);
    assert.notEqual(s1, s3, 'a one-character change must change the signature completely');
  });
});

// P0.2: inbound filtering now gates the WAL append (appendFn), not a direct
// Django webhook call - the filtering guarantee itself (fromMe/group/
// broadcast/empty never reach ANY delivery path) is unchanged and still the
// point of this test.
test('inbound filtering: fromMe/group/broadcast/empty messages never reach the WAL append path', async () => {
  let calls = 0;
  const appendFn = async () => {
    calls += 1;
    return { status: 'appended', id: '1-0' };
  };

  const fromMe = {
    key: { fromMe: true, remoteJid: '201234567890@s.whatsapp.net', id: 'AAA1' },
    message: { conversation: 'hello from merchant' },
  };
  assert.equal(shouldIgnoreInbound(fromMe), true);
  assert.equal(await processInboundMessage('s1', fromMe, appendFn), null);
  assert.equal(calls, 0);

  const group = {
    key: { fromMe: false, remoteJid: '120363000000000000@g.us', id: 'AAA2' },
    message: { conversation: 'group chatter' },
  };
  assert.equal(shouldIgnoreInbound(group), true);
  assert.equal(await processInboundMessage('s1', group, appendFn), null);
  assert.equal(calls, 0);

  const broadcast = {
    key: { fromMe: false, remoteJid: 'status@broadcast', id: 'AAA3' },
    message: { conversation: 'broadcast' },
  };
  assert.equal(shouldIgnoreInbound(broadcast), true);
  assert.equal(await processInboundMessage('s1', broadcast, appendFn), null);
  assert.equal(calls, 0);

  // No `message` at all (e.g. a protocol-only upsert) is the genuine "nothing
  // to record" case - an empty-string `conversation` is, by contrast, a real
  // (if empty) text message and is NOT filtered (see normalize.test.js: "text
  // type (even empty) is still content").
  const noContent = {
    key: { fromMe: false, remoteJid: '201234567890@s.whatsapp.net', id: 'AAA4' },
  };
  assert.equal(shouldIgnoreInbound(noContent), true);
  assert.equal(await processInboundMessage('s1', noContent, appendFn), null);
  assert.equal(calls, 0);

  // A legitimate inbound message MUST pass through exactly once.
  const valid = {
    key: { fromMe: false, remoteJid: '201234567890@s.whatsapp.net', id: 'AAA5' },
    messageTimestamp: Math.floor(Date.now() / 1000),
    message: { conversation: 'hi' },
  };
  const resultId = await processInboundMessage('s1', valid, appendFn);
  assert.equal(resultId, 'AAA5');
  assert.equal(calls, 1);
});

test('send queue (P0.4, durable): two messages for the same session are never sent simultaneously', async () => {
  process.env.ALLOW_FAKE_WA = '1';
  process.env.NODE_ENV = 'test';

  let active = 0;
  let maxActive = 0;
  const sent = [];

  const fakeSock = {
    sendMessage: async (to, content) => {
      active += 1;
      maxActive = Math.max(maxActive, active);
      sent.push({ to, text: content.text, at: Date.now() });
      await new Promise((resolve) => setTimeout(resolve, 20));
      active -= 1;
    },
  };

  const redis = createRedisClient();
  await waitForRedisReady(redis);
  setRedisClient(redis);
  try {
    const sessionId = `test-serialization-session-${crypto.randomUUID()}`;
    const session = createSessionRecord(sessionId, { sock: fakeSock });
    session.status = 'CONNECTED'; // the worker only reads session.sock while CONNECTED
    // P0.5: the worker now runs on its own dedicated Redis connection (never
    // the shared `redis` client above) - see sessions.js's waitForRedisReady
    // comment. Must be awaited so session.outboundWorkerClient exists before
    // this test's own cleanup below tries to close it.
    await startSessionOutboundWorker(session);

    const dest = `${crypto.randomUUID()}@s.whatsapp.net`;
    const r1 = await enqueueSend(sessionId, dest, 'message one');
    const r2 = await enqueueSend(sessionId, dest, 'message two');
    assert.equal(r1.status, 'queued', 'first message should be enqueued');
    assert.equal(r2.status, 'queued', 'second message should be enqueued');

    await waitUntil(() => sent.length === 2);

    assert.equal(sent.length, 2);
    assert.equal(maxActive, 1, 'messages must never be in-flight concurrently');
    assert.ok(sent[1].at >= sent[0].at, 'second send must start no earlier than the first');

    session.outboundWorker.stop();
    await session.outboundWorker.done;
    await closeRedisClient(session.outboundWorkerClient);
  } finally {
    setRedisClient(null);
    await closeRedisClient(redis);
  }
});

test('postInboundMessage forwards text as message_text (Django contract)', async () => {
  const base = 'http://localhost:8000';
  const prevBase = process.env.DJANGO_BASE_URL;
  const prevSecret = process.env.SHARWA_AI_GATEWAY_WEBHOOK_SECRET;
  process.env.DJANGO_BASE_URL = base;
  process.env.SHARWA_AI_GATEWAY_WEBHOOK_SECRET = SECRET;

  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  try {
    await postInboundMessage({
      session_id: 's1',
      from: '201234567890@s.whatsapp.net',
      text: 'أين طلبي؟',
      message_id: 'm1',
      media_object_key: 'sharwa-ai/s1/abc.jpg',
      media_type: 'image',
    });
  } finally {
    globalThis.fetch = originalFetch;
    if (prevBase === undefined) delete process.env.DJANGO_BASE_URL;
    else process.env.DJANGO_BASE_URL = prevBase;
    if (prevSecret === undefined) delete process.env.SHARWA_AI_GATEWAY_WEBHOOK_SECRET;
    else process.env.SHARWA_AI_GATEWAY_WEBHOOK_SECRET = prevSecret;
  }

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, `${base}/webhooks/sharwa-ai/inbound-message/`);
  const body = JSON.parse(calls[0].options.body);
  assert.equal(body.message_text, 'أين طلبي؟');
  assert.equal(body.text, undefined, 'must not send the legacy `text` key');
  assert.equal(body.message_id, 'm1');
  assert.equal(body.media_object_key, 'sharwa-ai/s1/abc.jpg');
  assert.equal(body.media_type, 'image');
});

test('outgoing webhook paths match Django public routes (no /api/, trailing slash)', async () => {
  const base = 'http://localhost:8000';
  const prevBase = process.env.DJANGO_BASE_URL;
  const prevSecret = process.env.SHARWA_AI_GATEWAY_WEBHOOK_SECRET;
  process.env.DJANGO_BASE_URL = base;
  process.env.SHARWA_AI_GATEWAY_WEBHOOK_SECRET = SECRET;

  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  try {
    await postSessionStatus({ session_id: 's1', status: 'QR_PENDING', phone_number: null, detail: null });
    await postInboundMessage({ session_id: 's1', from: '201234567890@s.whatsapp.net', text: 'hi', message_id: 'm1' });
  } finally {
    globalThis.fetch = originalFetch;
    if (prevBase === undefined) delete process.env.DJANGO_BASE_URL;
    else process.env.DJANGO_BASE_URL = prevBase;
    if (prevSecret === undefined) delete process.env.SHARWA_AI_GATEWAY_WEBHOOK_SECRET;
    else process.env.SHARWA_AI_GATEWAY_WEBHOOK_SECRET = prevSecret;
  }

  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, `${base}/webhooks/sharwa-ai/session-status/`);
  assert.equal(calls[1].url, `${base}/webhooks/sharwa-ai/inbound-message/`);
});
