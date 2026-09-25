// gateway/src/driver/waDriver.js
//
// WaDriver selection point (closes OQ-7: this abstraction was referenced by
// P0.2's own architecture section and by P0.3/P0.4/P0.5's acceptance texts,
// but never actually built until now — docs/P0_OPEN_QUESTIONS.md OQ-7).
//
// sessions.js calls ONLY createWaSocket() below; it never imports
// makeWASocket (Baileys) or makeFakeWaSocket (test-support) directly. This
// is the single seam that lets P0.4/P0.5/P0.8's acceptance tests exercise
// the real outbound queue / session state machine code against a fake
// WhatsApp network, while production always gets the real one.
//
// H7's production refusal is enforced HERE, not just inside fakeWaDriver.js
// (defense in depth, H4): selecting the fake driver requires all three of
// WA_DRIVER=fake, NODE_ENV=test and ALLOW_FAKE_WA=1 simultaneously. Any other
// combination — including WA_DRIVER=fake alone in a misconfigured prod env —
// silently and safely falls through to the real Baileys driver instead of
// throwing, because refusing startup over a stray test-only env var in
// production would itself be a worse failure mode than "ignore it and use
// the real driver" (H3: choose the safer failure direction).

import makeWASocket, {
  DisconnectReason,
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  Browsers,
  downloadMediaMessage,
} from '@whiskeysockets/baileys';

function fakeDriverAllowed() {
  return process.env.WA_DRIVER === 'fake'
    && process.env.NODE_ENV === 'test'
    && process.env.ALLOW_FAKE_WA === '1';
}

/**
 * Construct a WhatsApp socket — real (Baileys) unless the fake driver is
 * both requested AND permitted (see fakeDriverAllowed above).
 *
 * @param {object} opts
 * @param {object} opts.authState    `{ state, saveCreds }` from useMultiFileAuthState.
 * @param {object} [opts.version]    Baileys protocol version, if resolved.
 * @param {object} [opts.fakeOptions] Passed through to makeFakeWaSocket (test only).
 * @returns {Promise<object>} a socket exposing { user, ev, sendMessage, logout }.
 */
export async function createWaSocket({ authState, version, fakeOptions } = {}) {
  if (fakeDriverAllowed()) {
    // Dynamic import: keeps test-support/ out of the real driver's static
    // import graph entirely, so nothing in a production bundle/trace even
    // references the fake module unless this exact env combination is live.
    const { makeFakeWaSocket } = await import('../../test-support/fakeWaDriver.js');
    return makeFakeWaSocket(fakeOptions);
  }
  return makeWASocket({
    ...(version ? { version } : {}),
    printQRInTerminal: false,
    browser: Browsers.ubuntu('Chrome'),
    auth: authState.state,
  });
}

export { DisconnectReason, useMultiFileAuthState, fetchLatestBaileysVersion, downloadMediaMessage };
