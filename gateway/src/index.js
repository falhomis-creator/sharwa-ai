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
  setRedisClient,
} from './sessions.js';
import { drainSpool } from './ingest/spool.js';
import { createRedisClient, closeRedisClient } from './redis.js';
import { logger } from './logger.js';
import { config } from './config.js';

const PORT = Number.parseInt(process.env.PORT ?? '4001', 10);
const API_KEY = process.env.SHARWA_AI_GATEWAY_API_KEY ?? '';

const app = express();
app.disable('x-powered-by');
app.use(express.json({ limit: '1mb' }));

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

app.get('/healthz', (req, res) => {
  res.status(200).json({ status: 'ok' });
});

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
    logger.error({ session_id, err: err.message }, '[index] failed to create session');
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
    logger.error({ session_id: req.params.id, err: err.message }, '[index] logout failed');
    return res.status(500).json({ error: 'logout failed' });
  }
});

app.use((req, res) => {
  res.status(404).json({ error: 'not found' });
});

// eslint-disable-next-line no-unused-vars
app.use((err, req, res, next) => {
  if (err) {
    return res.status(400).json({ error: 'invalid JSON body' });
  }
  return next();
});

let drainTimer = null;

async function main() {
  // P0.2: connect to redis-durable once at startup. sessions.js uses this
  // shared client for WAL appends and the lidmap; a connection failure here is
  // NOT fatal to startup (H3: fail loud per-operation, not the whole process) -
  // every inbound message simply spools to disk until Redis is reachable.
  const client = createRedisClient();
  client.on('error', (err) => {
    logger.error({ err: err.message }, '[index] redis-durable client error');
  });
  setRedisClient(client);

  // Periodically replay anything that was spooled while Redis was unreachable.
  drainTimer = setInterval(() => {
    drainSpool(client).then((res) => {
      if (res.replayed > 0 || res.dropped > 0) {
        logger.info(res, '[index] spool drain cycle');
      }
    }).catch((err) => {
      logger.error({ err: err.message }, '[index] spool drain failed');
    });
  }, config.trimIntervalMs);
  drainTimer.unref?.();

  await rehydrateSessions();
  const server = app.listen(PORT, () => {
    logger.info({ port: PORT }, '[index] Sharwa AI gateway listening');
  });

  const shutdown = async (signal) => {
    logger.info({ signal }, '[index] shutting down');
    if (drainTimer) clearInterval(drainTimer);
    server.close();
    await closeRedisClient(client);
    process.exit(0);
  };
  process.on('SIGTERM', () => { shutdown('SIGTERM').catch(() => process.exit(1)); });
  process.on('SIGINT', () => { shutdown('SIGINT').catch(() => process.exit(1)); });
}

export { app };

const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isMain) {
  main().catch((err) => {
    logger.error({ err: err.message }, '[index] fatal startup error');
    process.exit(1);
  });
}
