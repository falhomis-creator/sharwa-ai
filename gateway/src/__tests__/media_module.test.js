// gateway/src/__tests__/media_module.test.js
// P0.3 (G3, disasters #16/#21): fast, hermetic unit tests for the new
// gateway/src/media.js module - caps, object-key format, magic-byte
// verification, the download/upload pipeline, and the concurrency
// semaphore. Follows the same convention as wal.test.js / dedupe.test.js /
// spool.test.js: a stub/fake redis client here, for speed and isolation.
// The quota Lua script's real atomicity under a genuine concurrent race is
// separately proven against real redis-durable in
// real_redis_integration.test.js (scenario (د) quota) - a fake client
// cannot execute real Lua, so it re-implements equivalent semantics in JS
// purely to exercise the surrounding orchestration logic here.

import test from 'node:test';
import assert from 'node:assert/strict';
import { S3Client } from '@aws-sdk/client-s3';
import {
  capBytesFor,
  normalizeFileLength,
  mediaObjectKey,
  setDownloadConcurrency,
  quotaLimits,
  magicByteMatches,
  downloadAndUploadMedia,
} from '../media.js';
import { fileTypeFromBuffer } from 'file-type';

// A real, valid 1x1 transparent PNG - file-type needs a genuinely valid
// signature + structure to detect it, not just the first magic bytes.
const REAL_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
  'base64',
);

function pngBuffer(padTo = 5000) {
  if (padTo <= REAL_PNG.length) return REAL_PNG.subarray(0, padTo);
  return Buffer.concat([REAL_PNG, Buffer.alloc(padTo - REAL_PNG.length, 0xab)]);
}

async function* fakeStream(buffer, chunkSize = 512) {
  for (let i = 0; i < buffer.length; i += chunkSize) {
    yield buffer.subarray(i, i + chunkSize);
  }
}

// In-memory stand-in for redis-durable's two Lua scripts (reserveQuota /
// releaseQuota in media.js), dispatched by argv shape since both scripts
// share one KEYS[1]. Real Lua atomicity is verified separately in
// real_redis_integration.test.js against the genuine redis-durable.
function fakeRedisQuota() {
  const store = new Map();
  return {
    store,
    async eval(_script, _numKeys, key, ...argv) {
      const rec = store.get(key) || { files: 0, bytes: 0 };
      if (argv.length === 4) {
        const [maxFiles, maxBytes, estBytes] = argv.map(Number);
        if (rec.files + 1 > maxFiles || rec.bytes + estBytes > maxBytes) {
          return [0, rec.files, rec.bytes];
        }
        rec.files += 1;
        rec.bytes += estBytes;
        store.set(key, rec);
        return [1, rec.files, rec.bytes];
      }
      const [deltaBytes] = argv.map(Number);
      rec.files -= 1;
      rec.bytes -= deltaBytes;
      store.set(key, rec);
      return [String(rec.files), String(rec.bytes)];
    },
    async hincrby(key, field, delta) {
      const rec = store.get(key) || { files: 0, bytes: 0 };
      rec[field] += Number(delta);
      store.set(key, rec);
      return rec[field];
    },
  };
}

function fakeS3Client() {
  const puts = [];
  // Upload (@aws-sdk/lib-storage) inspects real S3Client internals
  // (config.requestHandler, etc.) beyond just calling `.send`, so a bare
  // duck-typed {send} object is not enough - build a real S3Client
  // (it never actually dials out; `.send` is overridden below).
  const client = new S3Client({
    endpoint: 'http://127.0.0.1:1',
    region: 'us-east-1',
    forcePathStyle: true,
    credentials: { accessKeyId: 'test', secretAccessKey: 'test' },
  });
  client.send = async (command) => {
    const name = command.constructor.name;
    if (name === 'PutObjectCommand') {
      const { Bucket, Key, Body, ContentType } = command.input;
      let body;
      if (Buffer.isBuffer(Body) || Body instanceof Uint8Array) {
        body = Buffer.from(Body);
      } else {
        const chunks = [];
        for await (const c of Body) chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c));
        body = Buffer.concat(chunks);
      }
      puts.push({ Bucket, Key, ContentType, body });
      return { ETag: '"fake"' };
    }
    throw new Error(`fakeS3Client: unsupported command ${name}`);
  };
  client.puts = puts;
  return client;
}

// --- caps -------------------------------------------------------------

