// gateway/src/media.js
// P0.3 (G3, disasters #16/#21) - media handling: declared-size pre-check,
// streaming download (no full-buffer), streaming multipart upload, a global
// download concurrency semaphore, per-store daily quota on redis-durable,
// magic-byte content-type verification, and the spec's object-key format.
//
// Deliberately does NOT import sessions.js and is not imported by it at
// module-eval time in a way that creates a cycle: sessions.js calls into
// this module's exported functions.

import { downloadContentFromMessage } from '@whiskeysockets/baileys';
import { Upload } from '@aws-sdk/lib-storage';
import { fileTypeFromBuffer } from 'file-type';
import { PassThrough } from 'node:stream';
import { once } from 'node:events';
import { logger } from './logger.js';

// ---------------------------------------------------------------------------
// Size caps (env-configurable, PROMPT_P0_DEEPSEEK_PROMPT.md P0.3 defaults).
// ---------------------------------------------------------------------------

const DEFAULT_CAPS_BYTES = {
  image: 16 * 1024 * 1024,
  audio: 16 * 1024 * 1024,
  video: 64 * 1024 * 1024,
  document: 64 * 1024 * 1024,
  sticker: 2 * 1024 * 1024,
};

const CAP_ENV_VAR = {
  image: 'MEDIA_MAX_BYTES_IMAGE',
  audio: 'MEDIA_MAX_BYTES_AUDIO',
  video: 'MEDIA_MAX_BYTES_VIDEO',
  document: 'MEDIA_MAX_BYTES_DOCUMENT',
  sticker: 'MEDIA_MAX_BYTES_STICKER',
};

export function capBytesFor(type, env = process.env) {
  const raw = env[CAP_ENV_VAR[type]];
  const n = raw !== undefined ? Number(raw) : NaN;
  if (Number.isFinite(n) && n > 0) return n;
  return DEFAULT_CAPS_BYTES[type] ?? null;
}

// downloadContentFromMessage's `type` argument uses Baileys' own media-type
// vocabulary, which differs from our normalized entry.type in one place:
// there is no distinct "sticker" download type in Baileys - stickers are
// image-family content (webp) and are downloaded with type 'sticker' too
// (Baileys does define 'sticker' as a valid MediaType - see
// node_modules/@whiskeysockets/baileys/lib/Utils/messages-media.js
// MEDIA_HKDF_KEY_MAPPING, which includes `sticker`), so this is a 1:1 map.
const BAILEYS_DOWNLOAD_TYPE = {
  image: 'image',
  audio: 'audio',
  video: 'video',
  document: 'document',
  sticker: 'sticker',
};

export function normalizeFileLength(fileLength) {
  if (fileLength === undefined || fileLength === null) return null;
  if (typeof fileLength === 'number') return fileLength;
  // protobufjs Long-like objects (fileLength?: number | Long | null)
  if (typeof fileLength.toNumber === 'function') return fileLength.toNumber();
  const n = Number(fileLength);
  return Number.isFinite(n) ? n : null;
}

// ---------------------------------------------------------------------------
// Object key: {session_id}/{YYYY}/{MM}/{provider_message_id}.{ext}
// (corrected from the pre-P0.3 sharwa-ai/{session}/{uuid}.{ext} format -
// see docs/P0_DEVIATIONS.md for the deviation record.)
// ---------------------------------------------------------------------------

export function mediaObjectKey({ sessionId, providerMessageId, ext, now = new Date() }) {
  const yyyy = String(now.getUTCFullYear());
  const mm = String(now.getUTCMonth() + 1).padStart(2, '0');
  return `${sessionId}/${yyyy}/${mm}/${providerMessageId}.${ext}`;
}

// ---------------------------------------------------------------------------
// Global download concurrency semaphore (env-configurable, default 3).
// ---------------------------------------------------------------------------

class Semaphore {
  constructor(max) {
    this.max = max;
    this.current = 0;
    this.queue = [];
  }

  acquire() {
    if (this.current < this.max) {
      this.current += 1;
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      this.queue.push(() => {
        this.current += 1;
        resolve();
      });
    });
  }

  release() {
    this.current -= 1;
    const next = this.queue.shift();
    if (next) next();
  }
}

let downloadSemaphore = new Semaphore(Number(process.env.MEDIA_DOWNLOAD_CONCURRENCY) || 3);

