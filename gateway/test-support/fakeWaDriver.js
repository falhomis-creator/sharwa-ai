// gateway/test-support/fakeWaDriver.js
//
// FakeWaSocket / FakeWaDriver: the constitution's ONE named exception (H7,
// P0.2 section) to "no mocks for the thing under test" — WhatsApp itself is
// the one external network this project can never legitimately touch inside
// a test. This is a real, deterministic, in-process substitute for a Baileys
// socket, used to prove the outbound send pipeline (P0.4), the session
// lifecycle state machine (P0.5), and the E2E chaos suite (P0.8) without any
// real WhatsApp connection.
//
// Two hard gates keep this out of production, enforced in TWO independent
// places (defense in depth, H4):
//   1. This file lives under gateway/test-support/, which gateway/.dockerignore
//      excludes from the production image build context entirely — it is not
//      present inside a built container at all.
//   2. createWaDriver() (in gateway/src/driver/waDriver.js) refuses to select
//      the fake driver unless ALL THREE of WA_DRIVER=fake, NODE_ENV=test, and
//      ALLOW_FAKE_WA=1 are set simultaneously — a single flag flipped by
//      accident in a real deployment is not enough to activate it.
//
// Shape: mimics just the slice of the real Baileys socket surface that
// sessions.js / outbound/queue.js actually call, so both can be driven
// through the same `WaDriver` call sites (see driver/waDriver.js) regardless
// of which concrete driver is wired in.

import { EventEmitter } from 'node:events';
import crypto from 'node:crypto';

/**
 * @typedef {object} FakeWaOptions
 * @property {number} [sendLatencyMs]     Simulated network latency per send (default 5).
 * @property {boolean} [autoConnect]      Emit `connection.update` open immediately (default true).
 * @property {string}  [selfId]           The fake account's own WhatsApp id.
 */

/**
 * Create one FakeWaSocket instance — one per fake session, exactly like a
 * real Baileys `makeWASocket()` call returns one socket per session.
 *
 * @param {FakeWaOptions} [opts]
 */
export function makeFakeWaSocket(opts = {}) {
  if (!(process.env.ALLOW_FAKE_WA === '1' && process.env.NODE_ENV === 'test')) {
    // Mirrors the production refusal in driver/waDriver.js at the socket
    // construction site too, so even a direct, mistaken import of this file
    // cannot produce a live fake socket outside a test process (defense in
    // depth — see file header).
    throw new Error(
      'FakeWaSocket refused: requires ALLOW_FAKE_WA=1 and NODE_ENV=test (H7 — never available in production)',
    );
  }

  const sendLatencyMs = opts.sendLatencyMs ?? 5;
  const selfId = opts.selfId ?? '15550000000@s.whatsapp.net';

  const ev = new EventEmitter();
  // Unbounded EventEmitter listener warnings would be noise here: this
  // socket routinely has several long-lived subscribers (session state,
  // outbound worker, test assertions) for its whole lifetime, not a runaway
  // add-without-remove leak (H4 concern is about unbounded *data* growth —
  // `sentMessages` below is what a chaos test inspects and IS bounded by the
  // test's own message count, never left to grow across real use).
  ev.setMaxListeners(50);

  /** @type {Array<{ jid: string, content: object, options: object, sentAt: number }>} */
  const sentMessages = [];

  let closed = false;

  const sock = {
    user: { id: selfId },
    ev,

    /**
     * Mirrors Baileys' `sock.sendMessage(jid, content, options)`. `options`
     * carries `{ messageId }` when the caller pre-generated the WhatsApp
     * message id (the P0.4 pre-registration pattern in outbound/queue.js).
     * Returns a message-receipt-shaped object like the real API.
     */
    async sendMessage(jid, content, options = {}) {
      if (closed) {
        const err = new Error('FakeWaSocket: connection closed');
        err.code = 'CONNECTION_CLOSED';
        throw err;
      }
      if (sendLatencyMs > 0) {
        await new Promise((resolve) => setTimeout(resolve, sendLatencyMs));
      }
      const waMessageId = options.messageId ?? crypto.randomUUID();
      sentMessages.push({ jid, content, options, sentAt: Date.now(), waMessageId });
      const receipt = { key: { id: waMessageId, remoteJid: jid, fromMe: true }, message: content };
      // Simulate a `messages.update` "delivered" receipt shortly after send,
      // exactly like a real WhatsApp delivery ack — P0.4's evt "delivered"
      // type is driven by this in the outbound worker.
      setTimeout(() => {
        if (closed) return;
        ev.emit('messages.update', [{ key: receipt.key, update: { status: 3 /* DELIVERY_ACK */ } }]);
      }, sendLatencyMs + 2);
      return receipt;
    },

    async logout() {
      closed = true;
      ev.emit('connection.update', { connection: 'close', lastDisconnect: { error: { output: { statusCode: 401 } } } });
    },

    /** Test-only inspection surface — never part of the real Baileys API. */
    _fake: {
      sentMessages,
      isClosed: () => closed,
      /** Simulate the remote end closing the connection with a given DisconnectReason-shaped code. */
      simulateClose(statusCode) {
        closed = true;
        ev.emit('connection.update', { connection: 'close', lastDisconnect: { error: { output: { statusCode } } } });
      },
      /** Simulate an inbound message arriving from the (fake) phone. */
      simulateIncoming(msg) {
        ev.emit('messages.upsert', { messages: [msg], type: 'notify' });
      },
    },
  };

  if (opts.autoConnect ?? true) {
    // Deferred to a microtask so the caller can attach `connection.update`
    // listeners (as real sessions.js does, right after makeWASocket returns)
    // before the first event fires.
    queueMicrotask(() => {
      ev.emit('connection.update', { connection: 'open' });
    });
  }

  return sock;
}
