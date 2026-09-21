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
    message: { [field]: inner },
  };
}

// Test: detectMedia identifies each supported media kind and ignores plain text.
test('detectMedia identifies image/audio/video/document', () => {
  assert.equal(detectMedia(mediaMsg('image')).mediaType, 'image');
  assert.equal(detectMedia(mediaMsg('audio')).mediaType, 'audio');
  assert.equal(detectMedia(mediaMsg('video')).mediaType, 'video');
  assert.equal(detectMedia(mediaMsg('document')).mediaType, 'document');
  assert.equal(detectMedia({ key: {}, message: { conversation: 'hi' } }), null);
});

// Test: a media message is never ignored, even with an empty caption.
test('a media message with no caption is not ignored', () => {
  const msg = mediaMsg('image', { caption: '' });
  assert.equal(shouldIgnoreInbound(msg), false);
});

// Test: object key follows the sharwa-ai/{session_id}/{uuid}.{ext} shape.
test('mediaObjectKey produces sharwa-ai/{session}/{uuid}.{ext}', () => {
  const key = mediaObjectKey('sess-1', 'jpg');
  assert.match(key, /^sharwa-ai\/sess-1\/[0-9a-f-]{36}\.jpg$/);
});

// Test: successful upload forwards media_object_key and media_type in the payload.
test('successful media upload forwards media_object_key and media_type', async () => {
  const msg = mediaMsg('image');
  const captured = [];
  const sendFn = async (payload) => { captured.push(payload); };
  const downloadFn = async () => Buffer.from('fake-image-bytes');
  const uploadFn = async (_buffer, objectKey) => objectKey;

  const resultId = await processInboundMessage('sess-1', msg, sendFn, { downloadFn, uploadFn });

  assert.equal(resultId, 'MEDIA-image-1');
  assert.equal(captured.length, 1);
  assert.equal(captured[0].media_type, 'image');
  assert.match(captured[0].media_object_key, /^sharwa-ai\/sess-1\/[0-9a-f-]{36}\.jpg$/);
});

// Test: failed upload (after retries) yields media_object_key null, never throws.
test('failed media upload yields media_object_key null without throwing', async () => {
  const msg = mediaMsg('audio');
  const captured = [];
  const sendFn = async (payload) => { captured.push(payload); };
  const downloadFn = async () => Buffer.from('fake-audio-bytes');
  const uploadFn = async () => { throw new Error('MinIO down'); };

  const resultId = await processInboundMessage('sess-1', msg, sendFn, { downloadFn, uploadFn });

  assert.equal(resultId, 'MEDIA-audio-1');
  assert.equal(captured.length, 1);
  assert.equal(captured[0].media_object_key, null);
  assert.equal(captured[0].media_type, 'audio');
  assert.equal(captured[0].text, 'تعذّر معالجة المرفق المرسل.');
});