// Exposed for tests only - lets a test install a semaphore with a small max
// to prove the concurrency cap actually blocks a 4th download.
export function setDownloadConcurrency(max) {
  downloadSemaphore = new Semaphore(max);
}

// ---------------------------------------------------------------------------
// In-process metrics accounting (P0.3 spec §"مقاييس": media_bytes_total,
// media_inflight, media_failures_total{reason}, media_duration_seconds).
//
// This module owns the counters because it is the only place that knows the
// true inflight/byte/failure counts; wiring these onto an authenticated
// public `/metrics` Prometheus endpoint is P0.6 scope (that is where the
// whole `/metrics` HTTP surface + METRICS_TOKEN auth is built per the spec's
// own phase split) - see docs/P0_DEVIATIONS.md. `getMediaMetrics()` exists so
// P0.3's own acceptance tests (media_inflight <= 3 under concurrent load) can
// observe the real, live counters rather than re-deriving them.
// ---------------------------------------------------------------------------

let mediaMetrics = {
  inflight: 0,
  bytesTotal: 0,
  failuresByReason: Object.create(null),
  durationsMs: [],
};

export function getMediaMetrics() {
  return {
    inflight: mediaMetrics.inflight,
    bytesTotal: mediaMetrics.bytesTotal,
    failuresByReason: { ...mediaMetrics.failuresByReason },
    durationsMs: [...mediaMetrics.durationsMs],
  };
}

// Exposed for tests only - each test suite/process should start from a clean
// slate rather than accumulating counts across unrelated test cases.
export function resetMediaMetrics() {
  mediaMetrics = { inflight: 0, bytesTotal: 0, failuresByReason: Object.create(null), durationsMs: [] };
}

function recordFailure(reason) {
  mediaMetrics.failuresByReason[reason] = (mediaMetrics.failuresByReason[reason] || 0) + 1;
}

// ---------------------------------------------------------------------------
// Per-store daily quota, tracked in redis-durable. Atomic check+reserve via
// a single Lua script (same "fused Lua atomicity" pattern as ingest/dedupe.js
// - a plain GET-then-SET here would race under concurrent downloads for the
// same store).
// ---------------------------------------------------------------------------

const QUOTA_TTL_SECONDS = 2 * 24 * 60 * 60; // 2 days: safely outlives one UTC day

const RESERVE_SCRIPT = `
local key = KEYS[1]
local maxFiles = tonumber(ARGV[1])
local maxBytes = tonumber(ARGV[2])
local estBytes = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local files = tonumber(redis.call('HGET', key, 'files') or '0')
local bytes = tonumber(redis.call('HGET', key, 'bytes') or '0')
if (files + 1) > maxFiles or (bytes + estBytes) > maxBytes then
  return {0, files, bytes}
end
redis.call('HINCRBY', key, 'files', 1)
redis.call('HINCRBY', key, 'bytes', estBytes)
redis.call('EXPIRE', key, ttl)
return {1, files + 1, bytes + estBytes}
`;

const RELEASE_SCRIPT = `
local key = KEYS[1]
local deltaBytes = tonumber(ARGV[1])
redis.call('HINCRBY', key, 'files', -1)
redis.call('HINCRBY', key, 'bytes', -deltaBytes)
return redis.call('HMGET', key, 'files', 'bytes')
`;

function quotaKey(storeId, now = new Date()) {
  const day = now.toISOString().slice(0, 10);
  return `media_quota:${storeId}:${day}`;
}

export function quotaLimits(env = process.env) {
  const files = Number(env.MEDIA_QUOTA_DAILY_FILES);
  const bytes = Number(env.MEDIA_QUOTA_DAILY_BYTES);
  return {
    maxFiles: Number.isFinite(files) && files > 0 ? files : 500,
    maxBytes: Number.isFinite(bytes) && bytes > 0 ? bytes : 1024 * 1024 * 1024,
  };
}

export async function reserveQuota(redisClient, storeId, estimatedBytes, env = process.env) {
  const { maxFiles, maxBytes } = quotaLimits(env);
  const key = quotaKey(storeId);
  const [ok, files, bytes] = await redisClient.eval(
    RESERVE_SCRIPT,
    1,
    key,
    String(maxFiles),
    String(maxBytes),
    String(Math.max(0, Math.floor(estimatedBytes))),
    String(QUOTA_TTL_SECONDS),
  );
  return { ok: ok === 1, files, bytes };
}

