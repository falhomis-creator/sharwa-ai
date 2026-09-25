import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';

import {
  createWaSocket,
  DisconnectReason,
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  downloadMediaMessage,
} from './driver/waDriver.js';

import { S3Client, PutObjectCommand } from '@aws-sdk/client-s3';
import QRCode from 'qrcode';

import { postSessionStatus } from './webhook.js';
import { logger } from './logger.js';
import { normalizeMessage, shouldIgnoreInbound as normalizeShouldIgnore, detectMedia as normalizeDetectMedia, extractText as normalizeExtractText } from './ingest/normalize.js';
import { appendEvent, dedupeKeyFor } from './ingest/wal.js';
import { markDone } from './ingest/dedupe.js';
import { spool } from './ingest/spool.js';
import { createLidMap, recordPhoneNumberShare, buildIdentityUpdateEvent } from './ingest/lidmap.js';
import { config } from './config.js';
import { downloadAndUploadMedia } from './media.js';
import {
  enqueueSend as outboundEnqueueSend,
  startOutboundWorker,
  recoverInflight,
  queueDepth as outboundQueueDepth,
} from './outbound/queue.js';

// ---------------------------------------------------------------------------
// Tenant-isolated Baileys session manager.
//
// P0.2 change (R3_DIRECTIVE): inbound messages are no longer posted to Django
// synchronously from here (disaster #4/G1 - "a webhook hang blocks the model").
// processInboundMessage now normalizes the message (G4/G12) and appends it to
// the durable WAL (redis-durable, dedupe-protected, F4-capped). A separate
// process (forwarder.js) reads the WAL and delivers to Django with its own
// bounded retry. If the WAL append fails for any reason, the entry is spooled
// to disk (disaster #5/#17) rather than dropped (F2: never fail silently).
//
// P0.4 change (this file): the send path no longer uses an in-process array
// queue (createSessionRecord's old `queue`/`sending` fields, and the old
// processQueue/sendOne, existed only in RAM — any crash lost every message
// still waiting to send, exactly disaster #4/#5/#17 again but on the
// OUTBOUND side). Sending now goes through outbound/queue.js, a
// Redis-durable FIFO with idempotency, a per-number token bucket, and
// crash-recovery (recoverInflight, run once per session before its worker
// starts). See outbound/queue.js's own file header for the full design and
// its honestly-disclosed duplicate-window guarantee (H8).
//
// P0.4 change (this file): session sockets are now constructed via
// driver/waDriver.js's createWaSocket() instead of importing Baileys'
// makeWASocket directly — this is the WaDriver abstraction referenced (but
// never built) since P0.2 (closes docs/P0_OPEN_QUESTIONS.md OQ-7). In
// production this resolves to the exact same real Baileys socket as before;
// only P0.4/P0.5/P0.8's own tests select the fake one, and only under the
// triple env gate documented in driver/waDriver.js (H7).
//
// Session-status updates (postSessionStatus) remain a direct, low-frequency
// control-plane webhook call - out of scope for G1 (that disaster is about the
// high-volume inbound message path, not connection state transitions).
// ---------------------------------------------------------------------------

const AUTH_DIR = process.env.AUTH_SESSIONS_DIR
  ? path.resolve(process.env.AUTH_SESSIONS_DIR)
  : path.resolve('auth_sessions');

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** @type {Map<string, object>} sessionId -> session record */
const sessions = new Map();

/** Shared redis-durable client, set once by index.js at startup (H3: never
 * guessed - a null client means "not configured", and every write path treats
 * that as an immediate spool, never a silent drop). */
let redisClient = null;

/** @param {import('ioredis').Redis|null} client */
export function setRedisClient(client) {
  redisClient = client;
}

// --- pure helpers (exported for unit tests / kept for backward compat) -----

export function isGroupJid(jid) {
  return typeof jid === 'string' && (jid.endsWith('@g.us') || jid.endsWith('@broadcast'));
}

export const extractText = normalizeExtractText;
export const detectMedia = normalizeDetectMedia;
export const shouldIgnoreInbound = normalizeShouldIgnore;

// --- media handling (MinIO upload) ------------------------------------------

