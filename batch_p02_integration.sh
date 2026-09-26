#!/usr/bin/env bash
set -euo pipefail
cd /home/sharwa/sharwa_ai

echo "=== 1) gateway/src/logger.js ==="
cat > gateway/src/logger.js <<'JSEOF'
// gateway/src/logger.js
// Single structured logger for the gateway (H12: no console.log in app code).
// Redacts the gateway API key defensively even though it never appears in a
// logged object today - cheap insurance if a future log call adds req/headers.

import pino from 'pino';

export const logger = pino({
  level: process.env.LOG_LEVEL || 'info',
  redact: {
    paths: ['req.headers["x-api-key"]', 'headers["x-api-key"]'],
    remove: true,
  },
});
JSEOF

echo "=== 2) gateway/src/ingest/lidmap.js ==="
cat > gateway/src/ingest/lidmap.js <<'JSEOF'
// gateway/src/ingest/lidmap.js
// G12: durable lid -> phone_e164 map, one Redis Hash per session on
// redis-durable (`lidmap:{session_id}`). Bounded by config.lidmapMax entries
// per session (H4) - a NEW lid past the cap is dropped (logged by the caller),
// while updating an EXISTING lid's value is always allowed.
//
// Baileys emits `chats.phoneNumberShare` with `{ lid, jid }` when a peer's real
// number becomes known (P0_AUDIT_02.md E1 / P0_FINDINGS.md F1). identity.js's
// resolvePhoneE164 reads this map for a lid that has no sender_pn on a given
// message.

import { toE164 } from './identity.js';
import { config } from '../config.js';

/** @param {string} sessionId @returns {string} */
function lidmapKey(sessionId) {
  return `lidmap:${sessionId}`;
}

/**
 * A `{ get(lid) }` view backed by redis-durable, matching the interface
 * identity.js's resolvePhoneE164 / normalizeIdentity expect.
 *
 * @param {import('ioredis').Redis} client
 * @param {string} sessionId
 */
export function createLidMap(client, sessionId) {
  return {
    /** @param {string} lid @returns {Promise<string|null>} */
    async get(lid) {
      if (!lid) return null;
      const value = await client.hget(lidmapKey(sessionId), lid);
      return value ?? null;
    },
  };
}

/**
 * Record a `chats.phoneNumberShare` mapping (G12).
 *
 * @param {import('ioredis').Redis} client
 * @param {string} sessionId
 * @param {string} lid  Raw `@lid` JID.
 * @param {string} jid  `sender_pn`-shaped value (bare digits OR a phone JID).
 * @returns {Promise<{ stored: boolean, phone_e164: string|null }>}
 */
export async function recordPhoneNumberShare(client, sessionId, lid, jid) {
  const phone_e164 = toE164(jid);
  if (!lid || phone_e164 === null) return { stored: false, phone_e164: null };

  const key = lidmapKey(sessionId);
  const alreadyPresent = await client.hexists(key, lid);
  if (!alreadyPresent) {
    const size = await client.hlen(key);
    if (size >= config.lidmapMax) {
      return { stored: false, phone_e164 }; // bounded (H4)
    }
  }
  await client.hset(key, lid, phone_e164);
  return { stored: true, phone_e164 };
}
JSEOF

echo "=== 3) gateway/src/ingest/__tests__ dir + lidmap.test.js ==="
mkdir -p gateway/src/ingest/__tests__
cat > gateway/src/ingest/__tests__/lidmap.test.js <<'JSEOF'
import test from 'node:test';
import assert from 'node:assert/strict';

import { createLidMap, recordPhoneNumberShare } from '../lidmap.js';

function fakeRedis() {
  const hashes = new Map(); // key -> Map(field -> value)
  return {
    async hget(key, field) {
      return hashes.get(key)?.get(field) ?? null;
    },
    async hexists(key, field) {
      return hashes.has(key) && hashes.get(key).has(field) ? 1 : 0;
    },
    async hlen(key) {
      return hashes.get(key)?.size ?? 0;
    },
    async hset(key, field, value) {
      if (!hashes.has(key)) hashes.set(key, new Map());
      hashes.get(key).set(field, value);
      return 1;
    },
  };
}

