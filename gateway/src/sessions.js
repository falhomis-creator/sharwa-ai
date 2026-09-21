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

import { postSessionStatus, postInboundMessage } from './webhook.js';

// ---------------------------------------------------------------------------
// Tenant-isolated Baileys session manager.
//
// Constitution guarantees implemented here:
//  - One session per merchant (session_id); sessions never share state.
//  - session_id is ALWAYS supplied by Django. This gateway never generates or
//    guesses one, so an external party cannot predict a session id.
//  - Per-session FIFO send queue with jitter (2000-3000ms): two messages are
//    never transmitted for the same session at the same instant.
//  - Inbound messages are filtered (fromMe / group / broadcast / no-text)
//    BEFORE any webhook is emitted, preventing an infinite loop from day one.
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

// --- pure helpers (exported for unit tests) --------------------------------

export function isGroupJid(jid) {
  return typeof jid === 'string' && (jid.endsWith('@g.us') || jid.endsWith('@broadcast'));
}

export function extractText(msg) {
  if (!msg || !msg.message) return '';
  const m = msg.message;
  if (typeof m.conversation === 'string') return m.conversation;
  if (m.extendedTextMessage?.text) return m.extendedTextMessage.text;
  if (m.imageMessage?.caption) return m.imageMessage.caption;
  if (m.videoMessage?.caption) return m.videoMessage.caption;
  if (m.documentMessage?.caption) return m.documentMessage.caption;
  if (m.buttonsResponseMessage?.selectedDisplayText) return m.buttonsResponseMessage.selectedDisplayText;
  if (m.listResponseMessage?.title) return m.listResponseMessage.title;
  if (m.templateButtonReplyMessage?.selectedDisplayText) return m.templateButtonReplyMessage.selectedDisplayText;
  return '';
}

export function detectMedia(msg) {
  if (!msg || !msg.message) return null;
  const m = msg.message;
  if (m.imageMessage) return { mediaType: 'image', content: m.imageMessage };
  if (m.audioMessage) return { mediaType: 'audio', content: m.audioMessage };
  if (m.videoMessage) return { mediaType: 'video', content: m.videoMessage };
  if (m.documentMessage) return { mediaType: 'document', content: m.documentMessage };
  return null;
}

export function shouldIgnoreInbound(msg) {
  if (!msg) return true;
  if (msg.key?.fromMe === true) return true; // merchant's own outgoing messages
  if (isGroupJid(msg.key?.remoteJid)) return true; // group (@g.us) or broadcast (@broadcast)
  if (detectMedia(msg)) return false; // media messages are processed even with no text
  if (!extractText(msg)) return true; // no actual text
  return false;
}

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
  // Fallback: use the document's own filename extension when present.
  const fileName = content.fileName || content.filename || '';
  const ext = fileName.split('.').pop();
  if (ext && /^[a-z0-9]{1,10}$/i.test(ext)) return ext.toLowerCase();
  return 'bin';
}

export function mediaObjectKey(sessionId, ext) {
  return `sharwa-ai/${sessionId}/${crypto.randomUUID()}.${ext}`;
}

