import test from 'node:test';
import assert from 'node:assert/strict';

import { classifyDisconnect, mapConnectionStatus } from '../sessions.js';
import { DisconnectReason } from '../driver/waDriver.js';

// P0.5 (G6, spec literal: "أكواد الانقطاع الأربعة تنتج الحالات الصحيحة" -
// the four disconnect codes produce the correct states). classifyDisconnect
// is compared against the REAL, installed Baileys DisconnectReason enum
// (imported through driver/waDriver.js, never a guessed numeric literal) -
// see that file's own header for why sessions.js only ever goes through it.

function closeUpdate(statusCode, extra = {}) {
  return { connection: 'close', lastDisconnect: { error: { output: { statusCode } } }, ...extra };
}

test('classifyDisconnect: restartRequired -> restart (reconnect immediately)', () => {
  assert.equal(classifyDisconnect(closeUpdate(DisconnectReason.restartRequired)), 'restart');
});

test('classifyDisconnect: loggedOut -> logged_out (terminal, clears creds)', () => {
  assert.equal(classifyDisconnect(closeUpdate(DisconnectReason.loggedOut)), 'logged_out');
});

test('classifyDisconnect: forbidden (403) -> banned (terminal, emits the status event)', () => {
  assert.equal(classifyDisconnect(closeUpdate(DisconnectReason.forbidden)), 'banned');
});

test('classifyDisconnect: connectionReplaced -> conflict (<=1 reconnect attempt per 60s)', () => {
  assert.equal(classifyDisconnect(closeUpdate(DisconnectReason.connectionReplaced)), 'conflict');
});

test('classifyDisconnect: any other/unknown close reason -> backoff (exponential reconnect)', () => {
  assert.equal(classifyDisconnect(closeUpdate(DisconnectReason.connectionLost)), 'backoff');
  assert.equal(classifyDisconnect(closeUpdate(999999)), 'backoff');
  assert.equal(classifyDisconnect({ connection: 'close' }), 'backoff'); // no statusCode at all
});

// mapConnectionStatus's own external contract (status/qr_image_base64/
// connected_phone_number vocabulary) is frozen by P0.3's F3 finding and must
// stay byte-identical through P0.5 - regression-guarded here explicitly,
// since this file is exactly where a well-intentioned P0.5 change (adding
// new close-reason handling) could accidentally widen it.
test('mapConnectionStatus: P0.5 leaves the frozen status vocabulary untouched', () => {
  assert.equal(mapConnectionStatus({ qr: '2@abc' }), 'QR_PENDING');
  assert.equal(mapConnectionStatus({ connection: 'open' }), 'CONNECTED');
  assert.equal(mapConnectionStatus(closeUpdate(DisconnectReason.forbidden)), 'BANNED');
  assert.equal(mapConnectionStatus(closeUpdate(DisconnectReason.loggedOut)), 'DISCONNECTED');
  assert.equal(mapConnectionStatus(closeUpdate(DisconnectReason.connectionReplaced)), 'DISCONNECTED');
  assert.equal(mapConnectionStatus({}), 'UNKNOWN');
});