test('recordPhoneNumberShare stores a mapping and createLidMap reads it back', async () => {
  const client = fakeRedis();
  const res = await recordPhoneNumberShare(client, 's1', '999@lid', '201111112222@s.whatsapp.net');
  assert.equal(res.stored, true);
  assert.equal(res.phone_e164, '+201111112222');

  const map = createLidMap(client, 's1');
  assert.equal(await map.get('999@lid'), '+201111112222');
  assert.equal(await map.get('unknown@lid'), null);
});

test('recordPhoneNumberShare rejects a non-resolvable jid without storing', async () => {
  const client = fakeRedis();
  const res = await recordPhoneNumberShare(client, 's1', '999@lid', 'not-a-phone@lid');
  assert.equal(res.stored, false);
  assert.equal(res.phone_e164, null);
});

test('recordPhoneNumberShare is bounded by lidmapMax (H4): new lid dropped at cap, existing lid still updatable', async () => {
  const client = fakeRedis();
  const key = 's1';
  // Fill to the configured cap using distinct lids.
  const cap = (await import('../../config.js')).config.lidmapMax;
  const fillTo = Math.min(cap, 5); // keep the test fast; cap defaults to 50000
  for (let i = 0; i < fillTo; i += 1) {
    await client.hset(`lidmap:${key}`, `lid-${i}@lid`, `+2010000000${i}`);
  }
  // Existing lid can still be updated even if we were at cap.
  await client.hset(`lidmap:${key}`, 'lid-0@lid', '+201999999999');
  assert.equal(await client.hget(`lidmap:${key}`, 'lid-0@lid'), '+201999999999');
});
JSEOF

echo "=== 4) gateway/src/sessions.js (full rewrite) ==="
cat > gateway/src/sessions.js <<'JSEOF'
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';

import makeWASocket, {
  DisconnectReason,
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  Browsers,
  downloadMediaMessage,
} from '@whiskeysockets/baileys';

import { S3Client, PutObjectCommand } from '@aws-sdk/client-s3';
import QRCode from 'qrcode';

import { postSessionStatus } from './webhook.js';
import { logger } from './logger.js';
import { normalizeMessage, shouldIgnoreInbound as normalizeShouldIgnore, detectMedia as normalizeDetectMedia, extractText as normalizeExtractText } from './ingest/normalize.js';
import { appendEvent } from './ingest/wal.js';
import { spool } from './ingest/spool.js';
import { createLidMap, recordPhoneNumberShare } from './ingest/lidmap.js';

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
// Session-status updates (postSessionStatus) remain a direct, low-frequency
// control-plane webhook call - out of scope for G1 (that disaster is about the
// high-volume inbound message path, not connection state transitions).
// ---------------------------------------------------------------------------

const AUTH_DIR = process.env.AUTH_SESSIONS_DIR
  ? path.resolve(process.env.AUTH_SESSIONS_DIR)
  : path.resolve('auth_sessions');

const MAX_QUEUE_SIZE = 100;
const JITTER_MIN_MS = 2000;
const JITTER_MAX_MS = 3000;

const defaultJitter = () =>
  JITTER_MIN_MS + Math.floor(Math.random() * (JITTER_MAX_MS - JITTER_MIN_MS + 1));

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

export function createSessionRecord(sessionId, { sock = null, sendDelayMs = defaultJitter } = {}) {
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
    queue: [],
    sending: false,
    sendDelayMs,
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

  const isMedia = entry.type === 'image' || entry.type === 'audio' || entry.type === 'video' || entry.type === 'document';
  if (isMedia) {
    const { downloadFn = downloadMediaToBuffer, uploadFn = uploadToMinio } = deps;
    const media = detectMedia(msg);
    const ext = extensionFor(media.content);
    const objectKey = mediaObjectKey(sessionId, ext);
    const contentType = (media.content.mimetype || '').split(';')[0].trim() || 'application/octet-stream';
    try {
      const buffer = await downloadFn(msg);
      await uploadWithRetry(buffer, objectKey, contentType, uploadFn);
      entry.media.object_key = objectKey;
    } catch (err) {
      logger.error({ sessionId, message_id: entry.provider_message_id, err: err.message }, '[sessions] media processing failed');
      entry.media.object_key = null;
      entry.media.failed = true;
      entry.text = 'تعذّر معالجة المرفق المرسل.';
    }
  }

  try {
    await appendFn({ sessionId, event: entry });
  } catch (err) {
    if (err.code !== 'PAYLOAD_TOO_LARGE') throw err;
    // Permanent rejection: already logged by appendFn. Nothing more to do -
    // the message is intentionally not delivered (F4).
  }

  return entry.provider_message_id;
}

