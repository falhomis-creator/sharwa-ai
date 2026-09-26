import test from 'node:test';
import assert from 'node:assert/strict';

import { processInboundMessage, processFromMeMessage } from '../sessions.js';

// P0.7 (spec, literal - prompts/P0_DEEPSEEK_PROMPT.md): a WAL entry appended
// for an engine=ai_core session must carry that tag, so forwarder.js's
// shouldForwardToLegacy (see forwarder.test.js) can skip legacy Django
// delivery for it. Wired via wireSocketEvents' `deps = { engine: session.engine }`
// (sessions.js) - these are direct, no-socket-needed unit tests of the two
// append-path functions it feeds, mirroring webhook.test.js's own style.

function inboundMsg({ id, text }) {
  return {
    key: { fromMe: false, remoteJid: '201234567890@s.whatsapp.net', id },
    messageTimestamp: Math.floor(Date.now() / 1000),
    message: { conversation: text },
  };
}

function fromMeMsg({ id, text }) {
  return {
    key: { fromMe: true, remoteJid: '201234567890@s.whatsapp.net', id },
    messageTimestamp: Math.floor(Date.now() / 1000),
    message: { conversation: text },
  };
}

test('processInboundMessage tags the WAL entry with deps.engine when the session has one', async () => {
  const appended = [];
  const appendFn = async (record) => { appended.push(record); return { status: 'appended' }; };

  await processInboundMessage('s-ai-core', inboundMsg({ id: 'M1', text: 'hi' }), appendFn, { engine: 'ai_core' });

  assert.equal(appended.length, 1);
  assert.equal(appended[0].event.engine, 'ai_core');
});

test('processInboundMessage leaves entry.engine unset for a legacy session (no deps.engine)', async () => {
  const appended = [];
  const appendFn = async (record) => { appended.push(record); return { status: 'appended' }; };

  await processInboundMessage('s-legacy', inboundMsg({ id: 'M2', text: 'hi' }), appendFn);

  assert.equal(appended.length, 1);
  assert.equal(appended[0].event.engine, undefined, 'a legacy/no-metadata session must never gain an engine tag (P0.7: "جلسة بلا engine = django")');
});

test('processFromMeMessage (human_takeover_signal) tags the WAL entry with deps.engine when the session has one', async () => {
  const appended = [];
  const appendFn = async (record) => { appended.push(record); return { status: 'appended' }; };

  const result = await processFromMeMessage('s-ai-core', fromMeMsg({ id: 'M3', text: 'merchant typed this' }), appendFn, { engine: 'ai_core' });

  assert.equal(result, 'human_takeover');
  assert.equal(appended.length, 1);
  assert.equal(appended[0].event.engine, 'ai_core');
});

test('processFromMeMessage leaves entry.engine unset for a legacy session (no deps.engine)', async () => {
  const appended = [];
  const appendFn = async (record) => { appended.push(record); return { status: 'appended' }; };

  await processFromMeMessage('s-legacy', fromMeMsg({ id: 'M4', text: 'merchant typed this' }), appendFn);

  assert.equal(appended.length, 1);
  assert.equal(appended[0].event.engine, undefined);
});
