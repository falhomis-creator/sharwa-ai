import test from 'node:test';
import assert from 'node:assert/strict';

import { createSessionRecord } from '../sessions.js';

// P0.7: light Express-level integration for POST /sessions/:id/reconnect,
// same style as contract.test.js's own POST /sessions test - pre-registering
// via createSessionRecord means this exercises the "already_active" no-op
// path with no real Baileys socket needed.

process.env.SHARWA_AI_GATEWAY_API_KEY = 'sharwa-ai-test-api-key-p07-reconnect';
const { app } = await import('../index.js');

const API_KEY = 'sharwa-ai-test-api-key-p07-reconnect';

function listen(app) {
  return new Promise((resolve) => {
    const server = app.listen(0, () => resolve({ server, port: server.address().port }));
  });
}

function close(server) {
  return new Promise((resolve) => server.close(resolve));
}

test('POST /sessions/:id/reconnect requires the gateway API key', async () => {
  const { server, port } = await listen(app);
  try {
    const res = await fetch(`http://127.0.0.1:${port}/sessions/whatever/reconnect`, { method: 'POST' });
    assert.equal(res.status, 401);
  } finally {
    await close(server);
  }
});

test('POST /sessions/:id/reconnect returns the frozen status shape for an already-active session', async () => {
  const session = createSessionRecord('p07-reconnect-route-active', { sock: { fake: true } });
  session.status = 'CONNECTED';
  session.phoneNumber = '201234567890';

  const { server, port } = await listen(app);
  try {
    const res = await fetch(`http://127.0.0.1:${port}/sessions/p07-reconnect-route-active/reconnect`, {
      method: 'POST',
      headers: { 'X-API-Key': API_KEY },
    });
    assert.equal(res.status, 200);
    const body = await res.json();
    assert.deepEqual(Object.keys(body).sort(), ['connected_phone_number', 'qr_image_base64', 'status']);
    assert.equal(body.status, 'CONNECTED');
  } finally {
    await close(server);
  }
});