export async function downloadMediaToBuffer(msg) {
  // Official Baileys download API (verified in @whiskeysockets/baileys@6.7.24):
  // downloadMediaMessage(message, 'buffer') -> Buffer (decrypted, ready to store).
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
    forcePathStyle: true, // MinIO requires path-style addressing
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
        const delay = UPLOAD_BASE_DELAY_MS * 2 ** (attempt - 1); // 500, 1000
        console.error(`[sessions] MinIO upload attempt ${attempt}/${UPLOAD_MAX_ATTEMPTS} failed (${err.message}); retrying in ${delay}ms`);
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
    // WhatsApp officially bans a number with HTTP 403. In Baileys this
    // surfaces as DisconnectReason.forbidden (=== 403).
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

// --- inbound handling -------------------------------------------------------

export async function processInboundMessage(
  sessionId,
  msg,
  sendFn = postInboundMessage,
  deps = {},
) {
  if (shouldIgnoreInbound(msg)) return null;

  const from = msg.key.remoteJid;
  const text = extractText(msg);
  const message_id = msg.key.id ?? null;
  const media = detectMedia(msg);

  if (media) {
    const { downloadFn = downloadMediaToBuffer, uploadFn = uploadToMinio } = deps;
    const ext = extensionFor(media.content);
    const objectKey = mediaObjectKey(sessionId, ext);
    const contentType = (media.content.mimetype || '').split(';')[0].trim() || 'application/octet-stream';

    let media_object_key;
    let outText;
    try {
      const buffer = await downloadFn(msg);
      await uploadWithRetry(buffer, objectKey, contentType, uploadFn);
      media_object_key = objectKey;
      outText = text; // caption if present, otherwise empty
    } catch (err) {
      console.error(`[sessions] media processing failed for ${message_id}: ${err.message}`);
      media_object_key = null;
      outText = 'تعذّر معالجة المرفق المرسل.';
    }

    await sendFn({
      session_id: sessionId,
      from,
      text: outText,
      message_id,
      media_object_key,
      media_type: media.mediaType,
    });
    return message_id;
  }

  await sendFn({ session_id: sessionId, from, text, message_id });
  return message_id;
}

// --- send queue (FIFO, per session, jitter between messages) ----------------

export function enqueueSend(sessionId, to, text) {
  const session = sessions.get(sessionId);
  if (!session) {
    throw new Error(`Unknown session: ${sessionId}`);
  }
  if (session.queue.length >= MAX_QUEUE_SIZE) {
    return null; // signal a full queue (caller responds 503)
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
      // Jitter between consecutive messages is the single line of defense
      // against WhatsApp rate-limit bans for this number.
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
    console.error(`[sessions] session ${session.id} has no socket; dropping message ${item.message_id}`);
    return;
  }
  try {
    await session.sock.sendMessage(item.to, { text: item.text });
  } catch (err) {
    console.error(`[sessions] failed to send message ${item.message_id} on session ${session.id}: ${err.message}`);
  }
}


// --- query helpers for index.js --------------------------------------------

/**
 * Convert Baileys' raw QR string (update.qr) into a base64 PNG image WITHOUT
 * the "data:image/png;base64," prefix. Django's frontend (sharwa_ai_connect.html)
 * adds the prefix itself when missing, so we must never send the prefix here.
 *
 * @param {string|null|undefined} rawQr
 * @returns {Promise<string|null>}
 */
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
    console.error(`[sessions] could not fetch latest Baileys version (${err.message}); using library default`);
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

    // Fire-and-forget: the webhook layer already handles retries + backoff.
    postSessionStatus({
      session_id: sessionId,
      status: session.status,
      phone_number: session.phoneNumber,
      detail: update.lastDisconnect?.error?.message ?? update.connection ?? null,
    }).catch((err) => {
      console.error(`[sessions] session-status webhook threw unexpectedly: ${err.message}`);
    });
  });

  sock.ev.on('messages.upsert', ({ messages, type }) => {
    if (type !== 'notify') return;
    for (const msg of messages) {
      processInboundMessage(sessionId, msg).catch((err) => {
        console.error(`[sessions] inbound message processing failed: ${err.message}`);
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
    console.error(`[sessions] logout for ${sessionId} failed: ${err.message}`);
  }

  sessions.delete(sessionId);
  const authPath = session.authPath ?? path.join(AUTH_DIR, sessionId);
  await fs.rm(authPath, { recursive: true, force: true });
  console.log(`[sessions] logged out and removed session ${sessionId}`);
}

export async function rehydrateSessions() {
  let entries;
  try {
    entries = await fs.readdir(AUTH_DIR, { withFileTypes: true });
  } catch {
    return; // no auth_sessions directory yet - nothing to rehydrate
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
      registered = false; // missing / corrupt creds -> orphan
    }

    if (registered) {
      try {
        await createSession(sessionId);
        console.log(`[sessions] rehydrated registered session ${sessionId}`);
      } catch (err) {
        console.error(`[sessions] failed to rehydrate ${sessionId}: ${err.message}`);
      }
    } else {
      // Orphan: QR was shown but never scanned. A resurrected session would
      // mint a fresh QR that no known session_id on Django could match.
      await fs.rm(dirPath, { recursive: true, force: true }).catch(() => {});
      console.log(`[sessions] removed orphan session folder ${sessionId}`);
    }
  }
}
