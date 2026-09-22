// gateway/src/forwarder.js
// P0.2 Forwarder (owner decision, R3_DIRECTIVE): a SEPARATE process (same
// image, different start command - `node src/forwarder.js`, matching the
// `gateway-forwarder` service already budgeted in docker-compose.yml's
// comments) that reads normalized entries from the `in:{shard}` streams via
// consumer group `legacy-forwarder` and delivers them to Django through the
// EXISTING, unchanged webhook.js::postInboundMessage (same HMAC contract).
//
// This is what decouples inbound receipt (fast, fsync'd, never blocks the
// WhatsApp socket - disaster #4/G1) from delivery to Django (which may be
// slow or down - disaster #5/#17). Bounded retry via XAUTOCLAIM; a message
// that still fails after config.forwardMaxAttempts is parked on `dlq:in` and
// logged loudly (F2: never retried forever, never dropped silently).

import { logger } from './logger.js';
import { config } from './config.js';
import { createRedisClient, closeRedisClient } from './redis.js';
import { postInboundMessage } from './webhook.js';

const CONSUMER_NAME = `forwarder-${process.pid}`;
// XREADGROUP's own BLOCK parameter is a SERVER-side wait; redis.js's
// createRedisClient() also sets a CLIENT-side `commandTimeout` (from
// config.redis.timeoutMs, default 5000ms) that aborts ANY command - including
// this intentionally-blocking one - if it doesn't get a reply in time. Setting
// BLOCK_MS equal to (or above) that timeout means the client-side watchdog
// fires at essentially the same instant the server would naturally return an
// empty result, so on an idle stream EVERY cycle raced and lost: ioredis threw
// "Command timed out", the outer loop logged "[forwarder] loop error; backing
// off" and slept 1s, over and over (observed on real Docker - not a crash, but
// a permanent noisy poll instead of a real long-poll, plus an extra Redis round
// trip every ~6s). Keeping a safety margin below the command timeout lets the
// command return (with or without data) before that watchdog can fire.
const BLOCK_MS = Math.max(1000, config.redis.timeoutMs - 1500);
const CLAIM_IDLE_MS = 30_000; // claim entries idle for this long from dead consumers
const BATCH_SIZE = 50;

function streamKeys() {
  return Array.from({ length: config.ingestShards }, (_, i) => `in:${i}`);
}

async function ensureGroup(client, stream) {
  try {
    await client.xgroup('CREATE', stream, config.legacyForwarderGroup, '0', 'MKSTREAM');
  } catch (err) {
    if (!String(err.message).includes('BUSYGROUP')) throw err;
  }
}

/**
 * Wait for the redis-durable client to finish its TCP+AUTH handshake before
 * issuing the first command.
 *
 * redis.js's createRedisClient() deliberately uses `lazyConnect: false` +
 * `enableOfflineQueue: false` (H3: fail fast rather than silently buffer a
 * command in memory). That is the right choice for a per-message operation
 * with a spool fallback (sessions.js's defaultAppendToWal) - but ensureGroup()
 * below is this process's FIRST command, called synchronously right after
 * createRedisClient(). The handshake can never complete before that next line
 * of synchronous code runs, so without this wait the command was NOT racing
 * occasionally - it was guaranteed to fail on every single startup with
 * "Stream isn't writeable and enableOfflineQueue options is false", crash the
 * process (main().catch -> process.exit(1)), and crash-loop forever under
 * `restart: unless-stopped` (observed on real Docker: every restart, no
 * exceptions, regardless of backoff delay - confirming it was ordering, not
 * timing).
 */
function waitForReady(client) {
  if (client.status === 'ready') return Promise.resolve();
  return new Promise((resolve, reject) => {
    const onReady = () => {
      client.off('error', onError);
      resolve();
    };
    const onError = (err) => {
      client.off('ready', onReady);
      reject(err);
    };
    client.once('ready', onReady);
    client.once('error', onError);
  });
}

/**
 * identity_update entries (G12/R3_DIRECTIVE) are bookkeeping for a future P1
 * core consumer reading the stream directly - the legacy Django webhook has
 * no field for a lid/phone resolution, so the forwarder ACKs these without
 * calling postInboundMessage rather than sending a garbage payload (F2: this
 * is a deliberate, logged skip, not a silent drop - see processStream).
 *
 * @param {object} entry
 * @returns {boolean}
 */
function shouldForwardToLegacy(entry) {
  return entry.type !== 'identity_update';
}

/** Build the Django webhook payload from a normalized WAL entry. */
function toWebhookPayload(sessionId, entry) {
  return {
    session_id: sessionId,
    from: entry.identity?.jid_raw ?? null,
    text: entry.text ?? '',
    message_id: entry.provider_message_id,
    media_object_key: entry.media?.object_key ?? null,
    media_type: entry.media?.kind ?? null,
  };
}

