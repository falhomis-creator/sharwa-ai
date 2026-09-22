// gateway/src/ingest/normalize.js
// G4: normalize a raw Baileys message into a typed, storage-ready entry.
//
// Why this exists: the legacy code only knew text + four media kinds, so a
// location pin (the #1 reason for this task), a shared contact, a sticker, or a
// reaction were silently dropped by `shouldIgnoreInbound`. This module classifies
// every inbound message into the architecture's fixed type set
// (text|image|audio|video|document|location|contact|sticker|reaction|unsupported)
// and produces the fields the WAL stream carries (PROMPT_P0 §6 P0.2 step 1).
//
// Pure and storage-free: identity uses `ingest/identity.js` (G12); no Redis, no
// network. Media bytes are never read here — a media entry carries a small
// metadata reference, so the 64KB value cap (F4) is satisfied by construction.

import { normalizeIdentity } from './identity.js';

/** True for group (@g.us) or broadcast (@broadcast) JIDs. */
export function isGroupJid(jid) {
  return typeof jid === 'string' && (jid.endsWith('@g.us') || jid.endsWith('@broadcast'));
}

/**
 * Extract the human-visible body text of a message, or ''.
 * Covers plain text, captions, and interactive selections (buttons/list/template)
 * which the architecture normalizes to `text` (PROMPT_P0 §6 P0.2 step 4).
 */
export function extractText(msg) {
  if (!msg || !msg.message) return '';
  const m = msg.message;
  if (typeof m.conversation === 'string') return m.conversation;
  if (m.extendedTextMessage?.text) return m.extendedTextMessage.text;
  if (m.imageMessage?.caption) return m.imageMessage.caption;
  if (m.videoMessage?.caption) return m.videoMessage.caption;
  if (m.documentMessage?.caption) return m.documentMessage.caption;
  if (m.buttonsResponseMessage?.selectedDisplayText) return m.buttonsResponseMessage.selectedDisplayText;
  if (m.listResponseMessage?.singleSelectReply?.selectedRowId) return m.listResponseMessage.singleSelectReply.selectedRowId;
  if (m.listResponseMessage?.title) return m.listResponseMessage.title;
  if (m.templateButtonReplyMessage?.selectedDisplayText) return m.templateButtonReplyMessage.selectedDisplayText;
  return '';
}

/**
 * Legacy media detector (image/audio/video/document) — kept compatible with the
 * existing export so `sessions.js` and its tests keep working.
 */
export function detectMedia(msg) {
  if (!msg || !msg.message) return null;
  const m = msg.message;
  if (m.imageMessage) return { mediaType: 'image', content: m.imageMessage };
  if (m.audioMessage) return { mediaType: 'audio', content: m.audioMessage };
  if (m.videoMessage) return { mediaType: 'video', content: m.videoMessage };
  if (m.documentMessage) return { mediaType: 'document', content: m.documentMessage };
  return null;
}

/**
 * Classify a message into the architecture's type set. Returns null when the
 * message has no meaningful content (so it can be ignored upstream).
 */
export function detectType(msg) {
  if (!msg || !msg.message) return null;
  const m = msg.message;
  if (m.conversation !== undefined || m.extendedTextMessage) return 'text';
  if (m.imageMessage) return 'image';
  if (m.audioMessage) return 'audio';
  if (m.videoMessage) return 'video';
  if (m.documentMessage) return 'document';
  if (m.locationMessage) return 'location';
  if (m.contactMessage || m.contactsArrayMessage) return 'contact';
  if (m.stickerMessage) return 'sticker';
  if (m.reactionMessage) return 'reaction';
  if (m.buttonsResponseMessage || m.listResponseMessage || m.templateButtonReplyMessage) return 'text';
  // Anything else that still carries a message body is recorded as `unsupported`
  // rather than silently dropped (F2: no event is ever dropped silently).
  return 'unsupported';
}

/**
 * Decide whether an inbound message should be filtered before the WAL.
 *
 * G4 fix: location, contact, sticker and reaction are now classified as content,
 * so a location pin is no longer dropped (the #1 reason for this task). fromMe,
 * group/broadcast, and content-less messages are still ignored.
 */
export function shouldIgnoreInbound(msg) {
  if (!msg) return true;
  if (msg.key?.fromMe === true) return true;
  if (isGroupJid(msg.key?.remoteJid)) return true;
  return detectType(msg) === null;
}

