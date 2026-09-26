// gateway/src/config.js
import crypto from 'node:crypto';

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

  // --- P0.4: durable outbound send queue (G5/H4) ---------------------------
  // Every cap below is a direct H4 requirement ("every queue ... has a
  // written cap"). Defaults are this implementation's own documented choice
  // (the P0.4 spec text does not fix numeric defaults for these) - see
  // docs/P0_DEVIATIONS.md for the specific entry recording each default.
  const outQueueMax = intFrom(env.OUT_QUEUE_MAX, 'OUT_QUEUE_MAX', 500);
  if (outQueueMax < 1) problems.push('OUT_QUEUE_MAX must be >= 1');

  const outIdemTtlS = intFrom(env.OUTIDEM_TTL_S, 'OUTIDEM_TTL_S', 604800); // 7d, per spec text
  if (outIdemTtlS < 1) problems.push('OUTIDEM_TTL_S must be >= 1');

  const sentIdsTtlS = intFrom(env.SENT_IDS_TTL_S, 'SENT_IDS_TTL_S', 600); // 10 min, per spec text
  if (sentIdsTtlS < 1) problems.push('SENT_IDS_TTL_S must be >= 1');

  const sentMarkerTtlS = intFrom(env.SENT_MARKER_TTL_S, 'SENT_MARKER_TTL_S', 86400); // 24h: must outlive any realistic crash/restart window used by startup recovery
  if (sentMarkerTtlS < 1) problems.push('SENT_MARKER_TTL_S must be >= 1');

  const paceInteractiveMinMs = intFrom(env.PACE_INTERACTIVE_MIN_MS, 'PACE_INTERACTIVE_MIN_MS', 800);
  const paceInteractiveMaxMs = intFrom(env.PACE_INTERACTIVE_MAX_MS, 'PACE_INTERACTIVE_MAX_MS', 1500);
  const paceBulkMinMs = intFrom(env.PACE_BULK_MIN_MS, 'PACE_BULK_MIN_MS', 2000);
  const paceBulkMaxMs = intFrom(env.PACE_BULK_MAX_MS, 'PACE_BULK_MAX_MS', 3000);
  if (paceInteractiveMinMs > paceInteractiveMaxMs) problems.push('PACE_INTERACTIVE_MIN_MS must be <= PACE_INTERACTIVE_MAX_MS');
  if (paceBulkMinMs > paceBulkMaxMs) problems.push('PACE_BULK_MIN_MS must be <= PACE_BULK_MAX_MS');

  // Token buckets (per phone number, per kind) - Lua-atomic in outbound/tokenBucket.js.
  const serviceBucketCapacity = intFrom(env.SERVICE_BUCKET_CAPACITY, 'SERVICE_BUCKET_CAPACITY', 60);
  const serviceBucketRefillPerMin = intFrom(env.SERVICE_BUCKET_REFILL_PER_MIN, 'SERVICE_BUCKET_REFILL_PER_MIN', 60);
  const marketingBucketCapacity = intFrom(env.MARKETING_BUCKET_CAPACITY, 'MARKETING_BUCKET_CAPACITY', 20);
  const marketingBucketRefillPerMin = intFrom(env.MARKETING_BUCKET_REFILL_PER_MIN, 'MARKETING_BUCKET_REFILL_PER_MIN', 20); // spec default: 20 msg/min
  const marketingDailyCap = intFrom(env.MARKETING_DAILY_CAP, 'MARKETING_DAILY_CAP', 1000);
  if (serviceBucketCapacity < 1) problems.push('SERVICE_BUCKET_CAPACITY must be >= 1');
  if (marketingBucketCapacity < 1) problems.push('MARKETING_BUCKET_CAPACITY must be >= 1');
  if (marketingDailyCap < 1) problems.push('MARKETING_DAILY_CAP must be >= 1');

  const outboundMaxSendAttempts = intFrom(env.OUTBOUND_MAX_SEND_ATTEMPTS, 'OUTBOUND_MAX_SEND_ATTEMPTS', 3); // spec: "3 محاولات مع backoff"
  if (outboundMaxSendAttempts < 1) problems.push('OUTBOUND_MAX_SEND_ATTEMPTS must be >= 1');

  const outboundTtlInteractiveMs = intFrom(env.OUTBOUND_TTL_INTERACTIVE_MS, 'OUTBOUND_TTL_INTERACTIVE_MS', 10 * 60 * 1000); // 10 min, per spec text
  const outboundTtlBulkMs = intFrom(env.OUTBOUND_TTL_BULK_MS, 'OUTBOUND_TTL_BULK_MS', 24 * 3600 * 1000); // 24h, per spec text
  if (outboundTtlInteractiveMs < 1000) problems.push('OUTBOUND_TTL_INTERACTIVE_MS must be >= 1000');
  if (outboundTtlBulkMs < 1000) problems.push('OUTBOUND_TTL_BULK_MS must be >= 1000');

  const evtStreamMaxLen = intFrom(env.EVT_STREAM_MAXLEN, 'EVT_STREAM_MAXLEN', 100000);
  if (evtStreamMaxLen < 1) problems.push('EVT_STREAM_MAXLEN must be >= 1');

  // --- P0.5: session lifecycle, lease/fencing (G6/G7/G8) --------------------
  // instanceId identifies THIS process in lease values (`{instance_id}:{fencing_token}`)
  // and in logs/health. Set INSTANCE_ID explicitly when running >1 gateway
  // instance (docker-compose service replicas, a rolling deploy) so logs and
  // GET /sessions/:id/health.lease_holder are readable; a random default is
  // still safe (unique) for a single-instance deployment or a test process.
  const instanceId = read('INSTANCE_ID', `auto-${crypto.randomUUID().slice(0, 8)}`);

  // Lease (spec, literal): SET lease:{sid} NX PX 30000, renewed every 10s.
  const leaseTtlMs = intFrom(env.LEASE_TTL_MS, 'LEASE_TTL_MS', 30000);
  const leaseRenewMs = intFrom(env.LEASE_RENEW_MS, 'LEASE_RENEW_MS', 10000);
  if (leaseTtlMs < 1000) problems.push('LEASE_TTL_MS must be >= 1000');
  if (leaseRenewMs < 100) problems.push('LEASE_RENEW_MS must be >= 100');
  if (leaseRenewMs >= leaseTtlMs) problems.push('LEASE_RENEW_MS must be < LEASE_TTL_MS (renewal must land before expiry)');

  // Staged rehydrate boot (spec, literal): REHYDRATE_CONCURRENCY=2 + jitter 0.5-2s.
  const rehydrateConcurrency = intFrom(env.REHYDRATE_CONCURRENCY, 'REHYDRATE_CONCURRENCY', 2);
  const rehydrateJitterMinMs = intFrom(env.REHYDRATE_JITTER_MIN_MS, 'REHYDRATE_JITTER_MIN_MS', 500);
  const rehydrateJitterMaxMs = intFrom(env.REHYDRATE_JITTER_MAX_MS, 'REHYDRATE_JITTER_MAX_MS', 2000);
  if (rehydrateConcurrency < 1) problems.push('REHYDRATE_CONCURRENCY must be >= 1');
  if (rehydrateJitterMinMs > rehydrateJitterMaxMs) problems.push('REHYDRATE_JITTER_MIN_MS must be <= REHYDRATE_JITTER_MAX_MS');

  // MAX_SESSIONS (spec, literal: "نتيجة قياسك، لا تخمينك" - your OWN measurement,
  // never a guess). Real measurement taken on the VPS (docs/P0_DEVIATIONS.md,
  // this phase's entry): a single QR-pending (unpaired) session added ~3.15MiB
  // to a 512MB-limited gateway container (60.17MiB -> 63.32MiB, real
  // `docker stats`, single sample). That number is an honestly-disclosed FLOOR,
  // not the true per-session cost - a paired/connected session carries more
  // state (chat/contact sync, in-flight message buffers) that could not be
  // measured without a real WhatsApp account. Default below applies a 10x
  // safety margin over the measured floor (~32MB/session) against the
  // container's real headroom (512m limit - ~60MB idle base ~= 452MB), landing
  // on a deliberately conservative 10 - not itself measured, an explicit,
  // documented safety buffer pending a real paired-session measurement.
  const maxSessions = intFrom(env.MAX_SESSIONS, 'MAX_SESSIONS', 10);
  if (maxSessions < 1) problems.push('MAX_SESSIONS must be >= 1');

  // Reconnect backoff (spec, literal): exponential 1s -> 5min with jitter;
  // restartRequired reconnects immediately; CONFLICT is capped to <= 1 attempt
  // per 60s (enforced in sessions.js, not a simple backoff curve).
  const reconnectBaseMs = intFrom(env.RECONNECT_BASE_MS, 'RECONNECT_BASE_MS', 1000);
  const reconnectMaxMs = intFrom(env.RECONNECT_MAX_MS, 'RECONNECT_MAX_MS', 5 * 60 * 1000);
  const reconnectJitterMs = intFrom(env.RECONNECT_JITTER_MS, 'RECONNECT_JITTER_MS', 500);
  const conflictBackoffMs = intFrom(env.CONFLICT_BACKOFF_MS, 'CONFLICT_BACKOFF_MS', 60000);
  if (reconnectBaseMs < 100) problems.push('RECONNECT_BASE_MS must be >= 100');
  if (reconnectMaxMs < reconnectBaseMs) problems.push('RECONNECT_MAX_MS must be >= RECONNECT_BASE_MS');
  if (conflictBackoffMs < 1000) problems.push('CONFLICT_BACKOFF_MS must be >= 1000');

  // Lease sweep: how often a gateway instance re-scans AUTH_DIR for a
  // registered session it does not currently hold, to attempt takeover once
  // the prior holder's lease has expired (failover, G7's own acceptance text).
  const leaseSweepMs = intFrom(env.LEASE_SWEEP_MS, 'LEASE_SWEEP_MS', 5000);
  if (leaseSweepMs < 1000) problems.push('LEASE_SWEEP_MS must be >= 1000');

  // --- P0.6: operational readiness (G10, disasters 19/21 baseline) --------
  // METRICS_TOKEN (spec, literal): "سرّ فارغ = رفض إقلاع" - an empty/missing
  // token must refuse startup (H5), same posture as
  // SHARWA_AI_GATEWAY_API_KEY. Deliberately NOT added to `problems` here:
  // config.js is imported by nearly every module (including every test
  // file, transitively, via sessions.js), so throwing here would force
  // METRICS_TOKEN to be set in every single test file's environment just to
  // import unrelated constants. The refusal that actually matters - the
  // real gateway process declining to start - is enforced once, in
  // index.js's main(), which is the only place this value is a live
  // security control rather than a config constant. Read directly (not
  // intFrom - it's a string).
  const metricsToken = read('METRICS_TOKEN', '');

  // P0.6 closure (Batch C, my own addition - not from the literal spec text,
  // which only describes the GATEWAY's own /metrics): the forwarder is a
  // separate OS process with no HTTP server at all today (D-22's own
  // recorded reason monitoring was deferred). Giving it a small, dedicated
  // /healthz+/metrics server (forwarder.js, forwarder_metrics.js) needs its
  // own port, distinct from the gateway's PORT (4001) since both can run on
  // the same host/network. 4002 keeps the existing "gateway uses 4001"
  // convention obviously adjacent, not overlapping.
  const forwarderMetricsPort = intFrom(env.FORWARDER_METRICS_PORT, 'FORWARDER_METRICS_PORT', 4002);
  if (forwarderMetricsPort < 1 || forwarderMetricsPort > 65535) problems.push('FORWARDER_METRICS_PORT out of range');

  // /readyz (spec, literal): 503 if spool has been non-empty for longer than
  // this. Reused as the same threshold for the Prometheus alert rule
  // "spool غير فارغ > 5 دقائق" (ops/prometheus/alerts.yml) - one number, one
  // meaning, instead of two separately-tuned thresholds for the same signal.
  const spoolStaleS = intFrom(env.SPOOL_STALE_S, 'SPOOL_STALE_S', 300);
  if (spoolStaleS < 1) problems.push('SPOOL_STALE_S must be >= 1');

  // Graceful shutdown (spec, literal): exit within <= 25s, itself under the
  // compose stop_grace_period (30s for gateway/gateway-forwarder). Kept
  // comfortably under both so the process always exits on its own rather
  // than being SIGKILLed by Docker. downloadDrainTimeoutMs bounds only the
  // "let in-flight downloads finish" step within that budget.
  const shutdownTimeoutMs = intFrom(env.SHUTDOWN_TIMEOUT_MS, 'SHUTDOWN_TIMEOUT_MS', 20000);
  const downloadDrainTimeoutMs = intFrom(env.DOWNLOAD_DRAIN_TIMEOUT_MS, 'DOWNLOAD_DRAIN_TIMEOUT_MS', 8000);
  if (shutdownTimeoutMs < 1000) problems.push('SHUTDOWN_TIMEOUT_MS must be >= 1000');
  if (shutdownTimeoutMs > 25000) problems.push('SHUTDOWN_TIMEOUT_MS must be <= 25000 (spec: exit within <= 25s)');
  if (downloadDrainTimeoutMs >= shutdownTimeoutMs) problems.push('DOWNLOAD_DRAIN_TIMEOUT_MS must be < SHUTDOWN_TIMEOUT_MS (it is only one step of the shutdown budget)');

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

  // P0.6 Batch B: redis-cache connection - the fast copy of kill-switch
  // state (Postgres kill_switches, written only by core/api, is P0.7 scope
  // and does not exist yet; this gateway only ever READS the redis-cache
  // mirror - see gateway/src/killswitch.js). Mirrors the redis-durable block
  // above exactly: same convention, same lazy-validation posture (H3 -
  // reachability is validated by redis.js at connection time, not here).
  const redisCacheHost = read('REDIS_CACHE_HOST', '127.0.0.1');
  const redisCachePort = intFrom(env.REDIS_CACHE_PORT, 'REDIS_CACHE_PORT', 6379);
  const redisCachePassword = read('REDIS_CACHE_PASSWORD', '');
  const redisCacheTimeoutMs = intFrom(env.REDIS_CACHE_TIMEOUT_MS, 'REDIS_CACHE_TIMEOUT_MS', 5000);
  const redisCacheMaxRetries = intFrom(env.REDIS_CACHE_MAX_RETRIES, 'REDIS_CACHE_MAX_RETRIES', 3);
  if (redisCachePort < 1 || redisCachePort > 65535) problems.push('REDIS_CACHE_PORT out of range');
  if (redisCacheTimeoutMs < 100) problems.push('REDIS_CACHE_TIMEOUT_MS must be >= 100');
  if (redisCacheMaxRetries < 0) problems.push('REDIS_CACHE_MAX_RETRIES must be >= 0');

  // P0.6 Batch B (spec, literal): once the cached kill-switch state's age
  // exceeds this, marketing/broadcast specifically fail closed (blocked);
  // every other capability keeps using its last-known cached value
  // regardless of age (gateway/src/killswitch.js's checkCapability).
  const ksStaleMaxS = intFrom(env.KS_STALE_MAX_S, 'KS_STALE_MAX_S', 120);
  if (ksStaleMaxS < 1) problems.push('KS_STALE_MAX_S must be >= 1');

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
    redisCache: Object.freeze({
      host: redisCacheHost,
      port: redisCachePort,
      password: redisCachePassword,
      timeoutMs: redisCacheTimeoutMs,
      maxRetries: redisCacheMaxRetries,
    }),
    killswitch: Object.freeze({
      staleMaxS: ksStaleMaxS,
    }),
    coreIngestGroup,
    legacyForwarderGroup,
    metricsToken,
    forwarderMetricsPort,
    readyz: Object.freeze({
      spoolStaleS,
    }),
    shutdown: Object.freeze({
      timeoutMs: shutdownTimeoutMs,
      downloadDrainTimeoutMs,
    }),
    instanceId,
    maxSessions,
    lease: Object.freeze({
      ttlMs: leaseTtlMs,
      renewMs: leaseRenewMs,
      sweepMs: leaseSweepMs,
    }),
    rehydrate: Object.freeze({
      concurrency: rehydrateConcurrency,
      jitterMinMs: rehydrateJitterMinMs,
      jitterMaxMs: rehydrateJitterMaxMs,
    }),
    reconnect: Object.freeze({
      baseMs: reconnectBaseMs,
      maxMs: reconnectMaxMs,
      jitterMs: reconnectJitterMs,
      conflictBackoffMs,
    }),
    outbound: Object.freeze({
      queueMax: outQueueMax,
      idemTtlS: outIdemTtlS,
      sentIdsTtlS,
      sentMarkerTtlS,
      paceInteractiveMinMs,
      paceInteractiveMaxMs,
      paceBulkMinMs,
      paceBulkMaxMs,
      serviceBucketCapacity,
      serviceBucketRefillPerMin,
      marketingBucketCapacity,
      marketingBucketRefillPerMin,
      marketingDailyCap,
      maxSendAttempts: outboundMaxSendAttempts,
      ttlInteractiveMs: outboundTtlInteractiveMs,
      ttlBulkMs: outboundTtlBulkMs,
      evtStreamMaxLen,
    }),
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