test('capBytesFor returns the P0.3 spec defaults when env unset', () => {
  assert.equal(capBytesFor('image', {}), 16 * 1024 * 1024);
  assert.equal(capBytesFor('audio', {}), 16 * 1024 * 1024);
  assert.equal(capBytesFor('video', {}), 64 * 1024 * 1024);
  assert.equal(capBytesFor('document', {}), 64 * 1024 * 1024);
  assert.equal(capBytesFor('sticker', {}), 2 * 1024 * 1024);
});

test('capBytesFor honors an env override', () => {
  assert.equal(capBytesFor('image', { MEDIA_MAX_BYTES_IMAGE: '1234' }), 1234);
});

test('normalizeFileLength handles number, Long-like, and null/undefined', () => {
  assert.equal(normalizeFileLength(1000), 1000);
  assert.equal(normalizeFileLength({ toNumber: () => 2048 }), 2048);
  assert.equal(normalizeFileLength(null), null);
  assert.equal(normalizeFileLength(undefined), null);
});

test('quotaLimits defaults to 500 files / 1GB', () => {
  const { maxFiles, maxBytes } = quotaLimits({});
  assert.equal(maxFiles, 500);
  assert.equal(maxBytes, 1024 * 1024 * 1024);
});

// --- object key ---------------------------------------------------------

test('mediaObjectKey matches {session_id}/{YYYY}/{MM}/{provider_message_id}.{ext}', () => {
  const key = mediaObjectKey({
    sessionId: 'sess-1',
    providerMessageId: 'MSGID123',
    ext: 'jpg',
    now: new Date('2026-03-05T00:00:00Z'),
  });
  assert.equal(key, 'sess-1/2026/03/MSGID123.jpg');
});

// --- magic bytes ----------------------------------------------------------

test('magicByteMatches: real PNG bytes match image, mismatch video', async () => {
  const detected = await fileTypeFromBuffer(REAL_PNG);
  assert.equal(detected?.mime, 'image/png');
  assert.equal(magicByteMatches(detected, 'image'), true);
  assert.equal(magicByteMatches(detected, 'sticker'), true);
  assert.equal(magicByteMatches(detected, 'video'), false);
});

test('magicByteMatches: undetected bytes only pass for document', () => {
  assert.equal(magicByteMatches(null, 'document'), true);
  assert.equal(magicByteMatches(null, 'image'), false);
});

// --- full download+upload pipeline --------------------------------------

test('downloadAndUploadMedia: happy path uploads and returns object_key', async () => {
  setDownloadConcurrency(3);
  const redis = fakeRedisQuota();
  const s3 = fakeS3Client();
  const buf = pngBuffer(5000);
  const result = await downloadAndUploadMedia({
    content: { fileLength: buf.length },
    type: 'image',
    sessionId: 'sess-happy',
    providerMessageId: `MSG-${Date.now()}`,
    ext: 'jpg',
    mimeType: 'image/jpeg',
    redisClient: redis,
    s3Client: s3,
    bucket: 'sharwa-ai',
    downloadFn: async () => fakeStream(buf),
  });
  assert.equal(result.status, 'ok');
  assert.match(result.object_key, /^sess-happy\/\d{4}\/\d{2}\/MSG-\d+\.jpg$/);
  assert.equal(result.size, buf.length);
  assert.equal(s3.puts.length, 1);
  assert.equal(s3.puts[0].body.length, buf.length);
});

test('downloadAndUploadMedia: declared fileLength over cap is rejected before download', async () => {
  const redis = fakeRedisQuota();
  const s3 = fakeS3Client();
  let downloadCalled = false;
  const result = await downloadAndUploadMedia({
    content: { fileLength: 999 * 1024 * 1024 },
    type: 'sticker', // cap 2MB
    sessionId: 'sess-cap',
    providerMessageId: `MSG-${Date.now()}`,
    ext: 'webp',
    mimeType: 'image/webp',
    redisClient: redis,
    s3Client: s3,
    bucket: 'sharwa-ai',
    downloadFn: async () => { downloadCalled = true; return fakeStream(Buffer.alloc(10)); },
  });
  assert.equal(result.status, 'too_large');
  assert.equal(downloadCalled, false, 'must not download when the declared size already exceeds the cap');
  assert.equal(s3.puts.length, 0);
});