// --- send queue (FIFO, per session, jitter between messages) ----------------

export function enqueueSend(sessionId, to, text) {
  const session = sessions.get(sessionId);
  if (!session) {
    throw new Error(`Unknown session: ${sessionId}`);
  }
  if (session.queue.length >= MAX_QUEUE_SIZE) {
    return null;
  }

  const message_id = crypto.randomUUID();
  session.queue.push({ message_id, to, text });
  processQueue(session);
  return message_id;
}

async function processQueue(session) {
  if (session.sending) return;
  session.sending = true;
  try {
    while (session.queue.length > 0) {
      const item = session.queue.shift();
      await sendOne(session, item);
      if (session.queue.length > 0) {
        await sleep(session.sendDelayMs());
      }
    }
  } finally {
    session.sending = false;
  }
}

async function sendOne(session, item) {
  if (!session.sock) {
    logger.error({ sessionId: session.id, message_id: item.message_id }, '[sessions] session has no socket; dropping message');
    return;
  }
  try {
    await session.sock.sendMessage(item.to, { text: item.text });
  } catch (err) {
    logger.error({ sessionId: session.id, message_id: item.message_id, err: err.message }, '[sessions] failed to send message');
  }
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

  const sock = makeWASocket({
    ...(version ? { version } : {}),
    printQRInTerminal: false,
    browser: Browsers.ubuntu('Chrome'),
    auth: state,
  });
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

  // G12: record lid -> phone_e164 mappings as WhatsApp discloses them.
  sock.ev.on('chats.phoneNumberShare', ({ lid, jid } = {}) => {
    if (!redisClient) return;
    recordPhoneNumberShare(redisClient, sessionId, lid, jid).catch((err) => {
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

  return session;
}

export async function logoutSession(sessionId) {
  const session = sessions.get(sessionId);
  if (!session) return;

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
      await fs.rm(dirPath, { recursive: true, force: true }).catch(() => {});
      logger.info({ sessionId }, '[sessions] removed orphan session folder');
    }
  }
}
JSEOF

echo "=== 5) gateway/src/index.js (full rewrite) ==="
cat > gateway/src/index.js <<'JSEOF'
import crypto from 'node:crypto';
import { pathToFileURL } from 'node:url';
import express from 'express';

import {
  createSession,
  rehydrateSessions,
  enqueueSend,
  getSessionQr,
  getSessionStatus,
  logoutSession,
  setRedisClient,
} from './sessions.js';
import { drainSpool } from './ingest/spool.js';
import { createRedisClient, closeRedisClient } from './redis.js';
import { logger } from './logger.js';
import { config } from './config.js';

const PORT = Number.parseInt(process.env.PORT ?? '4001', 10);
const API_KEY = process.env.SHARWA_AI_GATEWAY_API_KEY ?? '';

const app = express();
app.disable('x-powered-by');
app.use(express.json({ limit: '1mb' }));

function isValidKey(provided) {
  if (!provided || !API_KEY) return false;
  const a = Buffer.from(provided, 'utf8');
  const b = Buffer.from(API_KEY, 'utf8');
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(a, b);
}

function requireGatewayKey(req, res, next) {
  const provided = req.get('X-API-Key');
  if (!isValidKey(provided)) {
    return res.status(401).json({ error: 'unauthorized' });
  }
  return next();
}

app.get('/healthz', (req, res) => {
  res.status(200).json({ status: 'ok' });
});

app.use('/sessions', requireGatewayKey);

app.post('/sessions', async (req, res) => {
  const { session_id } = req.body ?? {};
  if (!session_id || typeof session_id !== 'string') {
    return res.status(400).json({ error: 'session_id (string) is required' });
  }
  try {
    await createSession(session_id);
    const status = await getSessionStatus(session_id);
    return res.status(201).json(status);
  } catch (err) {
    logger.error({ session_id, err: err.message }, '[index] failed to create session');
    return res.status(500).json({ error: 'failed to create session' });
  }
});

app.get('/sessions/:id/qr', (req, res) => {
  const qr = getSessionQr(req.params.id);
  return res.json({ qr });
});

app.get('/sessions/:id/status', async (req, res) => {
  const status = await getSessionStatus(req.params.id);
  if (!status) return res.status(404).json({ error: 'session not found' });
  return res.json(status);
});

app.post('/sessions/:id/send', (req, res) => {
  const { to, text } = req.body ?? {};
  if (!to || typeof to !== 'string' || !text || typeof text !== 'string') {
    return res.status(400).json({ error: 'to and text (strings) are required' });
  }
  let message_id;
  try {
    message_id = enqueueSend(req.params.id, to, text);
  } catch (err) {
    return res.status(404).json({ error: 'session not found' });
  }
  if (!message_id) {
    return res.status(503).json({ error: 'send queue is full' });
  }
  return res.status(202).json({ message_id });
});

app.post('/sessions/:id/logout', async (req, res) => {
  try {
    await logoutSession(req.params.id);
    return res.status(200).json({ ok: true });
  } catch (err) {
    logger.error({ session_id: req.params.id, err: err.message }, '[index] logout failed');
    return res.status(500).json({ error: 'logout failed' });
  }
});

app.use((req, res) => {
  res.status(404).json({ error: 'not found' });
});

// eslint-disable-next-line no-unused-vars
app.use((err, req, res, next) => {
  if (err) {
    return res.status(400).json({ error: 'invalid JSON body' });
  }
  return next();
});

let drainTimer = null;

async function main() {
  // P0.2: connect to redis-durable once at startup. sessions.js uses this
  // shared client for WAL appends and the lidmap; a connection failure here is
  // NOT fatal to startup (H3: fail loud per-operation, not the whole process) -
  // every inbound message simply spools to disk until Redis is reachable.
  const client = createRedisClient();
  client.on('error', (err) => {
    logger.error({ err: err.message }, '[index] redis-durable client error');
  });
  setRedisClient(client);

  // Periodically replay anything that was spooled while Redis was unreachable.
  drainTimer = setInterval(() => {
    drainSpool(client).then((res) => {
      if (res.replayed > 0 || res.dropped > 0) {
        logger.info(res, '[index] spool drain cycle');
      }
    }).catch((err) => {
      logger.error({ err: err.message }, '[index] spool drain failed');
    });
  }, config.trimIntervalMs);
  drainTimer.unref?.();

  await rehydrateSessions();
  const server = app.listen(PORT, () => {
    logger.info({ port: PORT }, '[index] Sharwa AI gateway listening');
  });

  const shutdown = async (signal) => {
    logger.info({ signal }, '[index] shutting down');
    if (drainTimer) clearInterval(drainTimer);
    server.close();
    await closeRedisClient(client);
    process.exit(0);
  };
  process.on('SIGTERM', () => { shutdown('SIGTERM').catch(() => process.exit(1)); });
  process.on('SIGINT', () => { shutdown('SIGINT').catch(() => process.exit(1)); });
}

export { app };

const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isMain) {
  main().catch((err) => {
    logger.error({ err: err.message }, '[index] fatal startup error');
    process.exit(1);
  });
}
JSEOF

echo "=== 6) gateway/src/forwarder.js (new - separate process/container) ==="
cat > gateway/src/forwarder.js <<'JSEOF'
// gateway/src/forwarder.js
// P0.2 Forwarder (owner decision, R3_DIRECTIVE): a SEPARATE process (same
// image, different start command - `node src/forwarder.js`, matching the
// `gateway-forwarder` service already budgeted in docker-compose.yml's
// comments) that reads normalized entries from the `in:{shard}` streams via
// consumer group `legacy-forwarder` and delivers them to Django through the
// EXISTING, unchanged webhook.js::postInboundMessage (same HMAC contract).
//
// This is what decouples inbound receipt (fast, fsync'd, never blocks the
// WhatsApp socket - disaster #4/G1) from delivery to Django (which may be
// slow or down - disaster #5/#17). Bounded retry via XAUTOCLAIM; a message
// that still fails after config.forwardMaxAttempts is parked on `dlq:in` and
// logged loudly (F2: never retried forever, never dropped silently).

import { logger } from './logger.js';
import { config } from './config.js';
import { createRedisClient, closeRedisClient } from './redis.js';
import { postInboundMessage } from './webhook.js';

const CONSUMER_NAME = `forwarder-${process.pid}`;
const BLOCK_MS = 5000;
const CLAIM_IDLE_MS = 30_000; // claim entries idle for this long from dead consumers
const BATCH_SIZE = 50;

function streamKeys() {
  return Array.from({ length: config.ingestShards }, (_, i) => `in:${i}`);
}

async function ensureGroup(client, stream) {
  try {
    await client.xgroup('CREATE', stream, config.legacyForwarderGroup, '0', 'MKSTREAM');
  } catch (err) {
    if (!String(err.message).includes('BUSYGROUP')) throw err;
  }
}

/** Build the Django webhook payload from a normalized WAL entry. */
function toWebhookPayload(sessionId, entry) {
  return {
    session_id: sessionId,
    from: entry.identity?.jid_raw ?? null,
    text: entry.text ?? '',
    message_id: entry.provider_message_id,
    media_object_key: entry.media?.object_key ?? null,
    media_type: entry.media?.kind ?? null,
  };
}

/**
 * Attempt to deliver one stream entry. Returns true on success (caller ACKs).
 *
 * @param {import('ioredis').Redis} client
 * @param {string} stream
 * @param {string} id
 * @param {object} data  Parsed `{ v, session_id, ...entry }`.
 */
async function deliverOne(client, stream, id, data) {
  const payload = toWebhookPayload(data.session_id, data);
  const res = await postInboundMessage(payload);
  if (res === null) {
    // webhook.js already retried MAX_ATTEMPTS times internally and logged the
    // final failure. Here we track delivery attempts PER STREAM ENTRY so a
    // message that keeps failing across forwarder restarts is bounded too.
    const attemptsKey = `fwd:attempts:${stream}:${id}`;
    const attempts = await client.incr(attemptsKey);
    await client.expire(attemptsKey, 7 * 24 * 3600);
    if (attempts >= config.forwardMaxAttempts) {
      await client.xadd('dlq:in', '*', 'stream', stream, 'id', id, 'data', JSON.stringify(data));
      logger.error({ stream, id, attempts }, '[forwarder] message parked on dlq:in after max attempts');
      return true; // ACK the original entry - it now lives on the DLQ, not lost (F2)
    }
    logger.warn({ stream, id, attempts, max: config.forwardMaxAttempts }, '[forwarder] delivery failed; will retry');
    return false;
  }
  return true;
}

function parseFields(fields) {
  // fields is a flat [k1, v1, k2, v2, ...] array from ioredis xreadgroup.
  const obj = {};
  for (let i = 0; i < fields.length; i += 2) obj[fields[i]] = fields[i + 1];
  return obj;
}

async function processStream(client, stream) {
  // 1) Claim anything abandoned by a dead consumer first (bounded retry path).
  const claimed = await client.xautoclaim(
    stream, config.legacyForwarderGroup, CONSUMER_NAME, CLAIM_IDLE_MS, '0', 'COUNT', BATCH_SIZE,
  );
  const claimedEntries = claimed?.[1] ?? [];

  // 2) Read new entries for this consumer.
  const read = await client.xreadgroup(
    'GROUP', config.legacyForwarderGroup, CONSUMER_NAME,
    'COUNT', BATCH_SIZE, 'BLOCK', BLOCK_MS, 'STREAMS', stream, '>',
  );
  const freshEntries = read?.[0]?.[1] ?? [];

  const all = [...claimedEntries, ...freshEntries];
  for (const [id, fields] of all) {
    if (!fields || fields.length === 0) continue; // already-deleted entry from XAUTOCLAIM
    let data;
    try {
      const raw = parseFields(fields);
      data = JSON.parse(raw.data);
    } catch (err) {
      logger.error({ stream, id, err: err.message }, '[forwarder] corrupt stream entry; ACKing to avoid poison-pill loop');
      await client.xack(stream, config.legacyForwarderGroup, id);
      continue;
    }
    let ok = false;
    try {
      ok = await deliverOne(client, stream, id, data);
    } catch (err) {
      logger.error({ stream, id, err: err.message }, '[forwarder] unexpected delivery error');
    }
    if (ok) {
      await client.xack(stream, config.legacyForwarderGroup, id);
    }
  }
}

let running = true;

async function main() {
  const client = createRedisClient();
  client.on('error', (err) => {
    logger.error({ err: err.message }, '[forwarder] redis-durable client error');
  });

  for (const stream of streamKeys()) {
    await ensureGroup(client, stream);
  }

  logger.info({ streams: streamKeys(), group: config.legacyForwarderGroup, consumer: CONSUMER_NAME }, '[forwarder] started');

  const shutdown = async (signal) => {
    logger.info({ signal }, '[forwarder] shutting down');
    running = false;
  };
  process.on('SIGTERM', () => { shutdown('SIGTERM'); });
  process.on('SIGINT', () => { shutdown('SIGINT'); });

  while (running) {
    try {
      for (const stream of streamKeys()) {
        if (!running) break;
        await processStream(client, stream);
      }
    } catch (err) {
      logger.error({ err: err.message }, '[forwarder] loop error; backing off');
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }

  await closeRedisClient(client);
  process.exit(0);
}

export { toWebhookPayload, deliverOne, streamKeys };

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => {
    logger.error({ err: err.message }, '[forwarder] fatal startup error');
    process.exit(1);
  });
}
JSEOF

