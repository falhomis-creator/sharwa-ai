// gateway/src/redis.js
// Redis client factory + error classification for the durable tier.
//
// Why ioredis: the gateway needs Streams (XADD/XACK/XAUTOCLAIM/XTRIM), consumer
// groups, and atomic Lua (EVAL) — ioredis exposes all of these with a Promise
// API and stable reconnect semantics. See docs/P0_DEPENDENCIES.md (H11).
//
// Constitution H3: no error is swallowed. The ingest path treats ANY XADD failure
// as "spool it" (disaster #5/#17), so the client is configured to fail fast on a
// single command rather than buffer it in an offline queue that could silently
// drop a message the caller has already told the world was "safe".

import { Redis } from 'ioredis';
import { config } from './config.js';

/**
 * Build a redis-durable client. Every command has a per-request retry cap of 1
 * and the offline queue is disabled, so a downed Redis surfaces an error to the
 * caller (which then spools) instead of silently accumulating memory.
 *
 * @param {typeof config.redis} [redisConfig]
 * @returns {import('ioredis').Redis}
 */
export function createRedisClient(redisConfig = config.redis) {
  return new Redis({
    host: redisConfig.host,
    port: redisConfig.port,
    password: redisConfig.password || undefined,
    connectTimeout: redisConfig.timeoutMs,
    commandTimeout: redisConfig.timeoutMs,
    maxRetriesPerRequest: 1,
    retryStrategy: (times) => {
      // Stop reconnecting after the configured cap and let the caller decide
      // (spool / fail-closed). ioredis retryStrategy returning null stops retries.
      if (times > redisConfig.maxRetries) return null;
      return Math.min(200 * 2 ** (times - 1), 2000);
    },
    enableOfflineQueue: false,
    lazyConnect: false,
  });
}

/**
 * Classify a Redis error for a caller that must choose a recovery path (H3).
 *
 * @param {unknown} err
 * @returns {'retryable'|'permanent'|'fatal'}
 *  - `retryable`: transient connection/timeout — a bounded retry or spool is right.
 *  - `permanent`: Redis rejected the command for a policy reason (e.g. OOM under
 *    `noeviction`, wrong type) — retrying the same command will keep failing.
 *  - `fatal`: unknown — treat as retryable-but-unsafe, so the caller spools and
 *    logs loudly rather than guessing.
 */
export function classifyRedisError(err) {
  const code = err?.code ?? '';
  const message = String(err?.message ?? '');
  if (code === 'ECONNREFUSED' || code === 'ECONNRESET' || code === 'ETIMEDOUT'
    || code === 'EAI_AGAIN' || /connection is closed|connect ETIMEDOUT|READONLY/i.test(message)) {
    return 'retryable';
  }
  if (code === 'OOM' || /OOM command not allowed|command not allowed|maxmemory|noeviction|WRONGTYPE/i.test(message)) {
    return 'permanent';
  }
  return 'fatal';
}

/**
 * Gracefully close a client. Used by tests and the graceful-shutdown path.
 * @param {import('ioredis').Redis} client
 */
export async function closeRedisClient(client) {
  if (!client) return;
  try {
    await client.quit();
  } catch {
    client.disconnect();
  }
}
