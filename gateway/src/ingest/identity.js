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
// For phone-addressed JIDs the number is decoded from the JID itself (that IS
// the addressing scheme), not invented. sender_pn and lidmap values may arrive
// as bare digits OR as a JID (F1: sender_pn is a JID).
//
// Format decision (audit §2 E1.4, auditor-approved): phone_e164 is emitted as
// real E.164 (`+` + digits); wa_id is digits only (no `+`).
//
// The lid->pn map is injected as a `{ get(lid) => phoneE164 | null }` interface.
// This module never touches Redis. Storing the lidmap on redis-durable and the
// `identity_update` entry are deferred until Docker is available.

const LID_SUFFIX = '@lid';
const PN_SERVERS = new Set(['s.whatsapp.net', 'c.us']);

/** Split a JID into { user, server }. A value with no '@' has server = null. */
function splitJid(value) {
  if (typeof value !== 'string') return null;
  const at = value.indexOf('@');
  if (at === -1) return { user: value, server: null };
  return { user: value.slice(0, at), server: value.slice(at + 1) };
}

/** Remove the device (`:12`) and agent (`_5`) suffixes from a JID user part. */
function stripDeviceAgent(user) {
  return user.split(':')[0].split('_')[0];
}

/** True when the JID is an @lid identifier (opaque, not a phone number). */
export function isLidJid(jid) {
  return typeof jid === 'string' && jid.endsWith(LID_SUFFIX);
}

/** True when the JID is a phone-addressed JID on an accepted server. */
export function isPnJid(jid) {
  const s = splitJid(jid);
  if (!s || s.server === null) return false;
  return PN_SERVERS.has(s.server) && normalizeE164(stripDeviceAgent(s.user)) !== null;
}

/**
 * Normalize a phone value to real E.164 (`+` + digits) or null. Rejects anything
 * that is not a plausible phone number, so we never propagate a fabricated number.
 */
export function normalizeE164(value) {
  if (typeof value !== 'string') return null;
  const digits = value.replace(/\s+/g, '').replace(/^\+/, '');
  return /^\d{5,15}$/.test(digits) ? `+${digits}` : null;
}

/**
 * Decode a value (bare digits OR a JID) into E.164, or null.
 * - bare digits: normalized directly;
 * - JID: only `s.whatsapp.net` / `c.us` servers are accepted; the user part has
 *   its `:device` and `_agent` suffixes stripped;
 * - `@lid` never yields a number.
 */
export function toE164(value) {
  const s = splitJid(value);
  if (!s) return null;
  if (s.server === 'lid') return null;
  if (s.server !== null && !PN_SERVERS.has(s.server)) return null;
  return normalizeE164(stripDeviceAgent(s.user));
}

/** digits without the leading `+` (used for wa_id). */
function digitsFromE164(e164) {
  return e164.startsWith('+') ? e164.slice(1) : e164;
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
  const e164 = toE164(jid);
  if (e164 !== null) {
    return { jid_raw: jid, addressing: 'pn', wa_id: digitsFromE164(e164), phone_e164: e164 };
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
 * @param {string} [params.senderPn] sender_pn from the message itself (digits OR JID).
 * @param {{ get(lid: string): string | null | undefined }} [params.lidMap]
 * @returns {string | null}
 */
export function resolvePhoneE164({ jid, senderPn, lidMap } = {}) {
  const fromSender = toE164(senderPn);
  if (fromSender !== null) return fromSender;

  const fromJid = toE164(jid);
  if (fromJid !== null) return fromJid;

  if (isLidJid(jid) && lidMap && typeof lidMap.get === 'function') {
    const fromMap = toE164(lidMap.get(jid));
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
