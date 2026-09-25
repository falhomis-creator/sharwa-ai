import crypto from 'node:crypto';
import { pathToFileURL } from 'node:url';
import express from 'express';

import {
  createSession,
  rehydrateSessions,
  enqueueSend,
  getSessionQr,
  getSessionStatus,
  getSessionHealth,
  logoutSession,
  setRedisClient,
  startLeaseSweep,
  releaseAllOwnedLeases,
  getSessionCount,
} from './sessions.js';
import { drainSpool } from './ingest/spool.js';
import { createRedisClient, closeRedisClient } from './redis.js';
import { logger } from './logger.js';
import { config } from './config.js';
import { registry } from './metrics.js';
import { getMediaMetrics } from './media.js';

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

// P0.6 (spec, literal): a bearer token in the SAME timing-safe-compare style
// as requireGatewayKey above. isValidMetricsToken returning false whenever
// config.metricsToken is empty is a second line of defense only - the real
// enforcement is main() refusing to start at all with an empty token (H5).
function isValidMetricsToken(provided) {
  if (!provided || !config.metricsToken) return false;
  const a = Buffer.from(provided, 'utf8');
  const b = Buffer.from(config.metricsToken, 'utf8');
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(a, b);
}

// P0.6: /healthz stays frozen (spec, literal) - liveness only, never gated on
// dependencies. /readyz below is the new, additive readiness signal.
app.get('/healthz', (req, res) => {
  res.status(200).json({ status: 'ok' });
});

// P0.6 (spec, literal): 503 if redis-durable is unreachable, OR the spool has
// been non-empty for longer than SPOOL_STALE_S, OR MAX_SESSIONS is reached,
// OR graceful shutdown has already started.
app.get('/readyz', (req, res) => {
  const reasons = [];
  if (isShuttingDown) reasons.push('shutting_down');
  if (!redisClientRef || redisClientRef.status !== 'ready') reasons.push('redis_durable_unreachable');
  if (spoolNonEmptySince !== null && (Date.now() - spoolNonEmptySince) > config.readyz.spoolStaleS * 1000) {
    reasons.push('spool_stale');
  }
  if (getSessionCount() >= config.maxSessions) reasons.push('at_capacity');

  if (reasons.length > 0) {
    return res.status(503).json({ status: 'not_ready', reasons });
  }
  return res.status(200).json({ status: 'ready' });
});

