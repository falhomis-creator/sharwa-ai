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

/**
 * Build the WAL entry recording a lid -> phone_e164 resolution (R3_DIRECTIVE:
 * "lidmap على redis-durable ومدخل identity_update"). This is bookkeeping for a
 * future P1 core consumer reading the stream directly - the legacy Django
 * webhook has no field for it, so forwarder.js recognizes type ===
 * 'identity_update' and ACKs it without calling postInboundMessage (see
 * forwarder.js::shouldForwardToLegacy).
 *
 * provider_message_id is DETERMINISTIC (lid + phone_e164, not random/ts-based)
 * so a repeated `chats.phoneNumberShare` for an already-known pair dedupes via
 * the normal wal.js::dedupeKeyFor path instead of appending a fresh stream
 * entry every time Baileys re-emits the event (which it does on reconnects).
 *
 * @param {string} lid
 * @param {string} phone_e164
 * @returns {object} normalized entry, ready for wal.js::appendEvent
 */
export function buildIdentityUpdateEvent(lid, phone_e164) {
  return {
    type: 'identity_update',
    provider_message_id: `identity:${lid}:${phone_e164}`,
    ts: Date.now(),
    lid,
    phone_e164,
  };
}
