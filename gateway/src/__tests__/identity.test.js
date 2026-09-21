import test from 'node:test';
import assert from 'node:assert/strict';

import {
  isLidJid,
  isPnJid,
  normalizeE164,
  toE164,
  normalizeJid,
  resolvePhoneE164,
  normalizeIdentity,
} from '../ingest/identity.js';

// A lid->pn map is injected as a plain `{ get(lid) }` interface. No Redis here:
// the test uses an in-memory object, proving the pure functions are storage-free.
function lidMapOf(entries) {
  return { get: (lid) => (Object.prototype.hasOwnProperty.call(entries, lid) ? entries[lid] : null) };
}

const LID = '123456789012345@lid';

test('isLidJid / isPnJid classify addressing schemes', () => {
  assert.equal(isLidJid(LID), true);
  assert.equal(isLidJid('201234567890@s.whatsapp.net'), false);
  assert.equal(isPnJid('201234567890@s.whatsapp.net'), true);
  assert.equal(isPnJid('201234567890@c.us'), true);
  assert.equal(isPnJid('201234567890:12@s.whatsapp.net'), true);
  assert.equal(isPnJid(LID), false);
  assert.equal(isPnJid('123456789@g.us'), false);
  assert.equal(isLidJid(null), false);
});

test('normalizeE164 emits real E.164 (+digits) and rejects non-phone values', () => {
  assert.equal(normalizeE164('+201234567890'), '+201234567890');
  assert.equal(normalizeE164('201234567890'), '+201234567890');
  assert.equal(normalizeE164(' 201 2345 67890 '), '+201234567890');
  assert.equal(normalizeE164('abc'), null);
  assert.equal(normalizeE164(''), null);
  assert.equal(normalizeE164(null), null);
  assert.equal(normalizeE164(201234567890), null);
});

test('toE164 accepts bare digits and JIDs, strips :device/_agent, never from @lid', () => {
  assert.equal(toE164('201234567890'), '+201234567890');
  assert.equal(toE164('201234567890@s.whatsapp.net'), '+201234567890');
  assert.equal(toE164('201234567890:12@s.whatsapp.net'), '+201234567890');
  assert.equal(toE164('201234567890_5@s.whatsapp.net'), '+201234567890');
  assert.equal(toE164('201234567890@c.us'), '+201234567890');
  assert.equal(toE164(LID), null); // never from @lid
  assert.equal(toE164('123456789@g.us'), null);
});

test('normalizeJid: pn JID strips device suffix and emits +e164; lid yields null', () => {
  assert.deepEqual(normalizeJid('201234567890:12@s.whatsapp.net'), {
    jid_raw: '201234567890:12@s.whatsapp.net',
    addressing: 'pn',
    wa_id: '201234567890',
    phone_e164: '+201234567890',
  });
  assert.deepEqual(normalizeJid('201234567890@c.us'), {
    jid_raw: '201234567890@c.us',
    addressing: 'pn',
    wa_id: '201234567890',
    phone_e164: '+201234567890',
  });
  assert.deepEqual(normalizeJid(LID), {
    jid_raw: LID,
    addressing: 'lid',
    wa_id: LID,
    phone_e164: null,
  });
  assert.equal(normalizeJid(''), null);
});

test('resolvePhoneE164: sender_pn (digits OR JID) wins; lidmap accepts digits OR JID', () => {
  // sender_pn as a JID (the real F1 shape) is authoritative.
  assert.equal(
    resolvePhoneE164({ jid: LID, senderPn: '201111112222@s.whatsapp.net', lidMap: lidMapOf({ [LID]: '201555559999' }) }),
    '+201111112222',
  );
  // sender_pn as bare digits.
  assert.equal(resolvePhoneE164({ jid: LID, senderPn: '201111112222' }), '+201111112222');

  // lidmap value as a JID.
  assert.equal(
    resolvePhoneE164({ jid: LID, lidMap: lidMapOf({ [LID]: '201555559999@s.whatsapp.net' }) }),
    '+201555559999',
  );
  // lidmap value as bare digits.
  assert.equal(resolvePhoneE164({ jid: LID, lidMap: lidMapOf({ [LID]: '201555559999' }) }), '+201555559999');

  // no number -> null (never invented).
  assert.equal(resolvePhoneE164({ jid: LID, lidMap: lidMapOf({}) }), null);
});

test('normalizeIdentity: full G12 priority and E.164 output', () => {
  assert.deepEqual(normalizeIdentity({ remoteJid: LID }), {
    jid_raw: LID,
    addressing: 'lid',
    wa_id: LID,
    phone_e164: null,
  });
  assert.deepEqual(normalizeIdentity({ remoteJid: '201234567890:12@s.whatsapp.net' }), {
    jid_raw: '201234567890:12@s.whatsapp.net',
    addressing: 'pn',
    wa_id: '201234567890',
    phone_e164: '+201234567890',
  });
  assert.deepEqual(normalizeIdentity({ remoteJid: LID, senderPn: '201111112222@s.whatsapp.net' }), {
    jid_raw: LID,
    addressing: 'lid',
    wa_id: LID,
    phone_e164: '+201111112222',
  });
});

test('property: every non-null phone_e164 matches +digits; never derived from @lid digits', () => {
  const cases = [
    normalizeIdentity({ remoteJid: '201234567890@s.whatsapp.net' }),
    normalizeIdentity({ remoteJid: '201234567890:12@s.whatsapp.net' }),
    normalizeIdentity({ remoteJid: '201234567890@c.us' }),
    normalizeIdentity({ remoteJid: LID, senderPn: '201111112222@s.whatsapp.net' }),
    normalizeIdentity({ remoteJid: LID, lidMap: lidMapOf({ [LID]: '201555559999' }) }),
    normalizeIdentity({ remoteJid: LID }),
  ];
  for (const r of cases) {
    if (r.phone_e164 !== null) {
      assert.match(r.phone_e164, /^\+[0-9]{5,15}$/);
    }
  }
  // A lid that happens to contain digits must never yield a number from them.
  assert.equal(normalizeIdentity({ remoteJid: '201234567890@lid' }).phone_e164, null);
});

test('audit §5 scenario: @lid null -> phoneNumberShare updates map -> number present', () => {
  const store = {};
  const lidMap = lidMapOf(store);
  assert.equal(normalizeIdentity({ remoteJid: LID, lidMap }).phone_e164, null);
  // phoneNumberShare stores a JID, not bare digits (F1).
  store[LID] = '201555559999@s.whatsapp.net';
  assert.equal(normalizeIdentity({ remoteJid: LID, lidMap }).phone_e164, '+201555559999');
});
