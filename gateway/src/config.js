// gateway/src/config.js
// All P0.2 limits, read from the environment with documented defaults.
//
// Constitution H4 ("every queue, buffer, payload, file, retry count, wait time
// has a written cap"): every tunable below has a sane, documented default and is
// overridable via the environment so that no array/map/buffer can grow without
// bound. Startup validation is strict: an invalid (non-numeric / negative) value
// refuses to load rather than silently falling back and breaking a safety cap.

/**
 * Parse a non-negative integer from an environment value, or throw.
 * @param {string|undefined} raw   Raw environment string.
 * @param {string} name            Variable name (for the error message).
 * @param {number} defaultValue    Value used when `raw` is empty/undefined.
 * @returns {number}
 */
function intFrom(raw, name, defaultValue) {
  if (raw === undefined || raw === '') return defaultValue;
  const n = Number(raw);
  if (!Number.isInteger(n) || n < 0) {
    throw new Error(`config: ${name} must be a non-negative integer (got ${JSON.stringify(raw)})`);
  }
  return n;
}

/** @returns {{ value: number, raw: string }} */
function intPair(raw, name, defaultValue) {
  return { value: intFrom(raw, name, defaultValue), raw: raw ?? String(defaultValue) };
}

/**
 * Load and validate the P0.2 configuration.
 *
 * @param {NodeJS.ProcessEnv} [env] Environment to read from (injectable for tests).
 * @returns {Readonly<ReturnType<typeof buildConfig>>}
 */
export function loadConfig(env = process.env) {
  const problems = [];
  const config = buildConfig(env, problems);
  if (problems.length > 0) {
    throw new Error(`config: ${problems.join('; ')}`);
  }
  return config;
}