/** Baileys reports `messageTimestamp` in epoch seconds; the WAL wants epoch ms. */
function extractTs(msg) {
  const t = msg?.messageTimestamp;
  if (t !== undefined && t !== null && t !== '') {
    const n = Number(t);
    if (Number.isFinite(n) && n > 0) return Math.floor(n * 1000);
  }
  return Date.now();
}

/** Parse phone numbers out of a vCard string (TEL / waid lines). */
function parseVcardNumbers(vcard) {
  if (typeof vcard !== 'string') return [];
  const numbers = [];
  const telRe = /TEL[^:\r\n]*:([+\d][\d\s()-]*)/g;
  let m;
  while ((m = telRe.exec(vcard)) !== null) {
    const digits = m[1].replace(/[^\d+]/g, '');
    if (digits && !numbers.includes(digits)) numbers.push(digits);
  }
  const waidRe = /waid=(\d+)/g;
  while ((m = waidRe.exec(vcard)) !== null) {
    if (!numbers.includes(m[1])) numbers.push(m[1]);
  }
  return numbers;
}

/** Normalize a location message to the schema's `{lat, lng, name, address}` (+ is_live). */
function normalizeLocation(lm) {
  const lat = Number(lm.degreesLatitude);
  const lng = Number(lm.degreesLongitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  return {
    lat,
    lng,
    name: typeof lm.name === 'string' && lm.name ? lm.name : undefined,
    address: typeof lm.address === 'string' && lm.address ? lm.address : undefined,
    is_live: lm.isLive === true || lm.is_live === true,
  };
}

/** Normalize a contact / contacts-array message to `{name, numbers}`. */
function normalizeContact(m) {
  if (m.contactsArrayMessage) {
    const contacts = Array.isArray(m.contactsArrayMessage.contacts) ? m.contactsArrayMessage.contacts : [];
    const name = contacts.find((c) => c?.displayName)?.displayName;
    const numbers = contacts.flatMap((c) => parseVcardNumbers(c?.vcard));
    return { name, numbers };
  }
  const cm = m.contactMessage;
  return {
    name: typeof cm?.displayName === 'string' && cm.displayName ? cm.displayName : undefined,
    numbers: parseVcardNumbers(cm?.vcard),
  };
}

/**
 * Normalize one raw Baileys message into the WAL entry body.
 *
 * @param {object} msg                      Raw `messages.upsert` message.
 * @param {{ get(lid: string): string|null|undefined }} [opts.lidMap]  lid->phone map (G12).
 * @returns {object|null}  The normalized entry, or null when it should be ignored.
 */
export function normalizeMessage(msg, { lidMap } = {}) {
  if (shouldIgnoreInbound(msg)) return null;

  const m = msg.message;
  const type = detectType(msg);
  const identity = normalizeIdentity({
    remoteJid: msg.key.remoteJid,
    senderPn: msg.key.senderPn, // real F1 shape: sender_pn lives on the key (decode-wa-message.js:91)
    lidMap,
  });

  const entry = {
    provider_message_id: msg.key.id ?? null,
    type,
    ts: extractTs(msg),
    identity,
  };

  if (type === 'text') {
    const text = extractText(msg);
    if (text) entry.text = text;
  } else if (type === 'image' || type === 'audio' || type === 'video' || type === 'document') {
    const content = m[`${type}Message`];
    entry.media = {
      kind: type,
      mimetype: content?.mimetype ?? null,
      fileLength: content?.fileLength != null ? Number(content.fileLength) : null,
      fileName: typeof content?.fileName === 'string' ? content.fileName : undefined,
    };
    const caption = extractText(msg);
    if (caption) entry.text = caption;
  } else if (type === 'location') {
    const loc = normalizeLocation(m.locationMessage);
    if (loc) entry.location = loc;
  } else if (type === 'contact') {
    entry.contact = normalizeContact(m);
  } else if (type === 'sticker') {
    entry.media = {
      kind: 'sticker',
      mimetype: m.stickerMessage?.mimetype ?? null,
      fileLength: m.stickerMessage?.fileLength != null ? Number(m.stickerMessage.fileLength) : null,
    };
  } else if (type === 'reaction') {
    entry.reaction = {
      text: typeof m.reactionMessage?.text === 'string' ? m.reactionMessage.text : undefined,
      target_message_id: m.reactionMessage?.key?.id ?? null,
    };
  }
  // `unsupported` carries no extra fields: it is delivered with an empty text.

  return entry;
}
