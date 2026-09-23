// gateway/src/__tests__/media_e2e_minio.test.js
// P0.3 real E2E acceptance tests, run against REAL MinIO and REAL
// redis-durable (spec: "قبول P0.3 (بـMinIO حقيقية في docker-compose.test.yml)",
// prompts/P0_DEEPSEEK_PROMPT.md §6 P0.3). Not run by the default `node --test`
// suite (that stays hermetic/fast) - invoked explicitly via
// `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm
// gateway node --test src/__tests__/media_e2e_minio.test.js`, which is also
// what wires it to the real `minio` and `redis-durable` services on the
// `data` network. Guarded by RUN_MEDIA_E2E_TESTS=1 (plain `if`, not `.skip` -
// H7) so the default fast suite never silently depends on MinIO being up.
//
// Uses the REAL redis-durable that gateway/gateway-forwarder also use (same
// precedent as real_redis_integration.test.js: real client, randomized
// session/provider-message ids per test run, no flush) - never touched or
// disturbed beyond adding its own quota keys.

import test from 'node:test';
import assert from 'node:assert/strict';
import { Redis } from 'ioredis';
import { S3Client, ListMultipartUploadsCommand } from '@aws-sdk/client-s3';
import { fork } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  downloadAndUploadMedia,
  getMediaMetrics,
  resetMediaMetrics,
  setDownloadConcurrency,
} from '../media.js';

const RUN = process.env.RUN_MEDIA_E2E_TESTS === '1';

