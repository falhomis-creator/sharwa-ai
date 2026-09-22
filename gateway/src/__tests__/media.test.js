import test from 'node:test';
import assert from 'node:assert/strict';

import {
  detectMedia,
  mediaObjectKey,
  processInboundMessage,
  shouldIgnoreInbound,
} from '../sessions.js';

const MEDIA_KIND_FIELD = {
  image: 'imageMessage',
  audio: 'audioMessage',
  video: 'videoMessage',
  document: 'documentMessage',
};

function mediaMsg(kind, innerOverrides = {}) {
  const field = MEDIA_KIND_FIELD[kind];
  const inner = {
    mimetype: 'image/jpeg',
    mediaKey: Buffer.from('k'.repeat(32)),
    directPath: '/enc/something',
    ...innerOverrides,
  };
  return {
    key: { fromMe: false, remoteJid: '201234567890@s.whatsapp.net', id: `MEDIA-${kind}-1` },
    messageTimestamp: Math.floor(Date.now() / 1000),
    message: { [field]: inner },
  };
}

test('detectMedia identifies image/audio/video/document', () => {
  assert.equal(detectMedia(mediaMsg('image')).mediaType, 'image');
  assert.equal(detectMedia(mediaMsg('audio')).mediaType, 'audio');
  assert.equal(detectMedia(mediaMsg('video')).mediaType, 'video');
  assert.equal(detectMedia(mediaMsg('document')).mediaType, 'document');
  assert.equal(detectMedia({ key: {}, message: { conversation: 'hi' } }), null);
});

test('a media message with no caption is not ignored', () => {
  const msg = mediaMsg('image', { caption: '' });
  assert.equal(shouldIgnoreInbound(msg), false);
});

test('mediaObjectKey produces sharwa-ai/{session}/{uuid}.{ext}', () => {
  const key = mediaObjectKey('sess-1', 'jpg');
  assert.match(key, /^sharwa-ai\/sess-1\/[0-9a-f-]{36}\.jpg$/);
});

// P0.2: processInboundMessage now appends a normalized WAL record (not a
// direct Django webhook call). This test verifies the media upload wiring
// still runs and the object key lands in the appended record's `event.media`.
test('successful media upload is reflected in the appended WAL record', async () => {
  const msg = mediaMsg('image');
  const appended = [];
  const appendFn = async (record) => { appended.push(record); return { status: 'appended', id: '1-0' }; };
  const downloadFn = async () => Buffer.from('fake-image-bytes');
  const uploadFn = async (_buffer, objectKey) => objectKey;

  const resultId = await processInboundMessage('sess-1', msg, appendFn, { downloadFn, uploadFn });

  assert.equal(resultId, 'MEDIA-image-1');
  assert.equal(appended.length, 1);
  assert.equal(appended[0].sessionId, 'sess-1');
  assert.equal(appended[0].event.type, 'image');
  assert.match(appended[0].event.media.object_key, /^sharwa-ai\/sess-1\/[0-9a-f-]{36}\.jpg$/);
});

// P0.2: a failed upload (after retries) yields object_key: null + failed: true
// on the appended record, never throws, and still reaches the WAL (F2).
test('failed media upload yields object_key null in the appended record, never throws', async () => {
  const msg = mediaMsg('audio');
  const appended = [];
  const appendFn = async (record) => { appended.push(record); return { status: 'appended', id: '1-0' }; };
  const downloadFn = async () => Buffer.from('fake-audio-bytes');
  const uploadFn = async () => { throw new Error('MinIO down'); };

  const resultId = await processInboundMessage('sess-1', msg, appendFn, { downloadFn, uploadFn });

  assert.equal(resultId, 'MEDIA-audio-1');
  assert.equal(appended.length, 1);
  assert.equal(appended[0].event.media.object_key, null);
  assert.equal(appended[0].event.media.failed, true);
  assert.equal(appended[0].event.text, 'تعذّر معالجة المرفق المرسل.');
});
