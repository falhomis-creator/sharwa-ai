import test from 'node:test';
import assert from 'node:assert/strict';

import { createSessionRecord, getSession } from '../sessions.js';

// P0.7 (spec, literal): POST /sessions optionally accepts tenant_id/
// channel_account_id/engine. Same light-integration style as contract.test.js
// ("POST /sessions returns 201 with qr_image_base64") - pre-registering the
// session via createSessionRecord short-circuits createSession() before any
// Baileys socket or meta.json write happens, so this is a real HTTP
// round-trip through Express with no WhatsApp connection.

process.env.SHARWA_AI_GATEWAY_API_KEY = 'sharwa-ai-test-api-key-p07';
const { app } = await import('../index.js');

const API_KEY = 'sharwa-ai-test-api-key-p07';

function listen(app) {
  return new Promise((resolve) => {
    const server = app.listen(0, () => resolve({ server, port: server.address().port }));
  });
}

function close(server) {
  return new Promise((resolve) => server.close(resolve));
}

test('POST /sessions accepts tenant_id/channel_account_id/engine without breaking the frozen status contract', async () => {
  createSessionRecord('p07-route-session', {}); // pre-register: short-circuits createSession()

  const { server, port } = await listen(app);
  try {
    const res = await fetch(`http://127.0.0.1:${port}/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
      body: JSON.stringify({
        session_id: 'p07-route-session',
        tenant_id: 'tenant-abc',
        channel_account_id: 'chan-123',
        engine: 'ai_core',
      }),
    });

    assert.equal(res.status, 201);
    const body = await res.json();
    // Frozen GET/POST /sessions status contract (unchanged by P0.7 - additive
    // only): exactly these three keys, nothing about tenant/engine leaks in.
    assert.deepEqual(Object.keys(body).sort(), ['connected_phone_number', 'qr_image_base64', 'status']);
  } finally {
    await close(server);
  }
});

test('POST /sessions rejects a non-string tenant_id/channel_account_id/engine with 400 (VALIDATION_FAILED-shaped)', async () => {
  const { server, port } = await listen(app);
  try {
    const badTenant = await fetch(`http://127.0.0.1:${port}/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
      body: JSON.stringify({ session_id: 'p07-bad-tenant', tenant_id: 12345 }),
    });
    assert.equal(badTenant.status, 400);

    const badEngine = await fetch(`http://127.0.0.1:${port}/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
      body: JSON.stringify({ session_id: 'p07-bad-engine', engine: { nested: 'object' } }),
    });
    assert.equal(badEngine.status, 400);
  } finally {
    await close(server);
  }
});

test('POST /sessions with session_id only (no metadata) still works exactly as before P0.7 (backward compatible)', async () => {
  createSessionRecord('p07-route-legacy-session', {});

  const { server, port } = await listen(app);
  try {
    const res = await fetch(`http://127.0.0.1:${port}/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
      body: JSON.stringify({ session_id: 'p07-route-legacy-session' }),
    });
    assert.equal(res.status, 201);
    const session = getSession('p07-route-legacy-session');
    assert.equal(session.engine, null);
  } finally {
    await close(server);
  }
});