/**
 * Attempt to deliver one stream entry. Returns true on success (caller ACKs).
 *
 * @param {import('ioredis').Redis} client
 * @param {string} stream
 * @param {string} id
 * @param {object} data  Parsed `{ v, session_id, ...entry }`.
 */
async function deliverOne(client, stream, id, data) {
  const payload = toWebhookPayload(data.session_id, data);
  const res = await postInboundMessage(payload);
  if (res === null) {
    // webhook.js already retried MAX_ATTEMPTS times internally and logged the
    // final failure. Here we track delivery attempts PER STREAM ENTRY so a
    // message that keeps failing across forwarder restarts is bounded too.
    const attemptsKey = `fwd:attempts:${stream}:${id}`;
    const attempts = await client.incr(attemptsKey);
    await client.expire(attemptsKey, 7 * 24 * 3600);
    if (attempts >= config.forwardMaxAttempts) {
      await client.xadd('dlq:in', '*', 'stream', stream, 'id', id, 'data', JSON.stringify(data));
      logger.error({ stream, id, attempts }, '[forwarder] message parked on dlq:in after max attempts');
      return true; // ACK the original entry - it now lives on the DLQ, not lost (F2)
    }
    logger.warn({ stream, id, attempts, max: config.forwardMaxAttempts }, '[forwarder] delivery failed; will retry');
    return false;
  }
  return true;
}

function parseFields(fields) {
  // fields is a flat [k1, v1, k2, v2, ...] array from ioredis xreadgroup.
  const obj = {};
  for (let i = 0; i < fields.length; i += 2) obj[fields[i]] = fields[i + 1];
  return obj;
}

async function processStream(client, stream) {
  // 1) Claim anything abandoned by a dead consumer first (bounded retry path).
  const claimed = await client.xautoclaim(
    stream, config.legacyForwarderGroup, CONSUMER_NAME, CLAIM_IDLE_MS, '0', 'COUNT', BATCH_SIZE,
  );
  const claimedEntries = claimed?.[1] ?? [];

  // 2) Read new entries for this consumer.
  const read = await client.xreadgroup(
    'GROUP', config.legacyForwarderGroup, CONSUMER_NAME,
    'COUNT', BATCH_SIZE, 'BLOCK', BLOCK_MS, 'STREAMS', stream, '>',
  );
  const freshEntries = read?.[0]?.[1] ?? [];

  const all = [...claimedEntries, ...freshEntries];
  for (const [id, fields] of all) {
    if (!fields || fields.length === 0) continue; // already-deleted entry from XAUTOCLAIM
    let data;
    try {
      const raw = parseFields(fields);
      data = JSON.parse(raw.data);
    } catch (err) {
      logger.error({ stream, id, err: err.message }, '[forwarder] corrupt stream entry; ACKing to avoid poison-pill loop');
      await client.xack(stream, config.legacyForwarderGroup, id);
      continue;
    }

    if (!shouldForwardToLegacy(data)) {
      logger.info({ stream, id, type: data.type }, '[forwarder] skipping legacy delivery for non-message entry');
      await client.xack(stream, config.legacyForwarderGroup, id);
      continue;
    }

    let ok = false;
    try {
      ok = await deliverOne(client, stream, id, data);
    } catch (err) {
      logger.error({ stream, id, err: err.message }, '[forwarder] unexpected delivery error');
    }
    if (ok) {
      await client.xack(stream, config.legacyForwarderGroup, id);
    }
  }
}

let running = true;

async function main() {
  const client = createRedisClient();
  client.on('error', (err) => {
    logger.error({ err: err.message }, '[forwarder] redis-durable client error');
  });

  await waitForReady(client);

  for (const stream of streamKeys()) {
    await ensureGroup(client, stream);
  }

  logger.info({ streams: streamKeys(), group: config.legacyForwarderGroup, consumer: CONSUMER_NAME }, '[forwarder] started');

  const shutdown = async (signal) => {
    logger.info({ signal }, '[forwarder] shutting down');
    running = false;
  };
  process.on('SIGTERM', () => { shutdown('SIGTERM'); });
  process.on('SIGINT', () => { shutdown('SIGINT'); });

  while (running) {
    try {
      for (const stream of streamKeys()) {
        if (!running) break;
        await processStream(client, stream);
      }
    } catch (err) {
      logger.error({ err: err.message }, '[forwarder] loop error; backing off');
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }

  await closeRedisClient(client);
  process.exit(0);
}

export { toWebhookPayload, deliverOne, streamKeys, shouldForwardToLegacy, waitForReady, BLOCK_MS };

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => {
    logger.error({ err: err.message }, '[forwarder] fatal startup error');
    process.exit(1);
  });
}