export async function releaseQuota(redisClient, storeId, bytesDelta) {
  const key = quotaKey(storeId);
  await redisClient.eval(RELEASE_SCRIPT, 1, key, String(Math.floor(bytesDelta)));
}

// Adjust the byte reservation after the true size is known (actual size can
// differ slightly from the declared fileLength). Only ever called with the
// download already committed, so files count is untouched.
export async function adjustQuotaBytes(redisClient, storeId, bytesDelta) {
  if (!bytesDelta) return;
  const key = quotaKey(storeId);
  await redisClient.hincrby(key, 'bytes', Math.floor(bytesDelta));
}

// ---------------------------------------------------------------------------
// Magic-byte content-type verification (first ~4KB, via `file-type`).
// ---------------------------------------------------------------------------

const SNIFF_BYTES = 4100;

export function magicByteMatches(detected, type) {
  if (!detected) {
    // Some legitimate documents (plain text, csv, some older doc formats)
    // have no recognizable magic-byte signature at all. Only 'document' is
    // permissive here; every other declared type has a well-known
    // signature, so no detection on those is treated as a mismatch.
    return type === 'document';
  }
  const mime = detected.mime;
  if (type === 'image' || type === 'sticker') return mime.startsWith('image/');
  if (type === 'video') return mime.startsWith('video/');
  if (type === 'audio') {
    // WhatsApp voice notes (ogg/opus) and some audio are detected as
    // audio/* by file-type; a handful of containers (e.g. audio-only mp4)
    // are detected as video/mp4 by magic bytes alone.
    return mime.startsWith('audio/') || mime === 'video/mp4';
  }
  if (type === 'document') return true; // wide format range, no single family
  return true;
}

// ---------------------------------------------------------------------------
// Streaming download + streaming multipart upload, with cap enforcement,
// quota reservation, and magic-byte verification, all before the object is
// considered committed.
// ---------------------------------------------------------------------------

/**
 * @param {object} params
 * @param {object} params.content - the Baileys media content object
 *   ({mediaKey, directPath, url, fileLength, ...}), i.e. detectMedia(msg).content.
 * @param {'image'|'audio'|'video'|'document'|'sticker'} params.type
 * @param {string} params.sessionId
 * @param {string} params.providerMessageId
 * @param {string} params.ext
 * @param {string} params.mimeType - declared ContentType for the upload.
 * @param {object} params.redisClient - real ioredis client (redis-durable).
 * @param {string} [params.storeId] - quota scope; falls back to sessionId
 *   (see docs/P0_DEVIATIONS.md - the spec does not define tenant_id at the
 *   gateway layer, so session_id is the deviation fallback).
 * @param {object} params.s3Client - AWS SDK v3 S3Client (MinIO-compatible).
 * @param {string} params.bucket
 * @param {(content: object, type: string) => Promise<AsyncIterable<Buffer>>} [params.downloadFn]
 *   injected for tests; defaults to Baileys' downloadContentFromMessage.
 */
