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
import { appendEvent, dedupeKeyFor } from './ingest/wal.js';
import { markDone } from './ingest/dedupe.js';
import { spool } from './ingest/spool.js';
import { createLidMap, recordPhoneNumberShare, buildIdentityUpdateEvent } from './ingest/lidmap.js';
import { config } from './config.js';

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

  // G12: record lid -> phone_e164 mappings as WhatsApp discloses them, and
  // (R3_DIRECTIVE) append an identity_update WAL entry so a future P1 core
  // consumer can react to the resolution without re-deriving it from raw
  // messages. Mirrors processInboundMessage's spool-on-failure path (F2) via
  // the same defaultAppendToWal helper.
  sock.ev.on('chats.phoneNumberShare', ({ lid, jid } = {}) => {
    if (!redisClient) return;
    (async () => {
      const res = await recordPhoneNumberShare(redisClient, sessionId, lid, jid);
      if (!res.stored) return; // non-resolvable jid, or bounded out at lidmapMax (H4)

      const event = buildIdentityUpdateEvent(lid, res.phone_e164);
      const appendRes = await defaultAppendToWal({ sessionId, event });
      if (appendRes.status === 'appended') {
        // No separate "delivery confirmed" step exists for identity_update
        // (unlike messages, which wait for forwarder.js to reach Django) -
        // the entry is fully durable the moment it lands on the stream, so
        // extend its dedupe marker to the long TTL right away (G2-equivalent).
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
      await fs.rm(dirPath, { recursive: true, force: true }).catch((err) => {
        logger.warn({ sessionId, err: err.message }, '[sessions] failed to remove orphan session folder (non-fatal)');
      });
      logger.info({ sessionId }, '[sessions] removed orphan session folder');
    }
  }
}