const MIME_EXTENSION_MAP = {
  'image/jpeg': 'jpg',
  'image/jpg': 'jpg',
  'image/png': 'png',
  'image/webp': 'webp',
  'image/gif': 'gif',
  'audio/ogg': 'ogg',
  'audio/opus': 'ogg',
  'audio/mpeg': 'mp3',
  'audio/mp4': 'm4a',
  'audio/aac': 'aac',
  'audio/x-wav': 'wav',
  'video/mp4': 'mp4',
  'video/3gpp': '3gp',
  'video/quicktime': 'mov',
  'application/pdf': 'pdf',
  'text/plain': 'txt',
  'application/zip': 'zip',
  'application/msword': 'doc',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
  'application/vnd.ms-excel': 'xls',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
};

function extensionFor(content) {
  const mime = (content.mimetype || '').split(';')[0].trim().toLowerCase();
  if (MIME_EXTENSION_MAP[mime]) return MIME_EXTENSION_MAP[mime];
  const fileName = content.fileName || content.filename || '';
  const ext = fileName.split('.').pop();
  if (ext && /^[a-z0-9]{1,10}$/i.test(ext)) return ext.toLowerCase();
  return 'bin';
}

export function mediaObjectKey(sessionId, ext) {
  return `sharwa-ai/${sessionId}/${crypto.randomUUID()}.${ext}`;
}

export async function downloadMediaToBuffer(msg) {
  return downloadMediaMessage(msg, 'buffer');
}

let _s3Client = null;

function getS3Client() {
  if (_s3Client) return _s3Client;
  const accessKeyId = process.env.MINIO_ACCESS_KEY;
  const secretAccessKey = process.env.MINIO_SECRET_KEY;
  if (!accessKeyId || !secretAccessKey) {
    throw new Error('MinIO is not configured (MINIO_ACCESS_KEY/MINIO_SECRET_KEY)');
  }
  let endpoint = process.env.MINIO_ENDPOINT_URL;
  const useSSL = (process.env.MINIO_USE_SSL ?? 'false') === 'true';
  if (endpoint && !/^https?:\/\//i.test(endpoint)) {
    endpoint = `${useSSL ? 'https' : 'http'}://${endpoint}`;
  }
  if (!endpoint) {
    throw new Error('MinIO is not configured (MINIO_ENDPOINT_URL)');
  }
  _s3Client = new S3Client({
    region: 'us-east-1',
    endpoint,
    credentials: { accessKeyId, secretAccessKey },
    forcePathStyle: true,
  });
  return _s3Client;
}

export async function uploadToMinio(buffer, objectKey, contentType) {
  const bucket = process.env.MINIO_BUCKET_NAME;
  if (!bucket) throw new Error('MINIO_BUCKET_NAME is not set');
  const client = getS3Client();
  await client.send(new PutObjectCommand({
    Bucket: bucket,
    Key: objectKey,
    Body: buffer,
    ContentType: contentType || 'application/octet-stream',
  }));
  return objectKey;
}

const UPLOAD_MAX_ATTEMPTS = 3;
const UPLOAD_BASE_DELAY_MS = 500;

async function uploadWithRetry(buffer, objectKey, contentType, uploadFn) {
  let lastError;
  for (let attempt = 1; attempt <= UPLOAD_MAX_ATTEMPTS; attempt += 1) {
    try {
      return await uploadFn(buffer, objectKey, contentType);
    } catch (err) {
      lastError = err;
      if (attempt < UPLOAD_MAX_ATTEMPTS) {
        const delay = UPLOAD_BASE_DELAY_MS * 2 ** (attempt - 1);
        logger.warn({ err: err.message, attempt, maxAttempts: UPLOAD_MAX_ATTEMPTS, delay }, '[sessions] MinIO upload attempt failed; retrying');
        await sleep(delay);
      }
    }
  }
  throw lastError;
}

export function mapConnectionStatus(update) {
  if (update.qr) return 'QR_PENDING';
  if (update.connection === 'open') return 'CONNECTED';
  if (update.connection === 'close') {
    const statusCode = update.lastDisconnect?.error?.output?.statusCode;
    if (statusCode === DisconnectReason.forbidden) {
      return 'BANNED';
    }
    return 'DISCONNECTED';
  }
  return 'UNKNOWN';
}

function extractPhoneNumber(userId) {
  if (!userId) return null;
  const match = String(userId).match(/^(\d+)/);
  return match ? match[1] : null;
}

// --- session registry -------------------------------------------------------