export async function downloadAndUploadMedia({
  content,
  type,
  sessionId,
  providerMessageId,
  ext,
  mimeType,
  redisClient,
  storeId,
  s3Client,
  bucket,
  downloadFn = downloadContentFromMessage,
}) {
  const cap = capBytesFor(type);
  const declaredLength = normalizeFileLength(content?.fileLength);
  if (cap != null && declaredLength != null && declaredLength > cap) {
    recordFailure('too_large');
    return { status: 'too_large', size: declaredLength, cap };
  }

  const scope = storeId || sessionId;
  const reserveEstimate = declaredLength != null ? declaredLength : (cap ?? DEFAULT_CAPS_BYTES[type] ?? 0);
  const quota = await reserveQuota(redisClient, scope, reserveEstimate);
  if (!quota.ok) {
    recordFailure('quota_exceeded');
    return { status: 'quota_exceeded', files: quota.files, bytes: quota.bytes };
  }

  await downloadSemaphore.acquire();
  mediaMetrics.inflight += 1;
  const startedAt = Date.now();
  let released = false;
  const releaseOnce = () => {
    if (!released) {
      released = true;
      mediaMetrics.inflight -= 1;
      mediaMetrics.durationsMs.push(Date.now() - startedAt);
      downloadSemaphore.release();
    }
  };

  try {
    const baileysType = BAILEYS_DOWNLOAD_TYPE[type] || type;
    const sourceStream = await downloadFn(content, baileysType);

    const objectKey = mediaObjectKey({ sessionId, providerMessageId, ext });
    const passthrough = new PassThrough();
    const uploader = new Upload({
      client: s3Client,
      params: { Bucket: bucket, Key: objectKey, Body: passthrough, ContentType: mimeType },
      partSize: 5 * 1024 * 1024,
      queueSize: 2,
    });
    const uploadDone = uploader.done();
    // Prevent an unhandled-rejection crash if we abort below before anyone
    // awaits uploadDone on the rejected path. This is a genuine best-effort
    // suppression (the real outcome is handled on the path that does await
    // uploadDone, a few lines down), so it still logs rather than swallowing
    // silently (H3: never fail silently, even on a defensive path).
    uploadDone.catch((err) => {
      logger.warn({ err: err.message, sessionId, providerMessageId }, '[media] background upload-promise rejection after abort (expected on the rejected/aborted path)');
    });

    let total = 0;
    let sniffBuf = Buffer.alloc(0);
    let sniffed = false;
    let rejected = null;

    for await (const rawChunk of sourceStream) {
      const chunk = Buffer.isBuffer(rawChunk) ? rawChunk : Buffer.from(rawChunk);
      total += chunk.length;
      if (cap != null && total > cap) {
        rejected = { status: 'too_large', size: total, cap };
        break;
      }
      if (!sniffed) {
        sniffBuf = Buffer.concat([sniffBuf, chunk]);
        if (sniffBuf.length >= SNIFF_BYTES) {
          sniffed = true;
          const detected = await fileTypeFromBuffer(sniffBuf);
          if (!magicByteMatches(detected, type)) {
            rejected = { status: 'rejected_type', detected: detected ? detected.mime : null };
            break;
          }
        }
      }
      // Respect backpressure (H4: no unbounded buffer growth). If the S3
      // multipart upload is slower than the source stream - a slow/loaded
      // MinIO, or partSize/queueSize limiting how fast lib-storage drains
      // us - PassThrough.write() returns false and, if ignored, its
      // internal buffer accumulates every unconsumed chunk with no bound,
      // silently reproducing the exact full-buffer-in-memory failure mode
      // P0.3 exists to eliminate. Pausing the source read here until
      // 'drain' throttles OUR OWN download loop to the upload's real
      // speed, which is the actual point of streaming.
      const canWriteMore = passthrough.write(chunk);
      if (!canWriteMore) {
        await once(passthrough, 'drain');
      }
    }

    if (!rejected && !sniffed) {
      // Stream ended before we accumulated SNIFF_BYTES - sniff whatever we got.
      const detected = sniffBuf.length > 0 ? await fileTypeFromBuffer(sniffBuf) : null;
      if (!magicByteMatches(detected, type)) {
        rejected = { status: 'rejected_type', detected: detected ? detected.mime : null };
      }
    }

    if (rejected) {
      passthrough.destroy(new Error(rejected.status));
      try {
        await uploader.abort();
      } catch (err) {
        // Best-effort: the object is already refused (rejected.status below),
        // so a failed abort doesn't change the outcome, but it's still
        // logged rather than swallowed (H3).
        logger.warn({ err: err.message, sessionId, providerMessageId, status: rejected.status }, '[media] upload abort failed (best-effort, non-fatal)');
      }
      await releaseQuota(redisClient, scope, reserveEstimate);
      recordFailure(rejected.status);
      return rejected;
    }

    passthrough.end();
    await uploadDone;

    const byteDelta = total - reserveEstimate;
    if (byteDelta !== 0) {
      await adjustQuotaBytes(redisClient, scope, byteDelta);
    }

    mediaMetrics.bytesTotal += total;
    return { status: 'ok', object_key: objectKey, size: total };
  } catch (err) {
    await releaseQuota(redisClient, scope, reserveEstimate).catch((releaseErr) => {
      logger.warn({ err: releaseErr.message, sessionId, providerMessageId }, '[media] quota release failed after a download/upload error (best-effort, non-fatal)');
    });
    recordFailure('failed');
    return { status: 'failed', error: err && err.message ? err.message : String(err) };
  } finally {
    releaseOnce();
  }
}