test('downloadAndUploadMedia: actual bytes exceeding cap abort mid-stream (no declared fileLength)', async () => {
  const redis = fakeRedisQuota();
  const s3 = fakeS3Client();
  const big = pngBuffer(3 * 1024 * 1024); // valid PNG signature, but 3MB > sticker's 2MB cap
  const result = await downloadAndUploadMedia({
    content: {},
    type: 'sticker',
    sessionId: 'sess-midcap',
    providerMessageId: `MSG-${Date.now()}`,
    ext: 'webp',
    mimeType: 'image/webp',
    redisClient: redis,
    s3Client: s3,
    bucket: 'sharwa-ai',
    downloadFn: async () => fakeStream(big, 4096),
  });
  assert.equal(result.status, 'too_large');
  assert.equal(s3.puts.length, 0);
});

test('downloadAndUploadMedia: magic-byte mismatch is rejected and quota is released', async () => {
  const redis = fakeRedisQuota();
  const s3 = fakeS3Client();
  const storeId = `sess-mismatch-${Date.now()}`;
  const notAnImage = Buffer.concat([
    Buffer.from('this is definitely not an image file'),
    Buffer.alloc(5000, 0x20),
  ]);
  const result = await downloadAndUploadMedia({
    content: { fileLength: notAnImage.length },
    type: 'image',
    sessionId: storeId,
    providerMessageId: `MSG-${Date.now()}`,
    ext: 'jpg',
    mimeType: 'image/jpeg',
    redisClient: redis,
    s3Client: s3,
    bucket: 'sharwa-ai',
    downloadFn: async () => fakeStream(notAnImage),
  });
  assert.equal(result.status, 'rejected_type');
  assert.equal(s3.puts.length, 0);
  const rec = redis.store.get(`media_quota:${storeId}:${new Date().toISOString().slice(0, 10)}`);
  assert.equal(rec.files, 0, 'the reserved slot must be released on rejection');
});

test('downloadAndUploadMedia: quota_exceeded short-circuits before any download', async () => {
  const redis = fakeRedisQuota();
  const s3 = fakeS3Client();
  const storeId = `sess-quota-${Date.now()}`;
  process.env.MEDIA_QUOTA_DAILY_FILES = '1';
  try {
    const buf = pngBuffer(1000);
    const first = await downloadAndUploadMedia({
      content: { fileLength: buf.length },
      type: 'image',
      sessionId: storeId,
      providerMessageId: `MSG-A-${Date.now()}`,
      ext: 'jpg',
      mimeType: 'image/jpeg',
      redisClient: redis,
      s3Client: s3,
      bucket: 'sharwa-ai',
      downloadFn: async () => fakeStream(buf),
    });
    assert.equal(first.status, 'ok');

    let downloadCalled = false;
    const second = await downloadAndUploadMedia({
      content: { fileLength: buf.length },
      type: 'image',
      sessionId: storeId,
      providerMessageId: `MSG-B-${Date.now()}`,
      ext: 'jpg',
      mimeType: 'image/jpeg',
      redisClient: redis,
      s3Client: s3,
      bucket: 'sharwa-ai',
      downloadFn: async () => { downloadCalled = true; return fakeStream(buf); },
    });
    assert.equal(second.status, 'quota_exceeded');
    assert.equal(downloadCalled, false);
  } finally {
    delete process.env.MEDIA_QUOTA_DAILY_FILES;
  }
});

test('downloadAndUploadMedia: concurrency semaphore caps simultaneous downloads', async () => {
  setDownloadConcurrency(2);
  let concurrent = 0;
  let maxConcurrent = 0;
  const redis = fakeRedisQuota();
  const s3 = fakeS3Client();
  const buf = pngBuffer(600);

  async function run(i) {
    return downloadAndUploadMedia({
      content: { fileLength: buf.length },
      type: 'image',
      sessionId: 'sess-conc',
      providerMessageId: `MSG-C${i}-${Date.now()}`,
      ext: 'jpg',
      mimeType: 'image/jpeg',
      redisClient: redis,
      s3Client: s3,
      bucket: 'sharwa-ai',
      downloadFn: async () => {
        concurrent += 1;
        maxConcurrent = Math.max(maxConcurrent, concurrent);
        await new Promise((r) => setTimeout(r, 30));
        concurrent -= 1;
        return fakeStream(buf);
      },
    });
  }

  const results = await Promise.all([run(1), run(2), run(3), run(4), run(5)]);
  assert.ok(results.every((r) => r.status === 'ok'));
  assert.ok(maxConcurrent <= 2, `expected at most 2 concurrent downloads, saw ${maxConcurrent}`);
  setDownloadConcurrency(Number(process.env.MEDIA_DOWNLOAD_CONCURRENCY) || 3);
});