export function createSessionRecord(sessionId, { sock = null } = {}) {
  const existing = sessions.get(sessionId);
  if (existing) return existing;

  const session = {
    id: sessionId,
    sock,
    status: 'UNKNOWN',
    phoneNumber: null,
    lastQr: null,
    authPath: null,
    state: null,
    saveCreds: null,
    // P0.4: outbound worker handle for this session (null until the durable
    // send queue's worker is started - see startSessionOutboundWorker below).
    outboundWorker: null,
  };
  sessions.set(sessionId, session);
  return session;
}

export function getSession(sessionId) {
  return sessions.get(sessionId) ?? null;
}

// --- inbound handling (P0.2: normalize -> WAL, spool on failure) -----------

/**
 * Default append path: write the normalized record to the durable WAL. Any
 * failure (Redis down, unexpected error) is spooled to disk rather than
 * dropped (F2). A PAYLOAD_TOO_LARGE rejection is permanent (F4) - logged and
 * NOT spooled, since it can never fit regardless of retries.
 *
 * @param {{ sessionId: string, event: object }} record
 */
async function defaultAppendToWal(record) {
  if (!redisClient) {
    // No redis client configured (unit tests, or startup race): spool.
    await spool(record);
    return { status: 'spooled' };
  }
  try {
    return await appendEvent(redisClient, record);
  } catch (err) {
    if (err.code === 'PAYLOAD_TOO_LARGE') {
      logger.error({ sessionId: record.sessionId, err: err.message }, '[sessions] message rejected: payload too large (F4)');
      throw err;
    }
    logger.error({ sessionId: record.sessionId, err: err.message }, '[sessions] WAL append failed; spooling (disaster #5/#17)');
    await spool(record);
    return { status: 'spooled' };
  }
}

/**
 * Normalize an inbound Baileys message (G4/G12), resolve media if present,
 * and append it to the WAL. Returns the provider_message_id, or null when the
 * message was filtered (fromMe/group/broadcast/no content).
 *
 * @param {string} sessionId
 * @param {object} msg
 * @param {(record: { sessionId: string, event: object }) => Promise<unknown>} [appendFn]
 * @param {{ downloadFn?: Function, uploadFn?: Function, lidMap?: object }} [deps]
 * @returns {Promise<string|null>}
 */
export async function processInboundMessage(sessionId, msg, appendFn = defaultAppendToWal, deps = {}) {
  const lidMap = deps.lidMap ?? (redisClient ? createLidMap(redisClient, sessionId) : undefined);
  const entry = normalizeMessage(msg, { lidMap });
  if (entry === null) return null;

  const isMedia = entry.type === 'image' || entry.type === 'audio' || entry.type === 'video' || entry.type === 'document' || entry.type === 'sticker';
  if (isMedia) {
    const media = detectMedia(msg);
    const ext = extensionFor(media.content);
    const contentType = (media.content.mimetype || '').split(';')[0].trim() || 'application/octet-stream';

    if (deps.downloadFn || deps.uploadFn) {
      const { downloadFn = downloadMediaToBuffer, uploadFn = uploadToMinio } = deps;
      const objectKey = mediaObjectKey(sessionId, ext);
      try {
        const buffer = await downloadFn(msg);
        await uploadWithRetry(buffer, objectKey, contentType, uploadFn);
        entry.media.object_key = objectKey;
        entry.media.status = 'ok';
      } catch (err) {
        logger.error({ sessionId, message_id: entry.provider_message_id, err: err.message }, '[sessions] media processing failed');
        entry.media.object_key = null;
        entry.media.failed = true;
        entry.media.status = 'failed';
        entry.text = 'تعذّر معالجة المرفق المرسل.';
      }
    } else {
      try {
        if (!redisClient) throw new Error('redis-durable client not configured');
        const result = await downloadAndUploadMedia({
          content: media.content,
          type: entry.type,
          sessionId,
          providerMessageId: entry.provider_message_id,
          ext,
          mimeType: contentType,
          redisClient,
          s3Client: getS3Client(),
          bucket: process.env.MINIO_BUCKET_NAME,
          downloadFn: deps.mediaDownloadFn,
        });
        entry.media.status = result.status;
        if (result.status === 'ok') {
          entry.media.object_key = result.object_key;
        } else {
          entry.media.object_key = null;
          entry.media.failed = true;
          if (result.size !== undefined) entry.media.size = result.size;
          entry.text = 'تعذّر معالجة المرفق المرسل.';
          logger.warn({ sessionId, message_id: entry.provider_message_id, status: result.status }, '[sessions] media rejected (P0.3)');
        }
      } catch (err) {
        logger.error({ sessionId, message_id: entry.provider_message_id, err: err.message }, '[sessions] media processing failed');
        entry.media.object_key = null;
        entry.media.failed = true;
        entry.media.status = 'failed';
        entry.text = 'تعذّر معالجة المرفق المرسل.';
      }
    }
  }

  try {
    await appendFn({ sessionId, event: entry });
  } catch (err) {
    if (err.code !== 'PAYLOAD_TOO_LARGE') throw err;
  }

  return entry.provider_message_id;
}