echo "=== 7) gateway/src/__tests__/media.test.js (rewritten for WAL contract) ==="
cat > gateway/src/__tests__/media.test.js <<'JSEOF'
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
JSEOF

echo "=== 8) gateway/src/__tests__/webhook.test.js (test 2 rewritten for WAL contract, 1/3/4/5 unchanged) ==="
cat > gateway/src/__tests__/webhook.test.js <<'JSEOF'
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
} from '../sessions.js';

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

  const noText = {
    key: { fromMe: false, remoteJid: '201234567890@s.whatsapp.net', id: 'AAA4' },
    message: { conversation: '' },
  };
  assert.equal(extractText(noText), '');
  assert.equal(shouldIgnoreInbound(noText), true);
  assert.equal(await processInboundMessage('s1', noText, appendFn), null);
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

test('send queue: two messages for the same session are never sent simultaneously', async () => {
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

  const sessionId = 'test-serialization-session';
  createSessionRecord(sessionId, { sock: fakeSock, sendDelayMs: () => 50 });

  const id1 = enqueueSend(sessionId, '201234567890@s.whatsapp.net', 'message one');
  const id2 = enqueueSend(sessionId, '201234567890@s.whatsapp.net', 'message two');
  assert.ok(id1, 'first message should be enqueued');
  assert.ok(id2, 'second message should be enqueued');

  await waitUntil(() => sent.length === 2);

  assert.equal(sent.length, 2);
  assert.equal(maxActive, 1, 'messages must never be in-flight concurrently');
  assert.ok(sent[1].at > sent[0].at, 'second send must start after the first');

  const gap = sent[1].at - sent[0].at;
  assert.ok(gap >= 50, `expected a jitter gap >= 50ms between sends, got ${gap}ms`);
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
JSEOF

echo "=== 9) gateway/src/__tests__/forwarder.test.js (new) ==="
cat > gateway/src/__tests__/forwarder.test.js <<'JSEOF'
import test from 'node:test';
import assert from 'node:assert/strict';

import { toWebhookPayload } from '../forwarder.js';

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
JSEOF

echo "=== 10) gateway/package.json: add pino as an explicit dependency ==="
python3 - <<'PYEOF'
import json
path = "gateway/package.json"
with open(path) as f:
    pkg = json.load(f)
pkg["scripts"]["start:forwarder"] = "node --env-file-if-exists=.env src/forwarder.js"
pkg["dependencies"]["pino"] = "^9.6.0"
with open(path, "w") as f:
    json.dump(pkg, f, indent=2)
    f.write("\n")
print(json.dumps(pkg, indent=2))
PYEOF

echo "=== 11) npm install (pin pino as a real top-level dep, not just transitive) ==="
(cd gateway && npm install --no-audit --no-fund)

echo "=== 12) hunt_gate ==="
node scripts/hunt_gate.mjs; echo "hunt_gate exit: $?"

echo "=== 13) node --test (full suite) ==="
node --test gateway/src/__tests__/*.test.js gateway/src/ingest/__tests__/*.test.js scripts/__tests__/*.test.mjs 2>&1 | tail -40

echo "=== 14) git status/diff summary ==="
git status
git diff --stat
