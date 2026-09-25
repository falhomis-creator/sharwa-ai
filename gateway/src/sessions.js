import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';

import {
  createWaSocket,
  DisconnectReason,
  useMultiFileAuthState,
  downloadMediaMessage,
} from './driver/waDriver.js';

import { S3Client, PutObjectCommand } from '@aws-sdk/client-s3';
import QRCode from 'qrcode';

import { postSessionStatus } from './webhook.js';
import { logger } from './logger.js';
import { normalizeMessage, shouldIgnoreInbound as normalizeShouldIgnore, detectMedia as normalizeDetectMedia, extractText as normalizeExtractText } from './ingest/normalize.js';
import { normalizeIdentity } from './ingest/identity.js';
import { appendEvent, dedupeKeyFor } from './ingest/wal.js';
import { markDone } from './ingest/dedupe.js';
import { spool, spoolStatus } from './ingest/spool.js';
import { createLidMap, recordPhoneNumberShare, buildIdentityUpdateEvent } from './ingest/lidmap.js';
import { config } from './config.js';
import { downloadAndUploadMedia } from './media.js';
import {
  enqueueSend as outboundEnqueueSend,
  startOutboundWorker,
  recoverInflight,
  queueDepth as outboundQueueDepth,
  _keys as outboundKeys,
} from './outbound/queue.js';
import { acquireLease, renewLease, releaseLease, getLeaseHolder, isLeaseOwner } from './lease.js';
import { createRedisClient, closeRedisClient } from './redis.js';

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

/** Uniform random integer in [min, max] (P0.5: reconnect/rehydrate jitter). */
function jitter(min, max) {
  return min + Math.floor(Math.random() * (max - min + 1));
}

/**
 * P0.5 fix (found via this phase's own rehydrate/dual-instance tests, see
 * docs/P0_FINDINGS.md): ioredis multiplexes every command over ONE TCP
 * connection per client instance, and a blocking command (BLMOVE, used by
 * the outbound worker's poll loop) occupies that connection until it
 * returns. Every session's outbound worker used to share the single
 * `redisClient` used for lease ops / WAL appends / etc — with N sessions
 * running, N concurrent BLMOVE loops on one connection serialize (and,
 * eventually, time out) every other command queued behind them. Each
 * outbound worker now gets its own dedicated connection, resolved once it
 * reaches 'ready' so the worker's first BLMOVE doesn't have to fail-and-
 * retry against a not-yet-connected client under enableOfflineQueue:false.
 */
function waitForRedisReady(client) {
  if (client.status === 'ready') return Promise.resolve();
  return new Promise((resolve, reject) => {
    const onReady = () => { client.off('error', onError); resolve(); };
    const onError = (err) => { client.off('ready', onReady); reject(err); };
    client.once('ready', onReady);
    client.once('error', onError);
  });
}

/** Stops a session's outbound worker AND closes its dedicated Redis connection (see waitForRedisReady's comment above). Safe to call when no worker is running. */
async function stopOutboundWorker(session) {
  if (session.outboundWorker) {
    session.outboundWorker.stop();
    session.outboundWorker = null;
  }
  if (session.outboundWorkerClient) {
    const client = session.outboundWorkerClient;
    session.outboundWorkerClient = null;
    await closeRedisClient(client);
  }
}

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

/**
 * P0.5 (G6): classify a connection-close event into a reconnect ACTION,
 * separate from `mapConnectionStatus` (whose external `status` vocabulary is
 * frozen by contract - see that function's own comment). Compared by NAME
 * against the real `DisconnectReason` enum from driver/waDriver.js (the
 * actual installed Baileys version), never by guessed numeric literal.
 *
 * @param {object} update  The raw Baileys `connection.update` payload.
 * @returns {'restart'|'logged_out'|'banned'|'conflict'|'backoff'}
 */