// --- send queue (P0.4: durable, Redis-backed — see outbound/queue.js) ------

/**
 * Enqueue an outbound message durably (P0.4). Idempotent on `clientMsgId`
 * (auto-generated if the caller does not supply one — a caller that wants
 * true idempotency across its own retries must pass the same clientMsgId
 * each time).
 *
 * @param {string} sessionId
 * @param {string} to
 * @param {string} text
 * @param {{ clientMsgId?: string, kind?: 'interactive'|'bulk'|'marketing' }} [opts]
 * @returns {Promise<{ status: 'queued'|'duplicate'|'full', client_msg_id?: string, state?: string }>}
 */
export async function enqueueSend(sessionId, to, text, opts = {}) {
  const session = sessions.get(sessionId);
  if (!session) {
    throw new Error(`Unknown session: ${sessionId}`);
  }
  if (!redisClient) {
    // P0.4's whole point is a durable queue; without redis-durable there is
    // nowhere safe to put the message (H3: fail loud, never pretend to queue
    // something that only lives in RAM again).
    const err = new Error('redis-durable client not configured; cannot durably enqueue');
    err.code = 'REDIS_NOT_CONFIGURED';
    throw err;
  }
  const clientMsgId = opts.clientMsgId ?? crypto.randomUUID();
  const res = await outboundEnqueueSend(redisClient, { sessionId, clientMsgId, to, text, kind: opts.kind });
  if (res.status === 'full') return { status: 'full' };
  if (res.status === 'duplicate') return { status: 'duplicate', client_msg_id: clientMsgId, state: res.state };
  return { status: 'queued', client_msg_id: clientMsgId };
}

/**
 * Start (or restart) this session's outbound worker. Called once the
 * session's socket exists, after crash-recovery has run. Idempotent: calling
 * it twice for a session that already has a running worker stops the old one
 * first (used when a session reconnects with a new socket).
 *
 * @param {object} session
 */
export function startSessionOutboundWorker(session) {
  if (!redisClient) return; // nothing to run against; enqueueSend already refuses without redis
  if (session.outboundWorker) {
    session.outboundWorker.stop();
  }
  session.outboundWorker = startOutboundWorker(redisClient, session.id, {
    getSocket: () => (session.status === 'CONNECTED' ? session.sock : null),
  });
}

/** @returns {Promise<number>} current outbound queue depth for a session (P0.5's health endpoint will surface this). */
export async function getOutboundQueueDepth(sessionId) {
  if (!redisClient) return 0;
  return outboundQueueDepth(redisClient, sessionId);
}

// --- query helpers for index.js --------------------------------------------

export async function toQrImageBase64(rawQr) {
  if (!rawQr) return null;
  const dataUrl = await QRCode.toDataURL(rawQr, { type: 'image/png' });
  return dataUrl.replace(/^data:image\/png;base64,/, '');
}

export function getSessionQr(sessionId) {
  const session = sessions.get(sessionId);
  return session ? session.lastQr : null;
}

export async function getSessionStatus(sessionId) {
  const session = sessions.get(sessionId);
  if (!session) return null;
  return {
    status: session.status,
    qr_image_base64: await toQrImageBase64(session.lastQr),
    connected_phone_number: session.phoneNumber,
  };
}

// --- session lifecycle ------------------------------------------------------

