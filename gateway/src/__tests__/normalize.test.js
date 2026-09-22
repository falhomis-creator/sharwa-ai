import test from 'node:test';
import assert from 'node:assert/strict';

import {
  isGroupJid,
  extractText,
  detectMedia,
  detectType,
  shouldIgnoreInbound,
  normalizeMessage,
} from '../ingest/normalize.js';

const PN = '201234567890@s.whatsapp.net';

function msg(key, message, extra = {}) {
  return { key: { remoteJid: PN, fromMe: false, ...key }, message, ...extra };
}

test('detectType classifies every supported type and returns null for empty', () => {
  assert.equal(detectType(msg({}, { conversation: 'hi' })), 'text');
  assert.equal(detectType(msg({}, { extendedTextMessage: { text: 'hi' } })), 'text');
  assert.equal(detectType(msg({}, { imageMessage: {} })), 'image');
  assert.equal(detectType(msg({}, { audioMessage: {} })), 'audio');
  assert.equal(detectType(msg({}, { videoMessage: {} })), 'video');
  assert.equal(detectType(msg({}, { documentMessage: {} })), 'document');
  assert.equal(detectType(msg({}, { locationMessage: { degreesLatitude: 15, degreesLongitude: 44 } })), 'location');
  assert.equal(detectType(msg({}, { contactMessage: {} })), 'contact');
  assert.equal(detectType(msg({}, { contactsArrayMessage: { contacts: [] } })), 'contact');
  assert.equal(detectType(msg({}, { stickerMessage: {} })), 'sticker');
  assert.equal(detectType(msg({}, { reactionMessage: { text: '👍' } })), 'reaction');
  assert.equal(detectType(msg({}, { buttonsResponseMessage: { selectedDisplayText: 'A' } })), 'text');
  assert.equal(detectType(msg({}, { listResponseMessage: { title: 'B' } })), 'text');
  assert.equal(detectType(msg({}, { protocolMessage: {} })), 'unsupported');
  assert.equal(detectType(msg({}, {})), 'unsupported');
  assert.equal(detectType({ key: { remoteJid: PN } }), null);
  assert.equal(detectType(null), null);
});

test('shouldIgnoreInbound no longer drops location/contact/sticker/reaction (G4)', () => {
  assert.equal(shouldIgnoreInbound(msg({}, { locationMessage: { degreesLatitude: 15, degreesLongitude: 44 } })), false);
  assert.equal(shouldIgnoreInbound(msg({}, { contactMessage: { displayName: 'X' } })), false);
  assert.equal(shouldIgnoreInbound(msg({}, { stickerMessage: {} })), false);
  assert.equal(shouldIgnoreInbound(msg({}, { reactionMessage: { text: '👍' } })), false);
  // Still ignored: fromMe, group, broadcast, empty.
  assert.equal(shouldIgnoreInbound(msg({ fromMe: true }, { conversation: 'mine' })), true);
  assert.equal(shouldIgnoreInbound(msg({ remoteJid: '120363000000000000@g.us' }, { conversation: 'grp' })), true);
  assert.equal(shouldIgnoreInbound(msg({ remoteJid: 'status@broadcast' }, { conversation: 'bc' })), true);
  assert.equal(shouldIgnoreInbound(msg({}, { conversation: '' })), false); // text type (even empty) is still content
  assert.equal(shouldIgnoreInbound({ key: { remoteJid: PN, fromMe: false } }), true); // no message
});

test('isGroupJid / extractText / detectMedia keep legacy behavior', () => {
  assert.equal(isGroupJid('120363000000000000@g.us'), true);
  assert.equal(isGroupJid('status@broadcast'), true);
  assert.equal(isGroupJid(PN), false);

  assert.equal(extractText(msg({}, { conversation: 'hello' })), 'hello');
  assert.equal(extractText(msg({}, { imageMessage: { caption: 'pic' } })), 'pic');
  assert.equal(extractText(msg({}, { buttonsResponseMessage: { selectedDisplayText: 'Yes' } })), 'Yes');
  assert.equal(extractText(msg({}, { listResponseMessage: { singleSelectReply: { selectedRowId: 'opt1' } } })), 'opt1');

  assert.equal(detectMedia(msg({}, { imageMessage: {} })).mediaType, 'image');
  assert.equal(detectMedia(msg({}, { conversation: 'hi' })), null);
});

