import crypto from 'node:crypto';
import { pathToFileURL } from 'node:url';
import express from 'express';

import {
  createSession,
  rehydrateSessions,
  enqueueSend,
  getSessionQr,
  getSessionStatus,
  logoutSession,
} from './sessions.js';

const PORT = Number.parseInt(process.env.PORT ?? '4001', 10);
const API_KEY = process.env.SHARWA_AI_GATEWAY_API_KEY ?? '';

const app = express();
app.disable('x-powered-by');
app.use(express.json({ limit: '1mb' }));

// Constant-time comparison of the gateway key (never a direct `===`).
function isValidKey(provided) {
  if (!provided || !API_KEY) return false;
  const a = Buffer.from(provided, 'utf8');
  const b = Buffer.from(API_KEY, 'utf8');
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(a, b);
}

function requireGatewayKey(req, res, next) {
  const provided = req.get('X-API-Key');
  if (!isValidKey(provided)) {
    return res.status(401).json({ error: 'unauthorized' });
  }
  return next();
}

// Liveness probe - intentionally unprotected.
app.get('/healthz', (req, res) => {
  res.status(200).json({ status: 'ok' });
});

// Every /sessions route is protected by X-API-Key.
app.use('/sessions', requireGatewayKey);

app.post('/sessions', async (req, res) => {
  const { session_id } = req.body ?? {};
  if (!session_id || typeof session_id !== 'string') {
    return res.status(400).json({ error: 'session_id (string) is required' });
  }
  try {
    await createSession(session_id);
    const status = await getSessionStatus(session_id);
    return res.status(201).json(status);
  } catch (err) {
    console.error(`[index] failed to create session ${session_id}: ${err.message}`);
    return res.status(500).json({ error: 'failed to create session' });
  }
});

app.get('/sessions/:id/qr', (req, res) => {
  const qr = getSessionQr(req.params.id);
  return res.json({ qr });
});

app.get('/sessions/:id/status', async (req, res) => {
  const status = await getSessionStatus(req.params.id);
  if (!status) return res.status(404).json({ error: 'session not found' });
  return res.json(status);
});

app.post('/sessions/:id/send', (req, res) => {
  const { to, text } = req.body ?? {};
  if (!to || typeof to !== 'string' || !text || typeof text !== 'string') {
    return res.status(400).json({ error: 'to and text (strings) are required' });
  }
  let message_id;
  try {
    message_id = enqueueSend(req.params.id, to, text);
  } catch (err) {
    return res.status(404).json({ error: 'session not found' });
  }
  if (!message_id) {
    return res.status(503).json({ error: 'send queue is full' });
  }
  return res.status(202).json({ message_id });
});

app.post('/sessions/:id/logout', async (req, res) => {
  try {
    await logoutSession(req.params.id);
    return res.status(200).json({ ok: true });
  } catch (err) {
    console.error(`[index] logout failed for ${req.params.id}: ${err.message}`);
    return res.status(500).json({ error: 'logout failed' });
  }
});

// Unknown routes.
app.use((req, res) => {
  res.status(404).json({ error: 'not found' });
});

// JSON body parse errors.
// eslint-disable-next-line no-unused-vars
app.use((err, req, res, next) => {
  if (err) {
    return res.status(400).json({ error: 'invalid JSON body' });
  }
  return next();
});

async function main() {
  await rehydrateSessions();
  app.listen(PORT, () => {
    console.log(`[index] Sharwa AI gateway listening on port ${PORT}`);
  });
}

export { app };

// Only auto-start the server when this file is executed directly (not when it
// is imported by the test suite, which needs the bare Express app).
const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isMain) {
  main().catch((err) => {
    console.error(`[index] fatal startup error: ${err.message}`);
    process.exit(1);
  });
}