export function classifyDisconnect(update) {
  const statusCode = update?.lastDisconnect?.error?.output?.statusCode;
  if (statusCode === DisconnectReason.restartRequired) return 'restart';
  if (statusCode === DisconnectReason.loggedOut) return 'logged_out';
  if (statusCode === DisconnectReason.forbidden) return 'banned';
  if (statusCode === DisconnectReason.connectionReplaced) return 'conflict';
  return 'backoff';
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
    // P0.5: this worker's OWN dedicated Redis connection (never the shared
    // `redisClient`) - see waitForRedisReady's comment near the top of this
    // file for why.
    outboundWorkerClient: null,
    // --- P0.5: lease + reconnect state (GET /sessions/:id/health surfaces this) ---
    fencingToken: null,       // set once this instance holds the lease
    leaseRenewTimer: null,
    reconnectTimer: null,
    reconnectAttempts: 0,
    lastDisconnectReason: null,
    since: Date.now(),        // when `status` last changed
    stopping: false,          // true once logoutSession/shutdown has started - suppresses reconnect
    authLoaded: false,        // true once useMultiFileAuthState has run once for this process
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

// --- human takeover signal (P0.5, G8, disaster #11) -------------------------
//
// A `fromMe:true` message is Baileys reporting a message sent FROM the
// business's own WhatsApp - normally that is just the gateway's own echo of a
// send it just made. `shouldIgnoreInbound` (ingest/normalize.js) is correct
// to drop it from the ordinary customer-message pipeline either way, since it
// is never itself a customer message.
//
// But when it did NOT come from this gateway - a human agent typed a reply
// directly on the merchant's phone - the system needs to know, so a bot
// reply never talks over a human mid-conversation (disaster #11). The only
// reliable signal (spec, literal) is: is this message's id already
// pre-registered in `sent_ids:{sid}`? outbound/queue.js's processOne()
// writes that key BEFORE calling sendMessage() (P0.4-3) specifically so this
// check is race-free even if the fromMe echo arrives before our own
// sendMessage() call returns.
/**
 * @param {string} sessionId
 * @param {object} msg  Raw Baileys message with `key.fromMe === true`.
 * @param {(record: { sessionId: string, event: object }) => Promise<unknown>} [appendFn]
 * @param {{ lidMap?: object }} [deps]
 * @returns {Promise<'bot_echo'|'human_takeover'|'ignored'>}
 */
export async function processFromMeMessage(sessionId, msg, appendFn = defaultAppendToWal, deps = {}) {
  const waId = msg?.key?.id;
  if (!waId || isGroupJid(msg?.key?.remoteJid) || !msg?.message) return 'ignored';

  if (redisClient) {
    const isOurs = await redisClient.exists(outboundKeys.sentIdKey(sessionId, waId));
    if (isOurs) return 'bot_echo'; // our own send's echo - expected, not a signal
  }

  const text = extractText(msg);
  const lidMap = deps.lidMap ?? (redisClient ? createLidMap(redisClient, sessionId) : undefined);
  // The customer is the chat this was sent TO (remoteJid), not a sender - a
  // fromMe message has no meaningful senderPn of its own.
  const identity = normalizeIdentity({ remoteJid: msg.key.remoteJid, senderPn: undefined, lidMap });

  const event = {
    provider_message_id: waId,
    type: 'human_takeover_signal',
    ts: Date.now(),
    identity,
    text: text || '',
    direction: 'outbound_human',
  };

  try {
    await appendFn({ sessionId, event });
  } catch (err) {
    if (err.code !== 'PAYLOAD_TOO_LARGE') throw err;
  }
  logger.info({ sessionId, message_id: waId }, '[sessions] human_takeover_signal recorded (G8)');
  return 'human_takeover';
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
 * P0.5 fix: runs against a DEDICATED Redis connection (never the shared
 * `redisClient`) — see the comment above `waitForRedisReady`. `redisClient`
 * itself is still used only to gate "is Redis configured at all", matching
 * every other call site's null-check convention.
 *
 * @param {object} session
 */
export async function startSessionOutboundWorker(session) {
  if (!redisClient) return; // nothing to run against; enqueueSend already refuses without redis
  await stopOutboundWorker(session);
  const client = createRedisClient();
  try {
    await waitForRedisReady(client);
  } catch (err) {
    logger.error({ sessionId: session.id, err: err.message }, '[sessions] dedicated outbound-worker Redis connection failed to become ready');
    await closeRedisClient(client);
    return;
  }
  session.outboundWorkerClient = client;
  session.outboundWorker = startOutboundWorker(client, session.id, {
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

// --- session lifecycle (P0.5: lease-gated, reconnecting) --------------------
//
// P0.5 change (G6/G7): a session's socket is no longer created once and left
// alone. `openSocketForSession` is now called both on first start AND on
// every reconnect, and is gated by a Redis lease (`lease.js`) so at most one
// gateway instance ever holds a real socket (and writes creds.json) for a
// given session at a time - see docs/P0_DEVIATIONS.md for the design.
//
// Without a configured redisClient (unit tests, or a deliberately
// single-instance dev run), lease coordination is skipped entirely and a
// session simply opens its socket directly - this preserves every P0.2-P0.4
// test's existing no-redis behavior unchanged.

async function ensureAuthLoaded(session, sessionId) {
  if (session.authLoaded) return;
  const authPath = path.join(AUTH_DIR, sessionId);
  await fs.mkdir(authPath, { recursive: true });
  session.authPath = authPath;

  const { state, saveCreds } = await useMultiFileAuthState(authPath);
  session.state = state;

  // P0.5 (spec, literal): a creds write verifies lease ownership first. With
  // no redisClient configured there is no lease to check - write through
  // unconditionally (matches pre-P0.5 / single-instance behavior).
  session.saveCreds = async () => {
    if (redisClient && session.fencingToken !== null) {
      const owns = await isLeaseOwner(redisClient, sessionId, config.instanceId, session.fencingToken);
      if (!owns) {
        logger.warn({ sessionId }, '[sessions] dropped a creds write: lease no longer owned by this instance (P0.5)');
        return;
      }
    }
    await saveCreds();
  };
  session.authLoaded = true;
}

function wireSocketEvents(session, sock, sessionId) {
  sock.ev.on('creds.update', session.saveCreds);

  sock.ev.on('connection.update', (update) => {
    if (update.qr) session.lastQr = update.qr;
    const prevStatus = session.status;
    session.status = mapConnectionStatus(update); // contract-frozen vocabulary - unchanged by P0.5
    if (session.status !== prevStatus) session.since = Date.now();

    if (session.status === 'CONNECTED') {
      session.phoneNumber = extractPhoneNumber(sock.user?.id);
      session.reconnectAttempts = 0;
    }

    postSessionStatus({
      session_id: sessionId,
      status: session.status,
      phone_number: session.phoneNumber,
      detail: update.lastDisconnect?.error?.message ?? update.connection ?? null,
    }).catch((err) => {
      logger.error({ sessionId, err: err.message }, '[sessions] session-status webhook threw unexpectedly');
    });

    if (update.connection !== 'close' || session.stopping) return;

    // --- P0.5 (G6): reconnect state machine ---------------------------------
    const action = classifyDisconnect(update);
    session.lastDisconnectReason = action;

    if (action === 'logged_out') {
      // Terminal: creds are invalid. Never reconnect; wipe the auth folder so
      // a stale session cannot be mistakenly rehydrated later.
      logger.info({ sessionId }, '[sessions] LOGGED_OUT - clearing creds, not reconnecting');
      finalizeSessionExit(session, sessionId, { wipeAuth: true }).catch((err) => {
        logger.error({ sessionId, err: err.message }, '[sessions] cleanup after LOGGED_OUT failed');
      });
      return;
    }
    if (action === 'banned') {
      // Terminal: the postSessionStatus call above already emitted the
      // BANNED status event (spec: "BANNED تصدر الحدث") - just stop here.
      logger.warn({ sessionId }, '[sessions] BANNED - not reconnecting');
      finalizeSessionExit(session, sessionId, { wipeAuth: false }).catch((err) => {
        logger.error({ sessionId, err: err.message }, '[sessions] cleanup after BANNED failed');
      });
      return;
    }
    if (action === 'conflict') {
      // Spec (literal): at most ONE reconnect attempt within 60s. A single
      // delayed attempt after conflictBackoffMs satisfies that by
      // construction (only one timer is ever scheduled per close event).
      session.reconnectAttempts += 1;
      scheduleReconnect(session, sessionId, config.reconnect.conflictBackoffMs, 'conflict');
      return;
    }
    if (action === 'restart') {
      // restartRequired: reconnect immediately (tiny jitter only, to avoid a
      // thundering-herd if many sessions hit it at once).
      scheduleReconnect(session, sessionId, jitter(50, 250), 'restart');
      return;
    }
    // Default: exponential backoff, 1s -> 5min, with jitter (spec, literal).
    session.reconnectAttempts += 1;
    const backoff = Math.min(config.reconnect.baseMs * 2 ** (session.reconnectAttempts - 1), config.reconnect.maxMs);
    scheduleReconnect(session, sessionId, backoff + jitter(0, config.reconnect.jitterMs), 'backoff');
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
      // P0.5 (G8): fromMe messages never enter the customer pipeline (that
      // was already true - shouldIgnoreInbound drops them), but they are now
      // checked for a genuine human-takeover signal instead of being dropped
      // unconditionally.
      const task = msg?.key?.fromMe === true
        ? processFromMeMessage(sessionId, msg)
        : processInboundMessage(sessionId, msg);
      task.catch((err) => {
        logger.error({ sessionId, err: err.message }, '[sessions] inbound message processing failed');
      });
    }
  });
}

async function openSocketForSession(session, sessionId) {
  await ensureAuthLoaded(session, sessionId);

  // P0.5: the Baileys version fetch now lives inside createWaSocket() itself
  // (driver/waDriver.js), and only runs on the real-driver branch - see that
  // file's own comment for why (a real bug this phase's own tests surfaced:
  // it used to fire, pointlessly, on every FakeWa socket open too).
  const sock = await createWaSocket({ authState: { state: session.state, saveCreds: session.saveCreds } });
  session.sock = sock;
  wireSocketEvents(session, sock, sessionId);

  // P0.4: crash-recovery MUST run before the worker starts, so any item left
  // in `inflight` from a previous process (crashed mid-send) is resolved
  // before new sends can interleave with it. Re-run on every reconnect too -
  // a fresh socket after a close can race a still-inflight send exactly the
  // same way a fresh process can.
  if (redisClient) {
    await recoverInflight(redisClient, sessionId);
  }
  await startSessionOutboundWorker(session);
}

function scheduleReconnect(session, sessionId, delayMs, reason) {
  if (session.stopping) return;
  if (session.reconnectTimer) clearTimeout(session.reconnectTimer);
  session.reconnectTimer = setTimeout(() => {
    session.reconnectTimer = null;
    if (session.stopping) return;
    logger.info({ sessionId, reason, attempt: session.reconnectAttempts }, '[sessions] reconnecting (P0.5)');
    openSocketForSession(session, sessionId).catch((err) => {
      logger.error({ sessionId, err: err.message }, '[sessions] reconnect attempt failed');
      session.reconnectAttempts += 1;
      const backoff = Math.min(config.reconnect.baseMs * 2 ** (session.reconnectAttempts - 1), config.reconnect.maxMs);
      scheduleReconnect(session, sessionId, backoff + jitter(0, config.reconnect.jitterMs), 'backoff-after-open-error');
    });
  }, delayMs);
  session.reconnectTimer.unref?.();
}

/** Stop all P0.5 timers/worker and, optionally, wipe the on-disk auth folder + remove the in-memory record. Used by LOGGED_OUT/BANNED and by explicit logout. */
async function finalizeSessionExit(session, sessionId, { wipeAuth }) {
  session.stopping = true;
  if (session.reconnectTimer) { clearTimeout(session.reconnectTimer); session.reconnectTimer = null; }
  stopLeaseRenewal(session);
  await stopOutboundWorker(session);
  if (redisClient && session.fencingToken !== null) {
    await releaseLease(redisClient, sessionId, config.instanceId, session.fencingToken).catch((err) => {
      logger.warn({ sessionId, err: err.message }, '[sessions] failed to release lease on exit (non-fatal - it will simply expire)');
    });
    session.fencingToken = null;
  }
  if (wipeAuth) {
    const authPath = session.authPath ?? path.join(AUTH_DIR, sessionId);
    await fs.rm(authPath, { recursive: true, force: true }).catch((err) => {
      logger.warn({ sessionId, err: err.message }, '[sessions] failed to remove auth folder (non-fatal)');
    });
    sessions.delete(sessionId);
  }
}

function stopLeaseRenewal(session) {
  if (session.leaseRenewTimer) {
    clearInterval(session.leaseRenewTimer);
    session.leaseRenewTimer = null;
  }
}

/** P0.5 (G7): lease loss during renewal - close the socket immediately WITHOUT logout() (spec, literal), and stop sending. Leaves the in-memory record so status queries still resolve and a later sweep on this instance can retake the lease. */
async function handleLeaseLost(session, sessionId) {
  logger.warn({ sessionId }, '[sessions] LEASE LOST - closing socket immediately without logout (P0.5/G7)');
  stopLeaseRenewal(session);
  if (session.reconnectTimer) { clearTimeout(session.reconnectTimer); session.reconnectTimer = null; }
  await stopOutboundWorker(session);
  try {
    session.sock?.end?.(new Error('P0.5: lease lost'));
  } catch (err) {
    logger.error({ sessionId, err: err.message }, '[sessions] error closing socket after lease loss (non-fatal)');
  }
  session.sock = null;
  session.fencingToken = null;
  session.status = 'DISCONNECTED';
  session.lastDisconnectReason = 'lease_lost';
  session.since = Date.now();
  postSessionStatus({
    session_id: sessionId,
    status: session.status,
    phone_number: session.phoneNumber,
    detail: 'lease_lost',
  }).catch((err) => {
    logger.error({ sessionId, err: err.message }, '[sessions] session-status webhook threw unexpectedly (lease_lost)');
  });
}

function startLeaseRenewalLoop(session, sessionId) {
  stopLeaseRenewal(session);
  session.leaseRenewTimer = setInterval(() => {
    renewLease(redisClient, sessionId, config.instanceId, session.fencingToken, config.lease.ttlMs)
      .then((ok) => {
        if (!ok) return handleLeaseLost(session, sessionId);
      })
      .catch((err) => {
        logger.error({ sessionId, err: err.message }, '[sessions] lease renewal call failed (will retry next tick)');
      });
  }, config.lease.renewMs);
  session.leaseRenewTimer.unref?.();
}

/** @returns {number} sessions this process currently holds a live socket for (H4: this is what MAX_SESSIONS caps). */
function activeLocalSessionCount() {
  let n = 0;
  for (const s of sessions.values()) if (s.sock) n += 1;
  return n;
}

/**
 * Try to become this session's active holder: acquire the lease (if Redis is
 * configured) and open the socket. Never throws on "someone else holds it" -
 * that is the normal, expected outcome of a lease conflict, not an error
 * (H3 is about failing loud on unexpected conditions, not on this one).
 *
 * @returns {Promise<{ started: boolean, reason?: 'already_active'|'capacity'|'lease_held' }>}
 */
async function attemptStartSession(sessionId) {
  const session = createSessionRecord(sessionId);
  if (session.sock) return { started: true, reason: 'already_active' };
  if (activeLocalSessionCount() >= config.maxSessions) return { started: false, reason: 'capacity' };

  session.stopping = false;
  await ensureAuthLoaded(session, sessionId);

  if (redisClient) {
    const { acquired, fencingToken } = await acquireLease(redisClient, sessionId, config.instanceId, config.lease.ttlMs);
    if (!acquired) return { started: false, reason: 'lease_held' };
    session.fencingToken = fencingToken;
  }

  await openSocketForSession(session, sessionId);
  if (redisClient) startLeaseRenewalLoop(session, sessionId);
  return { started: true };
}

export async function createSession(sessionId) {
  // Preserved from pre-P0.5 behavior (relied on by contract.test.js etc.): a
  // session record pre-seeded via createSessionRecord (e.g. a test's fake
  // socket, status set directly) short-circuits here unconditionally - this
  // function only ever manages a session it created itself.
  const existing = sessions.get(sessionId);
  if (existing) return existing;

  if (activeLocalSessionCount() >= config.maxSessions) {
    const err = new Error(`MAX_SESSIONS (${config.maxSessions}) reached on this instance`);
    err.code = 'CAPACITY';
    throw err;
  }

  const result = await attemptStartSession(sessionId);
  if (!result.started) {
    const err = new Error(
      result.reason === 'capacity'
        ? `MAX_SESSIONS (${config.maxSessions}) reached on this instance`
        : `Session ${sessionId}: lease is currently held by another gateway instance`,
    );
    err.code = result.reason === 'capacity' ? 'CAPACITY' : 'LEASE_HELD';
    throw err;
  }
  return sessions.get(sessionId);
}

export async function logoutSession(sessionId) {
  const session = sessions.get(sessionId);
  if (!session) return;

  session.stopping = true;
  if (session.reconnectTimer) { clearTimeout(session.reconnectTimer); session.reconnectTimer = null; }
  stopLeaseRenewal(session);
  await stopOutboundWorker(session);

  try {
    if (session.sock) {
      await session.sock.logout();
    }
  } catch (err) {
    logger.error({ sessionId, err: err.message }, '[sessions] logout failed');
  }

  if (redisClient && session.fencingToken !== null) {
    await releaseLease(redisClient, sessionId, config.instanceId, session.fencingToken).catch((err) => {
      logger.warn({ sessionId, err: err.message }, '[sessions] failed to release lease on logout (non-fatal - it will simply expire)');
    });
  }

  sessions.delete(sessionId);
  const authPath = session.authPath ?? path.join(AUTH_DIR, sessionId);
  await fs.rm(authPath, { recursive: true, force: true });
  logger.info({ sessionId }, '[sessions] logged out and removed session');
}

/** Registered (paired) session ids currently on disk under AUTH_DIR - shared across every gateway instance via the `gateway_auth_sessions` volume. */
async function listRegisteredSessionIds() {
  let entries;
  try {
    entries = await fs.readdir(AUTH_DIR, { withFileTypes: true });
  } catch (err) {
    // Inherited pre-P0.4 gap, fixed while this file is touched (P0_AUDIT_01
    // A-item: "catch {return;} in rehydrateSessions - log + metric instead of
    // silent swallow"). ENOENT (AUTH_DIR not created yet) is the expected,
    // routine case on a brand-new deployment; anything else is worth a log.
    if (err.code !== 'ENOENT') {
      logger.warn({ err: err.message }, '[sessions] could not read AUTH_DIR; treating as no registered sessions');
    }
    return [];
  }

  const ids = [];
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const sessionId = entry.name;
    const credsPath = path.join(AUTH_DIR, sessionId, 'creds.json');
    try {
      const raw = await fs.readFile(credsPath, 'utf8');
      const creds = JSON.parse(raw);
      if (creds?.registered === true) {
        ids.push(sessionId);
      } else {
        await fs.rm(path.join(AUTH_DIR, sessionId), { recursive: true, force: true }).catch((err) => {
          logger.warn({ sessionId, err: err.message }, '[sessions] failed to remove orphan session folder (non-fatal)');
        });
        logger.info({ sessionId }, '[sessions] removed orphan session folder');
      }
    } catch (err) {
      // creds.json missing/corrupt is the routine case for a directory mid-
      // pairing (crashed before creds.json was ever written) - logged at
      // debug (silent by default, LOG_LEVEL='silent') rather than swallowed
      // outright, since this loop runs on every rehydrate/sweep tick and a
      // warn/info here would spam production logs for a normal condition.
      logger.debug({ sessionId, err: err.message }, '[sessions] no valid creds.json (not a registered session)');
    }
  }
  return ids;
}

/**
 * P0.5 (G7, spec literal): boot 30 sessions -> no more than
 * REHYDRATE_CONCURRENCY connect simultaneously, each staggered by a random
 * jitter. A tiny manual semaphore (no new dependency) bounds how many
 * `attemptStartSession` calls are in flight at once.
 */
export async function rehydrateSessions() {
  const ids = await listRegisteredSessionIds();
  const concurrency = Math.max(1, config.rehydrate.concurrency);
  let cursor = 0;

  async function worker() {
    for (;;) {
      const i = cursor;
      cursor += 1;
      if (i >= ids.length) return;
      const sessionId = ids[i];
      await sleep(jitter(config.rehydrate.jitterMinMs, config.rehydrate.jitterMaxMs));
      try {
        const res = await attemptStartSession(sessionId);
        if (res.started) {
          logger.info({ sessionId }, '[sessions] rehydrated registered session');
        } else {
          logger.info({ sessionId, reason: res.reason }, '[sessions] rehydrate: did not start this session on this instance');
        }
      } catch (err) {
        logger.error({ sessionId, err: err.message }, '[sessions] failed to rehydrate');
      }
    }
  }

  await Promise.all(Array.from({ length: Math.min(concurrency, ids.length) }, worker));
}

/**
 * P0.5 (G7 failover): periodically re-scan for a registered session this
 * instance does not currently hold, and try to take it over. A no-op tick
 * for any session whose lease is still held elsewhere (the common case) -
 * `attemptStartSession` only actually opens a socket once `acquireLease`
 * succeeds, which only happens once the prior holder's lease has expired or
 * been released.
 *
 * @returns {{ stop: () => void }}
 */
export function startLeaseSweep() {
  if (!redisClient) return { stop: () => {} }; // nothing to sweep for without a lease to coordinate
  const timer = setInterval(() => {
    listRegisteredSessionIds()
      .then((ids) => Promise.all(ids.map((sessionId) => {
        const existing = sessions.get(sessionId);
        if (existing?.sock) return null; // already active here
        return attemptStartSession(sessionId).then((res) => {
          if (res.started) logger.info({ sessionId }, '[sessions] lease sweep: took over an unheld session (P0.5/G7)');
        }).catch((err) => {
          logger.error({ sessionId, err: err.message }, '[sessions] lease sweep attempt failed');
        });
      })))
      .catch((err) => {
        logger.error({ err: err.message }, '[sessions] lease sweep scan failed');
      });
  }, config.lease.sweepMs);
  timer.unref?.();
  return { stop: () => clearInterval(timer) };
}

/**
 * Best-effort shutdown helper (P0.5-scoped subset of P0.6's fuller graceful
 * shutdown): stop every timer, close every socket WITHOUT logout(), and
 * release every lease this instance still owns, so the next holder does not
 * have to wait out the full lease TTL on an ordinary restart. P0.6 will wrap
 * this in the complete SIGTERM sequence (stop accepting requests, drain
 * in-flight downloads, flush spool, exit within the stop_grace_period).
 */
export async function releaseAllOwnedLeases() {
  const tasks = [];
  for (const [sessionId, session] of sessions.entries()) {
    if (session.reconnectTimer) { clearTimeout(session.reconnectTimer); session.reconnectTimer = null; }
    stopLeaseRenewal(session);
    tasks.push(stopOutboundWorker(session).catch((err) => {
      logger.warn({ sessionId, err: err.message }, '[sessions] failed to stop outbound worker during shutdown (non-fatal, process is exiting)');
    }));
    try {
      session.sock?.end?.(new Error('P0.5: shutting down'));
    } catch (err) {
      logger.warn({ sessionId, err: err.message }, '[sessions] error closing socket during shutdown (non-fatal, process is exiting)');
    }
    if (redisClient && session.fencingToken !== null) {
      tasks.push(
        releaseLease(redisClient, sessionId, config.instanceId, session.fencingToken).catch((err) => {
          logger.warn({ sessionId, err: err.message }, '[sessions] failed to release lease during shutdown (non-fatal - it will simply expire)');
        }),
      );
    }
  }
  await Promise.all(tasks);
}

/** P0.5: GET /sessions/:id/health - additive endpoint; getSessionStatus's own contract (status/qr_image_base64/connected_phone_number) is unchanged. */
export async function getSessionHealth(sessionId) {
  const session = sessions.get(sessionId);
  if (!session) return null;
  const [queueDepth, leaseHolder, spool] = await Promise.all([
    getOutboundQueueDepth(sessionId),
    redisClient ? getLeaseHolder(redisClient, sessionId) : Promise.resolve(null),
    spoolStatus(),
  ]);
  return {
    state: session.status,
    since: new Date(session.since).toISOString(),
    reconnect_attempts: session.reconnectAttempts,
    last_disconnect_reason: session.lastDisconnectReason,
    lease_holder: leaseHolder,
    queue_depth: queueDepth,
    spool,
    // P0.6 stub (spec, literal - the real kill-switch check is that phase's
    // scope): always "not blocked" until P0.6 wires the real check.
    kill_switch: { blocked: false },
  };
}