export async function createSession(sessionId) {
  const existing = sessions.get(sessionId);
  if (existing) return existing;

  const session = createSessionRecord(sessionId);
  const authPath = path.join(AUTH_DIR, sessionId);
  await fs.mkdir(authPath, { recursive: true });
  session.authPath = authPath;

  const { state, saveCreds } = await useMultiFileAuthState(authPath);
  session.state = state;
  session.saveCreds = saveCreds;

  let version;
  try {
    ({ version } = await fetchLatestBaileysVersion());
  } catch (err) {
    logger.warn({ err: err.message }, '[sessions] could not fetch latest Baileys version; using library default');
  }

  const sock = await createWaSocket({ authState: { state, saveCreds }, version });
  session.sock = sock;

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (update) => {
    if (update.qr) session.lastQr = update.qr;
    session.status = mapConnectionStatus(update);
    if (session.status === 'CONNECTED') {
      session.phoneNumber = extractPhoneNumber(sock.user?.id);
    }

    postSessionStatus({
      session_id: sessionId,
      status: session.status,
      phone_number: session.phoneNumber,
      detail: update.lastDisconnect?.error?.message ?? update.connection ?? null,
    }).catch((err) => {
      logger.error({ sessionId, err: err.message }, '[sessions] session-status webhook threw unexpectedly');
    });
  });

  sock.ev.on('chats.phoneNumberShare', ({ lid, jid } = {}) => {
    if (!redisClient) return;
    (async () => {
      const res = await recordPhoneNumberShare(redisClient, sessionId, lid, jid);
      if (!res.stored) return;

      const event = buildIdentityUpdateEvent(lid, res.phone_e164);
      const appendRes = await defaultAppendToWal({ sessionId, event });
      if (appendRes.status === 'appended') {
        await markDone(redisClient, dedupeKeyFor(sessionId, event.provider_message_id), config.dedupeDoneTtlS * 1000);
      }
    })().catch((err) => {
      logger.error({ sessionId, err: err.message }, '[sessions] failed to record phoneNumberShare (G12)');
    });
  });

  sock.ev.on('messages.upsert', ({ messages, type }) => {
    if (type !== 'notify') return;
    for (const msg of messages) {
      processInboundMessage(sessionId, msg).catch((err) => {
        logger.error({ sessionId, err: err.message }, '[sessions] inbound message processing failed');
      });
    }
  });

  // P0.4: crash-recovery MUST run before the worker starts, so any item left
  // in `inflight` from a previous process (crashed mid-send) is resolved
  // (confirmed-sent cleanup, or requeued) before new sends can interleave
  // with it.
  if (redisClient) {
    await recoverInflight(redisClient, sessionId);
  }
  startSessionOutboundWorker(session);

  return session;
}

export async function logoutSession(sessionId) {
  const session = sessions.get(sessionId);
  if (!session) return;

  if (session.outboundWorker) {
    session.outboundWorker.stop();
  }

  try {
    if (session.sock) {
      await session.sock.logout();
    }
  } catch (err) {
    logger.error({ sessionId, err: err.message }, '[sessions] logout failed');
  }

  sessions.delete(sessionId);
  const authPath = session.authPath ?? path.join(AUTH_DIR, sessionId);
  await fs.rm(authPath, { recursive: true, force: true });
  logger.info({ sessionId }, '[sessions] logged out and removed session');
}

export async function rehydrateSessions() {
  let entries;
  try {
    entries = await fs.readdir(AUTH_DIR, { withFileTypes: true });
  } catch {
    return;
  }

  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const sessionId = entry.name;
    const dirPath = path.join(AUTH_DIR, sessionId);
    const credsPath = path.join(dirPath, 'creds.json');

    let registered = false;
    try {
      const raw = await fs.readFile(credsPath, 'utf8');
      const creds = JSON.parse(raw);
      registered = creds?.registered === true;
    } catch {
      registered = false;
    }

    if (registered) {
      try {
        await createSession(sessionId);
        logger.info({ sessionId }, '[sessions] rehydrated registered session');
      } catch (err) {
        logger.error({ sessionId, err: err.message }, '[sessions] failed to rehydrate');
      }
    } else {
      await fs.rm(dirPath, { recursive: true, force: true }).catch((err) => {
        logger.warn({ sessionId, err: err.message }, '[sessions] failed to remove orphan session folder (non-fatal)');
      });
      logger.info({ sessionId }, '[sessions] removed orphan session folder');
    }
  }
}