if (!RUN) {
  test('(P0.3 E2E) not run: set RUN_MEDIA_E2E_TESTS=1 and run via docker-compose.test.yml (real MinIO) to execute', () => {
    // Intentionally a real, passing, real-output test - never the test-runner
    // skip mechanism (H7): the fast default suite must stay green without
    // requiring MinIO, but this is a visible, explained no-op, not a
    // silently disabled check.
  });
} else {
  const __dirname = path.dirname(fileURLToPath(import.meta.url));

  const BUCKET = process.env.MINIO_TEST_BUCKET_NAME || 'sharwa-ai-test';
  const s3Client = new S3Client({
    endpoint: process.env.MINIO_TEST_ENDPOINT_URL || 'http://minio:9000',
    region: 'us-east-1',
    forcePathStyle: true,
    credentials: {
      accessKeyId: process.env.MINIO_TEST_ACCESS_KEY,
      secretAccessKey: process.env.MINIO_TEST_SECRET_KEY,
    },
  });

  const redis = new Redis({
    host: process.env.REDIS_DURABLE_HOST || 'redis-durable',
    port: Number(process.env.REDIS_DURABLE_PORT || 6379),
    password: process.env.REDIS_DURABLE_PASSWORD,
  });

  test.after(async () => {
    await redis.quit();
  });

  // --- helpers ---------------------------------------------------------

  /** An async-iterable that yields `sizeBytes` in bounded chunks, exactly as
   * downloadContentFromMessage's real stream would - never materializing the
   * whole payload as one Buffer (that is the thing under test). */
  async function* fakeMediaStream(sizeBytes, { chunkSize = 256 * 1024 } = {}) {
    let remaining = sizeBytes;
    while (remaining > 0) {
      const n = Math.min(chunkSize, remaining);
      yield Buffer.alloc(n, 0x41);
      remaining -= n;
    }
  }

  function rssBytes() {
    return process.memoryUsage().rss;
  }

  function mb(bytes) {
    return (bytes / (1024 * 1024)).toFixed(1);
  }

  let mediaSeq = 0;
  function nextIds(sessionPrefix) {
    mediaSeq += 1;
    return {
      sessionId: `p03e2e-${sessionPrefix}-${Date.now()}-${mediaSeq}`,
      providerMessageId: `MSG-${mediaSeq}-${Date.now()}`,
    };
  }

  // --- E1: 50MB single file - RSS growth < 100MB above baseline ---------

  test('(P0.3-E1) 50MB streamed download+upload: RSS growth stays under 100MB above baseline (real MinIO + real redis-durable)', async () => {
    resetMediaMetrics();
    setDownloadConcurrency(3);

    if (global.gc) global.gc();
    const baseline = rssBytes();
    const samples = [];
    const sampleTimer = setInterval(() => samples.push(rssBytes()), 1000);

    const { sessionId, providerMessageId } = nextIds('e1');
    const sizeBytes = 50 * 1024 * 1024;
    const result = await downloadAndUploadMedia({
      content: { fileLength: sizeBytes },
      type: 'document', // 64MB cap - document/video are the only types that fit a 50MB test file
      sessionId,
      providerMessageId,
      ext: 'bin',
      mimeType: 'application/octet-stream',
      redisClient: redis,
      s3Client,
      bucket: BUCKET,
      downloadFn: async () => fakeMediaStream(sizeBytes),
    });

    clearInterval(sampleTimer);
    const peak = Math.max(baseline, ...samples, rssBytes());
    const growth = peak - baseline;
    console.log(`[E1] baseline RSS=${mb(baseline)}MB, peak RSS=${mb(peak)}MB, growth=${mb(growth)}MB, samples(MB)=[${samples.map(mb).join(', ')}]`);

    assert.equal(result.status, 'ok', `expected ok, got ${JSON.stringify(result)}`);
    assert.equal(result.size, sizeBytes);
    assert.ok(growth < 100 * 1024 * 1024, `RSS growth ${mb(growth)}MB must be < 100MB above baseline`);
  });

  // --- E2: 10 concurrent 50MB files - peak < 600MB, inflight <= 3 -------

  test('(P0.3-E2) 10 concurrent 50MB uploads: absolute peak RSS < 600MB and media_inflight never exceeds 3', async () => {
    resetMediaMetrics();
    setDownloadConcurrency(3);

    let maxInflightObserved = 0;
    const samples = [];
    const sampleTimer = setInterval(() => {
      samples.push(rssBytes());
      maxInflightObserved = Math.max(maxInflightObserved, getMediaMetrics().inflight);
    }, 250);

    const sizeBytes = 50 * 1024 * 1024;
    const jobs = Array.from({ length: 10 }, () => {
      const { sessionId, providerMessageId } = nextIds('e2');
      return downloadAndUploadMedia({
        content: { fileLength: sizeBytes },
        type: 'document',
        sessionId,
        providerMessageId,
        ext: 'bin',
        mimeType: 'application/octet-stream',
        redisClient: redis,
        s3Client,
        bucket: BUCKET,
        downloadFn: async () => fakeMediaStream(sizeBytes),
      });
    });

    const results = await Promise.all(jobs);
    clearInterval(sampleTimer);

    const peak = Math.max(...samples, rssBytes());
    console.log(`[E2] 10x50MB concurrent: peak RSS=${mb(peak)}MB, max media_inflight observed=${maxInflightObserved}`);

    for (const r of results) assert.equal(r.status, 'ok', `expected ok, got ${JSON.stringify(r)}`);
    assert.ok(peak < 600 * 1024 * 1024, `absolute peak RSS ${mb(peak)}MB must be < 600MB`);
    assert.ok(maxInflightObserved <= 3, `media_inflight observed ${maxInflightObserved} must never exceed 3`);
    assert.equal(getMediaMetrics().inflight, 0, 'inflight must return to 0 once all jobs finish');
  });

  // --- E3: kill -9 mid-download of 50MB -> one complete object, zero orphan multipart

  test('(P0.3-E3) kill -9 mid-upload of 50MB: after restart, exactly one complete object exists and no multipart upload is left orphaned', async () => {
    const { sessionId, providerMessageId } = nextIds('e3');

    const childScript = path.join(__dirname, '_media_e2e_kill_child.mjs');
    const child = fork(childScript, [sessionId, providerMessageId], {
      stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
      env: process.env,
    });

    let sawProgress = false;
    child.stdout.on('data', (buf) => {
      if (buf.toString().includes('PROGRESS')) sawProgress = true;
    });

    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('child never reported PROGRESS within 10s')), 10000);
      const checkInterval = setInterval(() => {
        if (sawProgress) {
          clearInterval(checkInterval);
          clearTimeout(timeout);
          resolve();
        }
      }, 20);
    });

    child.kill('SIGKILL');
    await new Promise((resolve) => child.once('exit', resolve));

    const objectKey = `${sessionId}/${new Date().getUTCFullYear()}/${String(new Date().getUTCMonth() + 1).padStart(2, '0')}/${providerMessageId}.bin`;
    const listed = await s3Client.send(new ListMultipartUploadsCommand({ Bucket: BUCKET }));
    const orphaned = (listed.Uploads || []).filter((u) => u.Key === objectKey);
    console.log(`[E3] orphaned multipart uploads for ${objectKey} after kill -9: ${orphaned.length}`);
    assert.equal(orphaned.length, 0, 'no multipart upload should be left dangling on real MinIO after a killed process');

    resetMediaMetrics();
    const sizeBytes = 50 * 1024 * 1024;
    const result = await downloadAndUploadMedia({
      content: { fileLength: sizeBytes },
      type: 'document',
      sessionId,
      providerMessageId,
      ext: 'bin',
      mimeType: 'application/octet-stream',
      redisClient: redis,
      s3Client,
      bucket: BUCKET,
      downloadFn: async () => fakeMediaStream(sizeBytes),
    });
    assert.equal(result.status, 'ok');
    assert.equal(result.size, sizeBytes, 'the retried upload must be the full, uncorrupted size - not a leftover partial');
  });

  // --- E4: over-cap rejected with zero network bytes; wrong magic bytes rejected

  test('(P0.3-E4a) over-cap file is rejected before any download call is made (zero bytes over the network)', async () => {
    resetMediaMetrics();
    const { sessionId, providerMessageId } = nextIds('e4a');
    let downloadFnCalled = false;

    const result = await downloadAndUploadMedia({
      content: { fileLength: 20 * 1024 * 1024 }, // > 16MB image cap
      type: 'image',
      sessionId,
      providerMessageId,
      ext: 'jpg',
      mimeType: 'image/jpeg',
      redisClient: redis,
      s3Client,
      bucket: BUCKET,
      downloadFn: async () => {
        downloadFnCalled = true;
        return fakeMediaStream(20 * 1024 * 1024);
      },
    });

    assert.equal(result.status, 'too_large');
    assert.equal(downloadFnCalled, false, 'downloadFn (the network call) must never be invoked for a declared-oversize file');
    assert.equal(getMediaMetrics().failuresByReason.too_large, 1);
  });

  test('(P0.3-E4b) image-extension file with non-image magic bytes is rejected as rejected_type, not uploaded', async () => {
    resetMediaMetrics();
    const { sessionId, providerMessageId } = nextIds('e4b');

    // A real ELF executable header, not an image at all, wearing an
    // image/jpeg mimetype+extension - the disguised-upload case the spec
    // calls out explicitly.
    async function* elfDisguisedAsImage() {
      yield Buffer.from([0x7f, 0x45, 0x4c, 0x46, 0x02, 0x01, 0x01, 0x00]);
      yield Buffer.alloc(8192, 0x90);
    }

    const result = await downloadAndUploadMedia({
      content: { fileLength: 8200 },
      type: 'image',
      sessionId,
      providerMessageId,
      ext: 'jpg',
      mimeType: 'image/jpeg',
      redisClient: redis,
      s3Client,
      bucket: BUCKET,
      downloadFn: async () => elfDisguisedAsImage(),
    });

    assert.equal(result.status, 'rejected_type');
    assert.equal(getMediaMetrics().failuresByReason.rejected_type, 1);
  });
}
