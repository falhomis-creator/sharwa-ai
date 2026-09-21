// gateway/src/ingest/identity.js
// Pure identity normalization for inbound WhatsApp messages (G12 / audit §5).
//
// Why this exists: baileys@6.7.24 addresses some senders by `@lid` (an opaque
// account identifier) rather than a phone number. We must NOT assume a JID is a
// phone number (P0_FINDINGS.md F1). This module turns a raw JID into a stable
// identity and resolves phone_e164 without ever inventing a number.
//
// phone_e164 resolution priority (audit §5):
//   1. sender_pn from the message itself (authoritative when present);
//   2. for @lid addresses, the value from the injected lid->pn map;
//   3. otherwise null.
// For phone-addressed JIDs (`<digits>@s.whatsapp.net`) the number is decoded
// from the JID itself (that IS the addressing scheme), not invented.
//
// The lid->pn map is injected as a `{ get(lid) => phoneE164 | null }` interface.
// This module never touches Redis. Storing the lidmap on redis-durable and the
// `identity_update` entry are deferred until Docker is available.

const LID_SUFFIX = '@lid';
const PN_SERVER = '@s.whatsapp.net';

/** True when the JID is an @lid identifier (opaque, not a phone number). */
export function isLidJid(jid) {
  return typeof jid === 'string' && jid.endsWith(LID_SUFFIX);
}

/** True when the JID is a phone-addressed JID (`<digits>@s.whatsapp.net`). */
export function isPnJid(jid) {
  return typeof jid === 'string' && jid.endsWith(PN_SERVER);
}

/**
 * Normalize an E.164-ish phone value to the gateway's canonical internal form:
 * digits only (no leading `+`, no whitespace). Returns null for anything that
 * is not a plausible phone number, so we never propagate a fabricated number.
 */
export function normalizeE164(value) {
  if (typeof value !== 'string') return null;
  const digits = value.replace(/\s+/g, '').replace(/^\+/, '');
  return /^\d{5,15}$/.test(digits) ? digits : null;
}

/** Decode the phone number from a phone-addressed JID, or null. */
export function phoneFromPnJid(jid) {
  if (!isPnJid(jid)) return null;
  return normalizeE164(jid.slice(0, -PN_SERVER.length));
}

/**
 * Normalize a raw JID into a stable identity record. phone_e164 here reflects
 * ONLY the JID itself (pn -> decoded number; lid/other -> null). Apply the full
 * sender_pn/lidmap priority via resolvePhoneE164 / normalizeIdentity.
 */
export function normalizeJid(jid) {
  if (typeof jid !== 'string' || jid === '') return null;
  if (isLidJid(jid)) {
    return { jid_raw: jid, addressing: 'lid', wa_id: jid, phone_e164: null };
  }
  const phone = phoneFromPnJid(jid);
  if (phone !== null) {
    return { jid_raw: jid, addressing: 'pn', wa_id: phone, phone_e164: phone };
  }
  // Not a 1:1 user address (group/broadcast/unknown). Those are filtered
  // upstream by shouldIgnoreInbound, so this is a defensive fallback.
  return { jid_raw: jid, addressing: 'pn', wa_id: jid, phone_e164: null };
}

/**
 * Resolve phone_e164 by priority: sender_pn -> (pn: JID / lid: lidmap) -> null.
 *
 * @param {object} params
 * @param {string} params.jid        Raw JID.
 * @param {string} [params.senderPn] sender_pn from the message itself.
 * @param {{ get(lid: string): string | null | undefined }} [params.lidMap]
 * @returns {string | null}
 */
export function resolvePhoneE164({ jid, senderPn, lidMap } = {}) {
  const fromSender = normalizeE164(senderPn);
  if (fromSender !== null) return fromSender;

  const fromJid = phoneFromPnJid(jid);
  if (fromJid !== null) return fromJid;

  if (isLidJid(jid) && lidMap && typeof lidMap.get === 'function') {
    const fromMap = normalizeE164(lidMap.get(jid));
    if (fromMap !== null) return fromMap;
  }

  return null;
}

/**
 * Full normalization entry point.
 *
 * @param {object} params
 * @param {string} params.remoteJid  Raw JID (message key.remoteJid).
 * @param {string} [params.senderPn] sender_pn from the message itself.
 * @param {{ get(lid: string): string | null | undefined }} [params.lidMap]
 * @returns {{ jid_raw: string, addressing: 'pn'|'lid', wa_id: string, phone_e164: string|null } | null}
 */
export function normalizeIdentity({ remoteJid, senderPn, lidMap } = {}) {
  const base = normalizeJid(remoteJid);
  if (base === null) return null;
  const phone_e164 = resolvePhoneE164({ jid: remoteJid, senderPn, lidMap });
  return { ...base, phone_e164 };
}