// P0.6 (spec, literal): Bearer-token-protected; an empty/missing METRICS_TOKEN
// refuses gateway startup entirely (main(), below) rather than ever serving
// this route unauthenticated.
app.get('/metrics', async (req, res) => {
  const auth = req.get('Authorization') || '';
  const provided = auth.startsWith('Bearer ') ? auth.slice('Bearer '.length) : '';
  if (!isValidMetricsToken(provided)) {
    return res.status(401).json({ error: 'unauthorized' });
  }
  res.set('Content-Type', registry.contentType);
  return res.send(await registry.metrics());
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
    // P0.5: MAX_SESSIONS capacity and a lease held by another instance are
    // both expected, well-defined outcomes (spec, literal: capacity -> a
    // clean 503) - never generic 500s.
    if (err.code === 'CAPACITY') {
      return res.status(503).json({ error: 'gateway at capacity (MAX_SESSIONS)' });
    }
    if (err.code === 'LEASE_HELD') {
      return res.status(409).json({ error: 'session is active on another gateway instance' });
    }
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

// P0.5 (G6/G7/G8): additive endpoint - GET /sessions/:id/status's own
// contract ({status, qr_image_base64, connected_phone_number}) is frozen and
// unchanged. This surfaces the new lifecycle detail: reconnect/lease state,
// queue depth, spool status, and the (P0.6-stub) kill-switch flag.
app.get('/sessions/:id/health', async (req, res) => {
  const health = await getSessionHealth(req.params.id);
  if (!health) return res.status(404).json({ error: 'session not found' });
  return res.json(health);
});

// P0.4: the send route now accepts an optional `client_msg_id` (idempotency
// key - a caller that retries a request after a timeout should reuse the
// same client_msg_id, per the spec's idempotent-enqueue contract) and an
// optional `kind` ('interactive' | 'bulk' | 'marketing', selecting pacing +
// token-bucket category - see gateway/src/outbound/queue.js). Both are
// optional for backward compatibility: an omitted client_msg_id gets a
// server-generated one (no idempotency across retries in that case - the
// caller opted out by not sending one).
//
// The queue-full response changed from the old in-RAM queue's 503 to a clean
// 429 (P0.4's own acceptance text: "طابور ممتلئ ⇒ 429 نظيف") - see
// docs/P0_DEVIATIONS.md.
app.post('/sessions/:id/send', async (req, res) => {
  const { to, text, client_msg_id, kind } = req.body ?? {};
  if (!to || typeof to !== 'string' || !text || typeof text !== 'string') {
    return res.status(400).json({ error: 'to and text (strings) are required' });
  }
  if (client_msg_id !== undefined && typeof client_msg_id !== 'string') {
    return res.status(400).json({ error: 'client_msg_id, if provided, must be a string' });
  }
  if (kind !== undefined && !['interactive', 'bulk', 'marketing'].includes(kind)) {
    return res.status(400).json({ error: "kind, if provided, must be one of 'interactive', 'bulk', 'marketing'" });
  }

  let result;
  try {
    result = await enqueueSend(req.params.id, to, text, {
      clientMsgId: client_msg_id || undefined,
      kind: kind || undefined,
    });
  } catch (err) {
    if (err.message && err.message.startsWith('Unknown session')) {
      return res.status(404).json({ error: 'session not found' });
    }
    logger.error({ session_id: req.params.id, err: err.message }, '[index] enqueueSend failed');
    return res.status(500).json({ error: 'failed to enqueue message' });
  }

  if (result.status === 'full') {
    return res.status(429).json({ error: 'send queue is full' });
  }
  if (result.status === 'duplicate') {
    // Idempotent replay: the original enqueue already happened (or is in
    // flight/sent) - report success with the same client_msg_id rather than
    // enqueueing a second copy.
    return res.status(202).json({
      message_id: result.client_msg_id,
      client_msg_id: result.client_msg_id,
      duplicate: true,
      state: result.state,
    });
  }
  return res.status(202).json({ message_id: result.client_msg_id, client_msg_id: result.client_msg_id });
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
// P0.6: module-level state the /readyz and /metrics routes (defined above,
// before main() runs) read from - all null/false until main() sets them up.
let redisClientRef = null;
let spoolNonEmptySince = null;
let isShuttingDown = false;

/** P0.6 (spec, literal): poll GET_MEDIA_METRICS().inflight until it drains to 0 or timeoutMs elapses - never blocks shutdown forever on a stuck download. */
async function waitForInflightDownloadsToDrain(timeoutMs) {
  const start = Date.now();
  for (;;) {
    const { inflight } = getMediaMetrics();
    if (inflight <= 0) return;
    if (Date.now() - start > timeoutMs) {
      logger.warn({ inflight, timeoutMs }, '[index] shutdown: in-flight downloads did not drain within budget, proceeding anyway');
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

async function main() {
  // P0.6 (spec, literal): "سرّ فارغ = رفض إقلاع" - refuse to start at all with
  // an empty/missing METRICS_TOKEN (H5), the same posture already enforced
  // for SHARWA_AI_GATEWAY_API_KEY. Deliberately NOT enforced inside
  // config.js's eager loadConfig() - see that file's own comment on
  // `metricsToken` for why; this is the one place it matters as a live
  // security control.
  if (!config.metricsToken) {
    throw new Error('main: METRICS_TOKEN is required and must be non-empty (H5: empty secret refuses startup)');
  }

  // P0.2: connect to redis-durable once at startup. sessions.js uses this
  // shared client for WAL appends and the lidmap; a connection failure here is
  // NOT fatal to startup (H3: fail loud per-operation, not the whole process) -
  // every inbound message simply spools to disk until Redis is reachable.
  const client = createRedisClient();
  redisClientRef = client;
  client.on('error', (err) => {
    logger.error({ err: err.message }, '[index] redis-durable client error');
  });
  setRedisClient(client);

  // Periodically replay anything that was spooled while Redis was unreachable.
  // P0.6: also tracks spoolNonEmptySince for GET /readyz's staleness check -
  // set the moment the spool is first observed non-empty, cleared the moment
  // it drains back to empty (drainSpool's own `remaining` count is the source
  // of truth - no extra fs/Redis call needed here).
  drainTimer = setInterval(() => {
    drainSpool(client).then((res) => {
      if (res.replayed > 0 || res.dropped > 0) {
        logger.info(res, '[index] spool drain cycle');
      }
      if (res.remaining > 0) {
        if (spoolNonEmptySince === null) spoolNonEmptySince = Date.now();
      } else {
        spoolNonEmptySince = null;
      }
    }).catch((err) => {
      logger.error({ err: err.message }, '[index] spool drain failed');
    });
  }, config.trimIntervalMs);
  drainTimer.unref?.();

  await rehydrateSessions();
  // P0.5 (G7): once staged rehydrate has claimed every session this instance
  // could grab immediately, keep periodically re-scanning for one whose
  // lease has since expired elsewhere (the other instance died, or released
  // it on its own graceful shutdown) - this is the actual failover mechanic.
  const leaseSweep = startLeaseSweep();

  const server = app.listen(PORT, () => {
    logger.info({ port: PORT }, '[index] Sharwa AI gateway listening');
  });

  const shutdown = async (signal) => {
    logger.info({ signal }, '[index] shutting down');
    isShuttingDown = true; // flips GET /readyz to 503 immediately (spec, literal)

    // P0.6 (spec, literal): whatever step below hangs, the process must still
    // exit within its own configured budget - itself kept comfortably under
    // the compose stop_grace_period (config.js) so Docker never has to
    // SIGKILL it. A safety net, not the expected path.
    const forceExitTimer = setTimeout(() => {
      logger.error({ signal }, '[index] graceful shutdown exceeded its budget - forcing exit');
      process.exit(1);
    }, config.shutdown.timeoutMs);
    forceExitTimer.unref?.();

    // 1) Stop accepting new HTTP requests FIRST (spec, literal order).
    if (drainTimer) clearInterval(drainTimer);
    leaseSweep.stop();
    await new Promise((resolve) => server.close(() => resolve()));

    // 2) Let in-flight media downloads finish, time-bounded.
    await waitForInflightDownloadsToDrain(config.shutdown.downloadDrainTimeoutMs);

    // 3) Close every session's socket WITHOUT logout() and release every
    // lease this instance owns (releaseAllOwnedLeases does both - see its
    // own doc comment), then flush the spool one last time so nothing
    // spooled during shutdown is left stranded on disk.
    await releaseAllOwnedLeases();
    await drainSpool(client).catch((err) => {
      logger.error({ err: err.message }, '[index] final spool flush failed');
    });

    await closeRedisClient(client);
    clearTimeout(forceExitTimer);
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