test('normalizeMessage: text with phone-addressed identity', () => {
  const e = normalizeMessage(msg({ id: 'ABC1' }, { conversation: 'أين طلبي؟' }, { messageTimestamp: 1700000000 }));
  assert.equal(e.provider_message_id, 'ABC1');
  assert.equal(e.type, 'text');
  assert.equal(e.text, 'أين طلبي؟');
  assert.equal(e.ts, 1700000000 * 1000);
  assert.deepEqual(e.identity, {
    jid_raw: PN,
    addressing: 'pn',
    wa_id: '201234567890',
    phone_e164: '+201234567890',
  });
});

test('normalizeMessage: location pin keeps its coordinates (G4 reason #1)', () => {
  const e = normalizeMessage(msg(
    { id: 'LOC1' },
    { locationMessage: { degreesLatitude: 15.3694, degreesLongitude: 44.1910, name: 'Sana\'a', address: 'Main st', isLive: true } },
  ));
  assert.equal(e.type, 'location');
  assert.deepEqual(e.location, { lat: 15.3694, lng: 44.1910, name: 'Sana\'a', address: 'Main st', is_live: true });
});

test('normalizeMessage: contact parses name and vCard numbers', () => {
  const vcard = 'BEGIN:VCARD\nVERSION:3.0\nFN:Ali\nTEL;type=CELL;waid=201555559999:+201555559999\nEND:VCARD';
  const e = normalizeMessage(msg({ id: 'CNT1' }, { contactMessage: { displayName: 'Ali', vcard } }));
  assert.equal(e.type, 'contact');
  assert.equal(e.contact.name, 'Ali');
  assert.deepEqual(e.contact.numbers, ['+201555559999', '201555559999']);
});

test('normalizeMessage: reaction carries emoji + target message id', () => {
  const e = normalizeMessage(msg(
    { id: 'R1' },
    { reactionMessage: { text: '👍', key: { remoteJid: PN, fromMe: false, id: 'TARGET1' } } },
  ));
  assert.equal(e.type, 'reaction');
  assert.deepEqual(e.reaction, { text: '👍', target_message_id: 'TARGET1' });
});

test('normalizeMessage: media carries a small metadata reference (no bytes)', () => {
  const e = normalizeMessage(msg(
    { id: 'IMG1' },
    { imageMessage: { mimetype: 'image/jpeg', fileLength: 200000, caption: 'look' } },
  ));
  assert.equal(e.type, 'image');
  assert.equal(e.text, 'look');
  assert.deepEqual(e.media, { kind: 'image', mimetype: 'image/jpeg', fileLength: 200000, fileName: undefined });
  assert.equal(JSON.stringify(e.media).includes('mediaKey'), false, 'media reference must never carry bytes/keys');
});

test('normalizeMessage: @lid identity resolved from sender_pn on the key (F1)', () => {
  const LID = '123456789012345@lid';
  const e = normalizeMessage(msg(
    { remoteJid: LID, id: 'LID1', senderPn: '201111112222@s.whatsapp.net' },
    { conversation: 'hello' },
  ));
  assert.deepEqual(e.identity, {
    jid_raw: LID,
    addressing: 'lid',
    wa_id: LID,
    phone_e164: '+201111112222',
  });
});

test('normalizeMessage: @lid with no sender_pn falls back to the lid map', () => {
  const LID = '123456789012345@lid';
  const lidMap = { get: (lid) => (lid === LID ? '201555559999@s.whatsapp.net' : null) };
  const e = normalizeMessage(msg({ remoteJid: LID, id: 'LID2' }, { conversation: 'hello' }), { lidMap });
  assert.equal(e.identity.phone_e164, '+201555559999');
});

test('normalizeMessage: ignored messages yield null', () => {
  assert.equal(normalizeMessage(msg({ fromMe: true }, { conversation: 'x' })), null);
  assert.equal(normalizeMessage(msg({ remoteJid: '120363000000000000@g.us' }, { conversation: 'x' })), null);
  assert.equal(normalizeMessage({ key: { remoteJid: PN, fromMe: false } }), null);
});
