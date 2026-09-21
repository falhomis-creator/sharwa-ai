import test from 'node:test';
import assert from 'node:assert/strict';

import {
  createSessionRecord,
  getSessionStatus,
  toQrImageBase64,
} from '../sessions.js';

// index.js captures process.env.SHARWA_AI_GATEWAY_API_KEY at module load time,
// so the key must be set BEFORE the module is imported. We import it lazily
// here: the bare Express app is exported for tests and no server is auto-started
// on import (see the isMain guard in index.js).
process.env.SHARWA_AI_GATEWAY_API_KEY = 'sharwa-ai-test-api-key';
const { app } = await import('../index.js');

const API_KEY = 'sharwa-ai-test-api-key';

function listen(app) {
  return new Promise((resolve) => {
    const server = app.listen(0, () => {
      resolve({ server, port: server.address().port });
    });
  });
}

function close(server) {
  return new Promise((resolve) => server.close(resolve));
}

// Test (new #1): authentication header contract. A valid X-API-Key passes, while
// the legacy X-Gateway-Key name is rejected with 401 — proving the fix is real.
test('auth header: X-API-Key passes, legacy X-Gateway-Key is rejected with 401', async () => {
  const { server, port } = await listen(app);
  try {
    const url = `http://127.0.0.1:${port}/sessions/unknown/status`;

    const okRes = await fetch(url, { headers: { 'X-API-Key': API_KEY } });
    assert.notEqual(okRes.status, 401, 'a valid X-API-Key must pass auth (not 401)');

    const legacyRes = await fetch(url, { headers: { 'X-Gateway-Key': API_KEY } });
    assert.equal(legacyRes.status, 401, 'the legacy X-Gateway-Key header must be rejected with 401');
  } finally {
    await close(server);
  }
});

// Test (new #3): toQrImageBase64 turns raw QR text into a base64 PNG with no
// "data:" prefix; null/empty input yields null.
test('toQrImageBase64 returns a valid base64 PNG without the data: prefix', async () => {
  const b64 = await toQrImageBase64('2@some-qr-payload');
  assert.equal(typeof b64, 'string');
  assert.ok(b64.length > 0);
  assert.equal(b64.startsWith('data:'), false, 'must not include the data: prefix');

  const buf = Buffer.from(b64, 'base64');
  assert.ok(buf.length > 8, 'decoded PNG must be non-trivial');
  assert.equal(buf.subarray(0, 4).toString('hex'), '89504e47', 'must decode to a real PNG image');

  assert.equal(await toQrImageBase64(null), null);
  assert.equal(await toQrImageBase64(''), null);
});

// Test (new #4): getSessionStatus returns exactly the three Django contract keys
// (status, qr_image_base64, connected_phone_number) with their literal names.
test('getSessionStatus returns exactly status/qr_image_base64/connected_phone_number', async () => {
  const session = createSessionRecord('status-shape-session');
  session.status = 'QR_PENDING';
  session.lastQr = '2@some-qr-payload';
  session.phoneNumber = '201234567890';

  const status = await getSessionStatus('status-shape-session');

  assert.deepEqual(
    Object.keys(status).sort(),
    ['connected_phone_number', 'qr_image_base64', 'status'],
  );
  assert.equal(status.status, 'QR_PENDING');
  assert.equal(status.connected_phone_number, '201234567890');
  assert.ok(typeof status.qr_image_base64 === 'string' && status.qr_image_base64.length > 0);
});

// Test (new #5): POST /sessions via Express (light integration). Pre-registering
// the session short-circuits createSession() (it returns the existing record
// before opening any Baileys socket), so this is a real HTTP round-trip with no
// WhatsApp connection. The 201 body must include qr_image_base64.
test('POST /sessions returns 201 with qr_image_base64 (not just session_id)', async () => {
  const session = createSessionRecord('express-session');
  session.status = 'QR_PENDING';
  session.lastQr = '2@some-qr-payload';
  session.phoneNumber = '201234567890';

  const { server, port } = await listen(app);
  try {
    const res = await fetch(`http://127.0.0.1:${port}/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
      body: JSON.stringify({ session_id: 'express-session' }),
    });

    assert.equal(res.status, 201);
    const body = await res.json();
    assert.ok(
      typeof body.qr_image_base64 === 'string' && body.qr_image_base64.length > 0,
      '201 body must include qr_image_base64',
    );
    assert.equal(body.session_id, undefined, 'Django does not require session_id in this response');
  } finally {
    await close(server);
  }
});