function buildConfig(env, problems) {
  const read = (name, def) => (env[name] === undefined || env[name] === '' ? def : env[name]);

  const ingestShards = intFrom(env.INGEST_SHARDS, 'INGEST_SHARDS', 4);
  if (ingestShards < 1) problems.push('INGEST_SHARDS must be >= 1');

  // F4 (owner decision #3): the gateway hard-rejects any single message value
  // larger than this BEFORE it reaches redis-durable. 64KB is far above any real
  // WhatsApp text/contact/location message; media payloads never become a Redis
  // value (only a small object-key reference). The Redis-side ceiling
  // (proto-max-bulk-len / client-query-buffer-limit = 8mb) is a separate, deeper
  // defense that rejects a runaway write as a protocol error.
  const maxValueBytes = intFrom(env.MAX_VALUE_BYTES, 'MAX_VALUE_BYTES', 65536);
  if (maxValueBytes < 1) problems.push('MAX_VALUE_BYTES must be >= 1');

  const spoolDir = read('SPOOL_DIR', '/data/spool');
  const spoolMaxMb = intFrom(env.SPOOL_MAX_MB, 'SPOOL_MAX_MB', 200);
  if (spoolMaxMb < 1) problems.push('SPOOL_MAX_MB must be >= 1');

  // G12/audit §5: per-session lid->phone map on redis-durable. A lid that is not
  // in the map simply yields phone_e164=null; the map never grows without bound.
  const lidmapMax = intFrom(env.LIDMAP_MAX, 'LIDMAP_MAX', 50000);
  if (lidmapMax < 1) problems.push('LIDMAP_MAX must be >= 1');

  const dedupePendingTtlS = intFrom(env.DEDUPE_PENDING_TTL_S, 'DEDUPE_PENDING_TTL_S', 60);
  const dedupeDoneTtlS = intFrom(env.DEDUPE_DONE_TTL_S, 'DEDUPE_DONE_TTL_S', 172800);
  if (dedupePendingTtlS < 1) problems.push('DEDUPE_PENDING_TTL_S must be >= 1');
  if (dedupeDoneTtlS < 1) problems.push('DEDUPE_DONE_TTL_S must be >= 1');

  // Forwarder (G1): bounded retry with XAUTOCLAIM; after this many attempts a
  // message is parked on `dlq:in` and an alert is raised — never retried forever.
  const forwardMaxAttempts = intFrom(env.FORWARD_MAX_ATTEMPTS, 'FORWARD_MAX_ATTEMPTS', 10);
  if (forwardMaxAttempts < 1) problems.push('FORWARD_MAX_ATTEMPTS must be >= 1');

  // Trimmer: every trimIntervalMs we XTRIM each stream to a MINID no older than
  // the oldest unacked id across all consumer groups (including core-ingest),
  // and never older than streamMaxAgeMs.
  const trimIntervalMs = intFrom(env.TRIM_INTERVAL_MS, 'TRIM_INTERVAL_MS', 60000);
  const streamMaxAgeMs = intFrom(env.STREAM_MAX_AGE_MS, 'STREAM_MAX_AGE_MS', 7 * 24 * 3600 * 1000);
  if (trimIntervalMs < 1000) problems.push('TRIM_INTERVAL_MS must be >= 1000');
  if (streamMaxAgeMs < 1000) problems.push('STREAM_MAX_AGE_MS must be >= 1000');

  // Bounded ingest concurrency (owner decision #2): a fixed-size pool of
  // in-flight XADDs across `in:{shard}` shards (never one sequential await chain).
  const ingestPoolSize = intFrom(env.INGEST_POOL_SIZE, 'INGEST_POOL_SIZE', 8);
  if (ingestPoolSize < 1) problems.push('INGEST_POOL_SIZE must be >= 1');

  // Redis connection. Host/port/password are validated lazily by redis.js at
  // connection time (H3: fail loud, never guess). Defaults target the local
  // data tier exposed for tests / a compose network alias `redis-durable`.
  const redisHost = read('REDIS_DURABLE_HOST', '127.0.0.1');
  const redisPort = intFrom(env.REDIS_DURABLE_PORT, 'REDIS_DURABLE_PORT', 6379);
  const redisPassword = read('REDIS_DURABLE_PASSWORD', '');
  const redisTimeoutMs = intFrom(env.REDIS_TIMEOUT_MS, 'REDIS_TIMEOUT_MS', 5000);
  const redisMaxRetries = intFrom(env.REDIS_MAX_RETRIES, 'REDIS_MAX_RETRIES', 3);
  if (redisPort < 1 || redisPort > 65535) problems.push('REDIS_DURABLE_PORT out of range');
  if (redisTimeoutMs < 100) problems.push('REDIS_TIMEOUT_MS must be >= 100');
  if (redisMaxRetries < 0) problems.push('REDIS_MAX_RETRIES must be >= 0');

  // The consumer group that the gateway creates at startup and that P1's
  // core-ingest worker will later read from. Fixed by the architecture; not a
  // tunable. Legacy sessions are consumed by `legacy-forwarder` (forwarder.js).
  const coreIngestGroup = 'core-ingest';
  const legacyForwarderGroup = 'legacy-forwarder';

  return Object.freeze({
    ingestShards,
    maxValueBytes,
    spoolDir,
    spoolMaxMb,
    lidmapMax,
    dedupePendingTtlS,
    dedupeDoneTtlS,
    forwardMaxAttempts,
    trimIntervalMs,
    streamMaxAgeMs,
    ingestPoolSize,
    redis: Object.freeze({
      host: redisHost,
      port: redisPort,
      password: redisPassword,
      timeoutMs: redisTimeoutMs,
      maxRetries: redisMaxRetries,
    }),
    coreIngestGroup,
    legacyForwarderGroup,
    // Recorded for H4 documentation / P0_REPORT: the exact raw values in force.
    _raw: Object.freeze({
      INGEST_SHARDS: intPair(env.INGEST_SHARDS, 'INGEST_SHARDS', 4).raw,
      MAX_VALUE_BYTES: intPair(env.MAX_VALUE_BYTES, 'MAX_VALUE_BYTES', 65536).raw,
      SPOOL_MAX_MB: intPair(env.SPOOL_MAX_MB, 'SPOOL_MAX_MB', 200).raw,
      LIDMAP_MAX: intPair(env.LIDMAP_MAX, 'LIDMAP_MAX', 50000).raw,
      FORWARD_MAX_ATTEMPTS: intPair(env.FORWARD_MAX_ATTEMPTS, 'FORWARD_MAX_ATTEMPTS', 10).raw,
      INGEST_POOL_SIZE: intPair(env.INGEST_POOL_SIZE, 'INGEST_POOL_SIZE', 8).raw,
    }),
  });
}

/**
 * The process-wide configuration. Loaded once; a missing/invalid critical value
 * throws here and refuses startup (H4/H5: never start half-configured).
 */
export const config = loadConfig();
