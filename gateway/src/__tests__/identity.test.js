import test from 'node:test';
import assert from 'node:assert/strict';

import {
  isLidJid,
  isPnJid,
  normalizeE164,
  phoneFromPnJid,
  normalizeJid,
  resolvePhoneE164,
  normalizeIdentity,
} from '../ingest/identity.js';

// A lid->pn map is injected as a plain `{ get(lid) }` interface. No Redis here:
// the test uses an in-memory object, proving the pure functions are storage-free.
function lidMapOf(entries) {
  return { get: (lid) => (Object.prototype.hasOwnProperty.call(entries, lid) ? entries[lid] : null) };
}

test('isLidJid / isPnJid classify the two addressing schemes', () => {
  assert.equal(isLidJid('123456789012345@lid'), true);
  assert.equal(isLidJid('201234567890@s.whatsapp.net'), false);
  assert.equal(isPnJid('201234567890@s.whatsapp.net'), true);
  assert.equal(isPnJid('123456789012345@lid'), false);
  assert.equal(isLidJid(null), false);
});

test('normalizeE164 strips +/whitespace and rejects non-phone values', () => {
  assert.equal(normalizeE164('+201234567890'), '201234567890');
  assert.equal(normalizeE164(' 201 2345 67890 '), '201234567890');
  assert.equal(normalizeE164('201234567890'), '201234567890');
  assert.equal(normalizeE164('abc'), null);
  assert.equal(normalizeE164(''), null);
  assert.equal(normalizeE164(null), null);
  assert.equal(normalizeE164(201234567890), null);
});

test('phoneFromPnJid decodes a phone JID and rejects lid/group JIDs', () => {
  assert.equal(phoneFromPnJid('201234567890@s.whatsapp.net'), '201234567890');
  assert.equal(phoneFromPnJid('123456789012345@lid'), null);
  assert.equal(phoneFromPnJid('123456789@g.us'), null);
});

test('normalizeJid maps pn and lid to a stable identity without assuming JID = phone', () => {
  assert.deepEqual(normalizeJid('201234567890@s.whatsapp.net'), {
    jid_raw: '201234567890@s.whatsapp.net',
    addressing: 'pn',
    wa_id: '201234567890',
    phone_e164: '201234567890',
  });
  assert.deepEqual(normalizeJid('123456789012345@lid'), {
    jid_raw: '123456789012345@lid',
    addressing: 'lid',
    wa_id: '123456789012345@lid',
    phone_e164: null,
  });
  assert.equal(normalizeJid(''), null);
});

test('resolvePhoneE164: sender_pn wins, then lidmap for @lid, then null', () => {
  const map = lidMapOf({ '123456789012345@lid': '+201555559999' });

  // (a) sender_pn is authoritative, even when the lidmap also has a number.
  assert.equal(
    resolvePhoneE164({ jid: '123456789012345@lid', senderPn: '201111112222', lidMap: map }),
    '201111112222',
  );

  // (b) @lid with no sender_pn falls back to the lidmap.
  assert.equal(
    resolvePhoneE164({ jid: '123456789012345@lid', lidMap: map }),
    '201555559999',
  );

  // (c) @lid with neither sender_pn nor a map entry -> null (never invented).
  assert.equal(resolvePhoneE164({ jid: '123456789012345@lid', lidMap: lidMapOf({}) }), null);
  assert.equal(resolvePhoneE164({ jid: '123456789012345@lid' }), null);

  // (d) phone-addressed JID resolves from the JID itself.
  assert.equal(resolvePhoneE164({ jid: '201234567890@s.whatsapp.net' }), '201234567890');
});

test('normalizeIdentity: full G12 priority for @lid and pn messages', () => {
  assert.deepEqual(normalizeIdentity({ remoteJid: '123456789012345@lid' }), {
    jid_raw: '123456789012345@lid',
    addressing: 'lid',
    wa_id: '123456789012345@lid',
    phone_e164: null,
  });

  assert.deepEqual(
    normalizeIdentity({
      remoteJid: '123456789012345@lid',
      lidMap: lidMapOf({ '123456789012345@lid': '201555559999' }),
    }),
    {
      jid_raw: '123456789012345@lid',
      addressing: 'lid',
      wa_id: '123456789012345@lid',
      phone_e164: '201555559999',
    },
  );

  assert.deepEqual(
    normalizeIdentity({
      remoteJid: '123456789012345@lid',
      senderPn: '201111112222',
      lidMap: lidMapOf({ '123456789012345@lid': '201555559999' }),
    }),
    {
      jid_raw: '123456789012345@lid',
      addressing: 'lid',
      wa_id: '123456789012345@lid',
      phone_e164: '201111112222',
    },
  );
});

test('audit §5 scenario: @lid is null, then a phoneNumberShare populates the map, then the number is present', () => {
  const lid = '123456789012345@lid';
  const store = {}; // stands in for lidmap:{session_id} hash on redis-durable (deferred)
  const lidMap = lidMapOf(store);

  // 1) First message: @lid with no number -> phone_e164 = null.
  const first = normalizeIdentity({ remoteJid: lid, lidMap });
  assert.equal(first.phone_e164, null);

  // 2) A `chats.phoneNumberShare` event arrives: the map is updated (pure, no Redis).
  store[lid] = '201555559999';

  // 3) A later message from the same identifier now carries the number.
  const later = normalizeIdentity({ remoteJid: lid, lidMap });
  assert.equal(later.phone_e164, '201555559999');
});
