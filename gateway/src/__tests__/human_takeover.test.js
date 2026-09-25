import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';

import { processFromMeMessage, setRedisClient } from '../sessions.js';
import { _keys as outboundKeys } from '../outbound/queue.js';
import { createRedisClient, closeRedisClient } from '../redis.js';

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
before(async () => { await waitForReady(redis); setRedisClient(redis); });
after(async () => { setRedisClient(null); await closeRedisClient(redis); });

function fromMeMsg({ id, remoteJid, text }) {
  return {
    key: { fromMe: true, remoteJid, id },
    messageTimestamp: Math.floor(Date.now() / 1000),
    message: { conversation: text },
  };
}

test('processFromMeMessage: an id already in sent_ids (our own echo) is recognized and NOT appended (G8)', async () => {
  const sessionId = `takeover-echo-${crypto.randomUUID()}`;
  const waId = `WAID-${crypto.randomUUID()}`;
  // P0.4's own pre-registration convention (processOne writes this BEFORE
  // calling sendMessage) - simulated directly here.
  await redis.set(outboundKeys.sentIdKey(sessionId, waId), '1', 'EX', 600);

  const appended = [];
  const appendFn = async (record) => { appended.push(record); return { status: 'appended' }; };

  const result = await processFromMeMessage(
    sessionId,
    fromMeMsg({ id: waId, remoteJid: '201234567890@s.whatsapp.net', text: 'bot reply' }),
    appendFn,
  );

  assert.equal(result, 'bot_echo');
  assert.equal(appended.length, 0, 'the bot\'s own echo must never be recorded as a takeover signal');
});

test('processFromMeMessage: an id NOT in sent_ids (a human typed on the phone) IS a takeover signal', async () => {
  const sessionId = `takeover-human-${crypto.randomUUID()}`;
  const waId = `WAID-${crypto.randomUUID()}`; // never registered in sent_ids

  const appended = [];
  const appendFn = async (record) => { appended.push(record); return { status: 'appended' }; };

  const result = await processFromMeMessage(
    sessionId,
    fromMeMsg({ id: waId, remoteJid: '201234567890@s.whatsapp.net', text: 'أنا هرد عليك دلوقتي بنفسي' }),
    appendFn,
  );

  assert.equal(result, 'human_takeover');
  assert.equal(appended.length, 1);
  const { event } = appended[0];
  assert.equal(event.type, 'human_takeover_signal');
  assert.equal(event.direction, 'outbound_human');
  assert.equal(event.provider_message_id, waId);
  assert.equal(event.text, 'أنا هرد عليك دلوقتي بنفسي');
  assert.equal(event.identity.phone_e164, '+201234567890', 'the customer identity is the chat this was sent TO');
});

test('processFromMeMessage: race-safe when sent_ids is written BEFORE the echo arrives (the normal, race-free ordering)', async () => {
  const sessionId = `takeover-race-${crypto.randomUUID()}`;
  const waId = `WAID-${crypto.randomUUID()}`;

  // Exactly P0.4's real ordering guarantee: sent_ids is written before
  // sendMessage() is even called, so by the time ANY echo (however fast)
  // reaches messages.upsert, the key already exists.
  await redis.set(outboundKeys.sentIdKey(sessionId, waId), '1', 'EX', 600);

  const appendFn = async () => { throw new Error('must not be called for a bot echo'); };
  const result = await processFromMeMessage(
    sessionId,
    fromMeMsg({ id: waId, remoteJid: '201234567890@s.whatsapp.net', text: 'bot reply' }),
    appendFn,
  );
  assert.equal(result, 'bot_echo');
});

test('processFromMeMessage: ignores group chats and content-less protocol messages', async () => {
  const sessionId = `takeover-ignore-${crypto.randomUUID()}`;
  const group = await processFromMeMessage(sessionId, {
    key: { fromMe: true, remoteJid: '120363000000000000@g.us', id: crypto.randomUUID() },
  });
  assert.equal(group, 'ignored');

  const noContent = await processFromMeMessage(sessionId, {
    key: { fromMe: true, remoteJid: '201234567890@s.whatsapp.net', id: crypto.randomUUID() },
  });
  assert.equal(noContent, 'ignored');
});
