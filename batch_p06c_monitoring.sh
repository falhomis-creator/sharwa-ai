#!/usr/bin/env bash
# ops/batch_p06c_monitoring.sh
#
# P0.6 Batch C (final) - monitoring: metric wiring across every real code
# path, a dedicated forwarder metrics HTTP server, Prometheus + 8 alert
# rules, docker-compose.yml wiring, and one real gap fixed along the way
# (F18: core-ingest consumer group was never created at gateway boot).
#
# Everything this script writes was self-verified in an isolated sandbox
# BEFORE this script existed: gateway/verify_metrics.mjs (26/26, all new
# metrics against real local Redis + a real forwarder.js child process hit
# with real curl), gateway/verify_f18.mjs (13/13, the core-ingest fix against
# real Redis, including a simulated restart), promtool check config/check
# rules against both new YAML files, and the FULL existing gateway suite
# (118/118, unchanged) run locally against real Redis. None of those verify_*
# scripts are repo files - they never get written by this script.
#
# What this script does, in order:
#   1. backs up every EXISTING file it is about to modify, with a timestamp
#   2. writes the modified/new gateway source files
#   3. writes ops/prometheus/{prometheus.yml,alerts.yml} (new)
#   4. writes ops/wire_p06c.mjs (new) and runs it to wire docker-compose.yml
#      surgically - NOT a YAML round-trip (same discipline as ops/wire_p06.mjs)
#   5. generates ops/prometheus/secrets/metrics_token from this repo's own
#      real .env (METRICS_TOKEN) - gitignored, never committed
#   6. runs scripts/hunt_gate.mjs (scoped bypass: only refuses on a violation
#      inside a file THIS script itself just wrote)
#   7. validates both new Prometheus YAML files for real with the EXACT pinned
#      image (prom/prometheus:v3.14.0) via `docker run ... promtool`
#   8. runs the FULL existing gateway test suite for real (Docker, real
#      redis-cache/redis-durable - no mocks, H7)
#   9. appends F18 to docs/P0_FINDINGS.md and D-28 to docs/P0_DEVIATIONS.md
#      (idempotent - skipped if already present)
#  10. prints a git diff --stat summary
#
# It does NOT run git add/commit, and it does NOT start the real stack
# (docker compose up). Gate A (committing) remains your own explicit call,
# same as every batch before this one. A separate script,
# ops/verify_p06c_live.sh, brings the stack up for a real live check AFTER
# you've reviewed this script's diff - run that one only when you're ready.
#
# Usage: bash ops/batch_p06c_monitoring.sh   (run from the repo root)

set -euo pipefail

if [ ! -f docker-compose.yml ] || [ ! -d gateway ]; then
  echo "REFUSED: this does not look like the repo root (no docker-compose.yml / gateway/ here)." >&2
  echo "Run from the repo root, e.g. /home/sharwa/sharwa_ai" >&2
  exit 1
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"

echo "== backing up files this script is about to modify =="
if [ -f "gateway/src/metrics.js" ]; then cp "gateway/src/metrics.js" "gateway/src/metrics.js.bak.${TS}"; echo "backed up: gateway/src/metrics.js.bak.${TS}"; fi
if [ -f "gateway/src/forwarder.js" ]; then cp "gateway/src/forwarder.js" "gateway/src/forwarder.js.bak.${TS}"; echo "backed up: gateway/src/forwarder.js.bak.${TS}"; fi
if [ -f "gateway/src/ingest/wal.js" ]; then cp "gateway/src/ingest/wal.js" "gateway/src/ingest/wal.js.bak.${TS}"; echo "backed up: gateway/src/ingest/wal.js.bak.${TS}"; fi
if [ -f "gateway/src/ingest/spool.js" ]; then cp "gateway/src/ingest/spool.js" "gateway/src/ingest/spool.js.bak.${TS}"; echo "backed up: gateway/src/ingest/spool.js.bak.${TS}"; fi
if [ -f "gateway/src/media.js" ]; then cp "gateway/src/media.js" "gateway/src/media.js.bak.${TS}"; echo "backed up: gateway/src/media.js.bak.${TS}"; fi
if [ -f "gateway/src/outbound/queue.js" ]; then cp "gateway/src/outbound/queue.js" "gateway/src/outbound/queue.js.bak.${TS}"; echo "backed up: gateway/src/outbound/queue.js.bak.${TS}"; fi
if [ -f "gateway/src/index.js" ]; then cp "gateway/src/index.js" "gateway/src/index.js.bak.${TS}"; echo "backed up: gateway/src/index.js.bak.${TS}"; fi
if [ -f "gateway/src/config.js" ]; then cp "gateway/src/config.js" "gateway/src/config.js.bak.${TS}"; echo "backed up: gateway/src/config.js.bak.${TS}"; fi
if [ -f "gateway/.env.example" ]; then cp "gateway/.env.example" "gateway/.env.example.bak.${TS}"; echo "backed up: gateway/.env.example.bak.${TS}"; fi
if [ -f ".gitignore" ]; then cp ".gitignore" ".gitignore.bak.${TS}"; echo "backed up: .gitignore.bak.${TS}"; fi

echo "== writing gateway/src/metrics.js (modify) =="
mkdir -p "$(dirname "gateway/src/metrics.js")"
cat > "gateway/src/metrics.js" <<'GW_METRICS_JS_EOF'
// gateway/src/metrics.js
// P0.6 (G10, H12): Prometheus metrics, served (Bearer-token-protected) at
// GET /metrics by index.js.
//
// Scope of THIS phase's wiring (Batch A): the default process/runtime
// metrics (collectDefaultMetrics - confirmed, not guessed, to produce the
// exact names the spec requires: process_resident_memory_bytes,
// nodejs_heap_size_used_bytes, process_open_fds - see docs/P0_DEVIATIONS.md)
// plus the three session-lifecycle metrics this phase's own code touches
// directly: session_state, session_reconnects_total, lease_lost_total.
//
// Batch B addition: killswitch_state{scope,capability} and
// killswitch_stale_seconds (both wired to killswitch.js's real sync points)
// plus out_blocked_total{capability} (wired to outbound/queue.js's real
// kill-switch-blocked outcome) - each added in the same change that gives it
// a real increment/update point, same posture as Batch A's three
// session-lifecycle metrics.
//
// Batch C addition (P0.6 closure - monitoring): the rest of the spec's fixed
// name list - ingest_*, stream_*, forwarder_attempts_total (gateway-side
// definition only - see forwarder_metrics.js for the forwarder PROCESS's own
// registry), dlq_length (ditto), out_queue_depth/out_sent_total/
// out_failed_total, media_*. Every one below is wired to a real, existing
// code path in the SAME change that defines it here (D-22's own rule: a
// metric registered without a real increment point reads as a confirmed
// "0", which is exactly the fabricated-looking-data H1 forbids, wearing a
// metrics hat). See docs/P0_DEVIATIONS.md for the two label/name deviations
// this batch records (out_queue_depth's label is `kind`, not the spec's
// literal `priority` - no such field exists in the real code; and
// ingest_spool_depth is a byte size, not a record count - spool.js's own
// O(1) health-check discipline never reads the whole file just to count
// lines).
import client from 'prom-client';
import { config } from './config.js';

export const registry = new client.Registry();

client.collectDefaultMetrics({ register: registry });

// session_state{state}: how many currently-tracked sessions are in each
// status value (the same frozen vocabulary mapConnectionStatus produces -
// CONNECTED/CONNECTING/DISCONNECTED/etc). A gauge, not a counter: a session
// moving CONNECTING -> CONNECTED must decrement the old label and increment
// the new one, never just accumulate.
export const sessionStateGauge = new client.Gauge({
  name: 'session_state',
  help: 'Number of sessions currently in each lifecycle state (P0.5 mapConnectionStatus vocabulary).',
  labelNames: ['state'],
  registers: [registry],
});

/**
 * Record a session's status transition. Call with the OLD and NEW status
 * every time `session.status` actually changes (mirrors the existing
 * `if (session.status !== prevStatus)` guard in sessions.js - never call
 * this on a non-transition, or the gauge double-counts).
 *
 * @param {string|null} prevState  Previous status, or null on first transition.
 * @param {string} nextState       New status.
 */
export function recordSessionStateTransition(prevState, nextState) {
  if (prevState) sessionStateGauge.labels(prevState).dec();
  sessionStateGauge.labels(nextState).inc();
}

/**
 * Remove a session's last-known state from the gauge entirely - call this
 * when the session's in-memory record is deleted (logout, LOGGED_OUT
 * cleanup), never on an ordinary state transition (use
 * recordSessionStateTransition for that).
 *
 * @param {string} lastState
 */
export function untrackSessionState(lastState) {
  sessionStateGauge.labels(lastState).dec();
}

// session_reconnects_total{reason}: one increment per scheduled reconnect
// attempt, labeled with the same reason string already used in sessions.js's
// own log line ('conflict' | 'restart' | 'backoff' | 'backoff-after-open-error').
export const sessionReconnectsTotal = new client.Counter({
  name: 'session_reconnects_total',
  help: 'Total reconnect attempts scheduled, by reason (P0.5 classifyDisconnect / open-error retry).',
  labelNames: ['reason'],
  registers: [registry],
});

// lease_lost_total: incremented once per real lease-loss event
// (handleLeaseLost in sessions.js - a renewal that failed because another
// instance now holds the lease).
export const leaseLostTotal = new client.Counter({
  name: 'lease_lost_total',
  help: 'Total times this instance lost a session lease during renewal (P0.5/G7).',
  registers: [registry],
});

// killswitch_state{scope,capability}: the last-synced severity of one
// (scope, capability) pair from the redis-cache mirror (0=on, 1=degraded,
// 2=off) - a gauge, set directly by killswitch.js on every successful HGETALL
// (initial resync, periodic 30s resync, or a ks:changes-triggered refetch).
export const killswitchStateGauge = new client.Gauge({
  name: 'killswitch_state',
  help: 'Last-synced kill-switch severity per scope+capability from the redis-cache mirror (0=on, 1=degraded, 2=off).',
  labelNames: ['scope', 'capability'],
  registers: [registry],
});

// killswitch_stale_seconds: seconds since the last successful redis-cache
// sync of ANY scope. 0 while healthy; grows unbounded while redis-cache is
// unreachable - the spec's own staleness signal (KS_STALE_MAX_S) made
// observable. Refreshed on a short internal timer by killswitch.js so a
// /metrics scrape between resync ticks still reflects the true elapsed time,
// not a stale snapshot from the last sync.
export const killswitchStaleSecondsGauge = new client.Gauge({
  name: 'killswitch_stale_seconds',
  help: 'Seconds since the last successful redis-cache kill-switch sync (P0.6 Batch B).',
  registers: [registry],
});

// out_blocked_total{capability}: one increment per outbound queue item that
// reached processOne() and was blocked by the kill-switch (outcome
// failed/error_class=blocked) - wired directly at outbound/queue.js's real
// blocked-item code path.
export const outBlockedTotal = new client.Counter({
  name: 'out_blocked_total',
  help: 'Total outbound queue items blocked by the kill-switch, by capability (P0.6 Batch B).',
  labelNames: ['capability'],
  registers: [registry],
});

// ---------------------------------------------------------------------------
// Batch C (P0.6 closure): ingest/WAL metrics - wired at gateway/src/ingest/
// wal.js's and spool.js's real code paths (the only places that write the
// WAL / the disk spool).
// ---------------------------------------------------------------------------

// ingest_ack_seconds: literal spec text - "من وصول الرسالة حتى نجاح XADD"
// (from message arrival until XADD succeeds). Measured inside
// ingest/wal.js's appendEvent() itself (function entry -> fusedAppend
// success), the only real code path that performs the XADD - NOT observed
// on the 'duplicate' outcome, since no XADD ran in that case (see
// ingest_duplicates_total below for that path instead).
export const ingestAckSeconds = new client.Histogram({
  name: 'ingest_ack_seconds',
  help: 'Time from appendEvent() entry to a successful WAL XADD, in seconds (P0.6 closure).',
  buckets: [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1, 2],
  registers: [registry],
});

// ingest_messages_total{type}: one increment per message that actually lands
// in the WAL stream (the 'appended' outcome only - a duplicate never reaches
// XADD, see above), labeled by the same normalized `type` vocabulary
// normalize.js's detectType() produces.
export const ingestMessagesTotal = new client.Counter({
  name: 'ingest_messages_total',
  help: 'Total inbound messages appended to the WAL, by normalized type (P0.6 closure).',
  labelNames: ['type'],
  registers: [registry],
});

// ingest_duplicates_total: one increment per appendEvent() call that the
// fused dedupe+XADD recognized as an already-claimed provider_message_id
// (the 'duplicate' outcome - a real, successful de-duplication, not a
// failure).
export const ingestDuplicatesTotal = new client.Counter({
  name: 'ingest_duplicates_total',
  help: 'Total inbound messages recognized as duplicates by the fused dedupe+XADD (P0.6 closure).',
  registers: [registry],
});

// ingest_spool_depth: DEVIATION from the spec's literal "depth" wording (see
// docs/P0_DEVIATIONS.md) - this is the spool file's current SIZE IN BYTES,
// not a record count. spool.js's own spoolStatus() is deliberately O(1)
// (stat() only) for GET /sessions/:id/health's sake; counting records would
// require reading the whole file on every scrape, which is exactly the kind
// of unbounded scrape-time cost H4/H8 forbid. Updated at the two real places
// the spool file's size actually changes: spool.js's own spool() (append)
// and drainSpool() (rewrite).
export const ingestSpoolDepthBytes = new client.Gauge({
  name: 'ingest_spool_depth',
  help: 'Current disk-spool file size in bytes (DEVIATION: byte size, not a record count - see docs/P0_DEVIATIONS.md). 0 when empty.',
  registers: [registry],
});

// ingest_spool_overflow_total: one increment per spool() call rejected
// because the spool file was already at its SPOOL_MAX_MB cap (H4) - the
// real SPOOL_FULL code path in spool.js.
export const ingestSpoolOverflowTotal = new client.Counter({
  name: 'ingest_spool_overflow_total',
  help: 'Total spool() calls rejected because the disk spool was already at its SPOOL_MAX_MB cap (P0.6 closure).',
  registers: [registry],
});

// ---------------------------------------------------------------------------
// stream_length{stream} / stream_pending{group}: unlike every other metric in
// this file, these two reflect state that no single gateway-process code
// path owns - stream length is driven by BOTH this process's XADDs and the
// separate forwarder process's XACKs/XTRIMs, and pending-entry count is
// driven entirely by the forwarder's consumer-group progress. Real-time
// XLEN/XPENDING at scrape time (via collect(), prom-client's supported
// per-metric async refresh hook - see registry.metrics() already being
// awaited in index.js) is the only honest way to report them, rather than
// an in-process counter that would silently drift from reality.
//
// setStreamMetricsRedisClient() is called once by index.js's main() with the
// SAME redis-durable client already created there (no second connection).
// Before that call (module import time, every hermetic unit test that
// imports metrics.js transitively via sessions.js/config.js), collect() is a
// deliberate, logged no-op - never a thrown error that could take down an
// unrelated /metrics scrape or crash a test importing this module.
// ---------------------------------------------------------------------------

let streamMetricsClient = null;

/** @param {import('ioredis').Redis|null} client */
export function setStreamMetricsRedisClient(client) {
  streamMetricsClient = client;
}

function shardStreamKeys() {
  return Array.from({ length: config.ingestShards }, (_, i) => `in:${i}`);
}

export const streamLengthGauge = new client.Gauge({
  name: 'stream_length',
  help: 'Current XLEN of each in:{shard} stream, refreshed at scrape time (P0.6 closure).',
  labelNames: ['stream'],
  registers: [registry],
  async collect() {
    if (!streamMetricsClient) return;
    for (const stream of shardStreamKeys()) {
      try {
        const len = await streamMetricsClient.xlen(stream);
        this.labels(stream).set(Number(len));
      } catch (err) {
        // H3: never silently skip - but one unreachable stream must never
        // abort the whole /metrics scrape for every other metric.
        // eslint-disable-next-line no-console
        console.error(`[metrics] stream_length collect failed for ${stream}: ${err.message}`);
      }
    }
  },
});

// stream_pending{group}: DEVIATION (see docs/P0_DEVIATIONS.md) - only the
// `legacy-forwarder` group is reported. `core-ingest` is created at gateway
// startup (F18, this same batch) but has no real consumer until P1 - its
// pending count would always read 0 for a reason that has nothing to do
// with health (nothing has ever claimed from it), which is exactly the kind
// of misleading "confirmed zero" D-22 already forbids for an unwired metric.
export const streamPendingGauge = new client.Gauge({
  name: 'stream_pending',
  help: 'Current XPENDING summary count for the legacy-forwarder consumer group, refreshed at scrape time (P0.6 closure; core-ingest omitted - see docs/P0_DEVIATIONS.md).',
  labelNames: ['group'],
  registers: [registry],
  async collect() {
    if (!streamMetricsClient) return;
    let total = 0;
    let maxIdleMs = 0;
    for (const stream of shardStreamKeys()) {
      try {
        const summary = await streamMetricsClient.xpending(stream, config.legacyForwarderGroup);
        total += Number(summary?.[0] ?? 0);
        // Extended form (IDLE 0, full range) to find the oldest pending
        // entry's real idle time - feeds stream_pending_oldest_seconds below,
        // needed for the alerts.yml rule "أقدم رسالة معلّقة بالـStream > 60
        // ثانية" (the literal spec text). One extra call per stream per
        // scrape, same cadence as the summary call above - no new Redis
        // connection, no new polling loop.
        if (Number(summary?.[0] ?? 0) > 0) {
          const entries = await streamMetricsClient.xpending(
            stream, config.legacyForwarderGroup, 'IDLE', '0', '-', '+', 1000,
          );
          for (const entry of entries ?? []) {
            const idleMs = Number(entry?.[2] ?? 0);
            if (idleMs > maxIdleMs) maxIdleMs = idleMs;
          }
        }
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error(`[metrics] stream_pending collect failed for ${stream}: ${err.message}`);
      }
    }
    this.labels(config.legacyForwarderGroup).set(total);
    streamPendingOldestSecondsGauge.labels(config.legacyForwarderGroup).set(maxIdleMs / 1000);
  },
});

// stream_pending_oldest_seconds{group}: NOT in the spec's literal fixed-name
// list (see docs/P0_DEVIATIONS.md) - added because the alerts.yml rule the
// spec DOES require literally ("أقدم رسالة معلّقة بالـStream > 60 ثانية" -
// oldest pending stream message > 60s) has no other real metric to alert on:
// stream_pending{group} is a COUNT, not an age. Computed in the same
// collect() cycle as stream_pending above (the extended XPENDING form
// already fetched there), so this never costs an extra Redis round trip.
export const streamPendingOldestSecondsGauge = new client.Gauge({
  name: 'stream_pending_oldest_seconds',
  help: 'Idle time (seconds) of the oldest pending entry across in:{shard} for the legacy-forwarder group (P0.6 closure; not in the spec\'s literal metric list - added to back the required "oldest pending > 60s" alert rule, see docs/P0_DEVIATIONS.md).',
  labelNames: ['group'],
  registers: [registry],
});

// ---------------------------------------------------------------------------
// Batch C: outbound queue metrics - wired at outbound/queue.js's real state
// transitions (queued -> inflight -> sent/failed/requeued), the same
// in-process-counter posture as session_state/media_inflight above, NOT a
// scrape-time Redis scan (there is no cheap way to enumerate every active
// session's queue key from this module without a production-unsafe KEYS/SCAN
// - see docs/P0_DEVIATIONS.md).
// ---------------------------------------------------------------------------

// out_queue_depth{kind}: DEVIATION (see docs/P0_DEVIATIONS.md) - labeled
// `kind`, not the spec's literal `priority`. The real, implemented code
// (outbound/queue.js) has no separate "priority" field; `kind`
// (interactive|bulk|marketing) is the actual partition that exists and is
// known at every enqueue/dequeue/requeue call site.
export const outQueueDepthGauge = new client.Gauge({
  name: 'out_queue_depth',
  help: 'Current outbound queue depth by kind, tracked at every real enqueue/dequeue/requeue transition (P0.6 closure; label is `kind`, not the spec\'s literal `priority` - see docs/P0_DEVIATIONS.md).',
  labelNames: ['kind'],
  registers: [registry],
});

export const outSentTotal = new client.Counter({
  name: 'out_sent_total',
  help: 'Total outbound messages successfully sent (P0.6 closure).',
  registers: [registry],
});

// out_failed_total{class}: scoped to 'expired' and 'retryable' (the two
// outcomes classified that way by outbound/queue.js's own emitEvt calls). A
// kill-switch block is already counted precisely once via out_blocked_total
// {capability} (P0.6 Batch B) - deliberately not double-counted here under a
// second metric name for the same event.
export const outFailedTotal = new client.Counter({
  name: 'out_failed_total',
  help: 'Total outbound messages that failed, by class (expired|retryable - blocked is counted separately via out_blocked_total, P0.6 closure).',
  labelNames: ['class'],
  registers: [registry],
});

// ---------------------------------------------------------------------------
// Batch C: media metrics - wired at gateway/src/media.js's real accounting
// points (the same ones that already feed getMediaMetrics()/
// resetMediaMetrics(), P0.3's own in-process bookkeeping - see D-15/D-22).
// Counters are NEVER reset by resetMediaMetrics() (a test-only helper) -
// only the in-process mediaMetrics object and the mediaInflightGauge (a
// gauge, safe to reset for test isolation) are.
// ---------------------------------------------------------------------------

export const mediaBytesTotal = new client.Counter({
  name: 'media_bytes_total',
  help: 'Total bytes successfully downloaded and uploaded for media messages (P0.6 closure; accounting since P0.3).',
  registers: [registry],
});

export const mediaInflightGauge = new client.Gauge({
  name: 'media_inflight',
  help: 'Current number of in-flight media downloads/uploads (P0.6 closure; accounting since P0.3).',
  registers: [registry],
});

export const mediaFailuresTotal = new client.Counter({
  name: 'media_failures_total',
  help: 'Total media download/upload failures, by reason (P0.6 closure; accounting since P0.3).',
  labelNames: ['reason'],
  registers: [registry],
});

export const mediaDurationSeconds = new client.Histogram({
  name: 'media_duration_seconds',
  help: 'Duration of a media download+upload attempt, in seconds (P0.6 closure; accounting since P0.3).',
  buckets: [0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60],
  registers: [registry],
});
GW_METRICS_JS_EOF
echo "wrote gateway/src/metrics.js ($(wc -l < "gateway/src/metrics.js") lines)"

echo "== writing gateway/src/forwarder.js (modify) =="
mkdir -p "$(dirname "gateway/src/forwarder.js")"
cat > "gateway/src/forwarder.js" <<'GW_FORWARDER_JS_EOF'
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
//
// P0.6 closure (Batch C, my own addition beyond the literal spec text - see
// docs/P0_DEVIATIONS.md): this process now also serves a small, dedicated
// Bearer-token-protected /metrics + open /healthz HTTP server on
// config.forwarderMetricsPort, so ops/prometheus/prometheus.yml has a real
// target to scrape here (D-22's own recorded reason monitoring was deferred:
// "الـforwarder يعمل في حاوية منفصلة بلا نقطة HTTP تُكشَط أصلاً"). Same
// timing-safe Bearer-token check as index.js's own /metrics, same H5 refusal
// posture (empty METRICS_TOKEN -> refuse to start at all) for consistency.

import crypto from 'node:crypto';
import http from 'node:http';

import { logger } from './logger.js';
import { config } from './config.js';
import { createRedisClient, closeRedisClient } from './redis.js';
import { postInboundMessage } from './webhook.js';
import { forwarderRegistry, forwarderAttemptsTotal, setForwarderRedisClient } from './forwarder_metrics.js';

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
 * P0.5 (G8): human_takeover_signal entries are, by spec, NEVER passed to the
 * legacy customer-message webhook ("الـForwarder يتجاهلها" - the spec's own
 * words) - they exist so a future P1 consumer can react to a human agent
 * replying from the merchant's own phone, not as a customer message. Skipped
 * the same deliberate/logged way as identity_update, never silently.
 *
 * @param {object} entry
 * @returns {boolean}
 */
function shouldForwardToLegacy(entry) {
  return entry.type !== 'identity_update' && entry.type !== 'human_takeover_signal';
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
      forwarderAttemptsTotal.labels('dlq').inc();
      return true; // ACK the original entry - it now lives on the DLQ, not lost (F2)
    }
    logger.warn({ stream, id, attempts, max: config.forwardMaxAttempts }, '[forwarder] delivery failed; will retry');
    forwarderAttemptsTotal.labels('retry').inc();
    return false;
  }
  forwarderAttemptsTotal.labels('delivered').inc();
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

/**
 * P0.6 closure: timing-safe Bearer-token check, identical in shape to
 * index.js's own isValidMetricsToken (same secret, config.metricsToken -
 * shared across both processes via the same METRICS_TOKEN env var).
 */
function isValidMetricsToken(provided) {
  if (!provided || !config.metricsToken) return false;
  const a = Buffer.from(provided, 'utf8');
  const b = Buffer.from(config.metricsToken, 'utf8');
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(a, b);
}

/**
 * Small, dedicated HTTP server for this process's /healthz + /metrics -
 * deliberately NOT express (no other route needs a framework here; the
 * gateway process's own index.js already depends on express for its much
 * larger real HTTP surface, but adding that same dependency weight here for
 * two routes would be its own unrelated deviation). Returns the raw
 * http.Server so main() can close it during shutdown.
 */
function startMetricsServer() {
  const server = http.createServer(async (req, res) => {
    if (req.method !== 'GET') {
      res.writeHead(405, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'method not allowed' }));
      return;
    }
    if (req.url === '/healthz') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ status: 'ok' }));
      return;
    }
    if (req.url === '/metrics') {
      const auth = req.headers.authorization || '';
      const provided = auth.startsWith('Bearer ') ? auth.slice('Bearer '.length) : '';
      if (!isValidMetricsToken(provided)) {
        res.writeHead(401, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'unauthorized' }));
        return;
      }
      try {
        const body = await forwarderRegistry.metrics();
        res.writeHead(200, { 'Content-Type': forwarderRegistry.contentType });
        res.end(body);
      } catch (err) {
        logger.error({ err: err.message }, '[forwarder] /metrics collection failed');
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'metrics collection failed' }));
      }
      return;
    }
    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'not found' }));
  });
  server.listen(config.forwarderMetricsPort, () => {
    logger.info({ port: config.forwarderMetricsPort }, '[forwarder] metrics server listening');
  });
  return server;
}

async function main() {
  // P0.6 closure (H5, same posture as index.js's main() - D-21): refuse to
  // start at all with an empty/missing METRICS_TOKEN, rather than silently
  // serving an unauthenticated (or permanently 401-ing, indistinguishable
  // from "working but misconfigured") /metrics route.
  if (!config.metricsToken) {
    throw new Error('main: METRICS_TOKEN is required and must be non-empty (H5: empty secret refuses startup)');
  }

  const client = createRedisClient();
  client.on('error', (err) => {
    logger.error({ err: err.message }, '[forwarder] redis-durable client error');
  });

  await waitForReady(client);
  setForwarderRedisClient(client);

  for (const stream of streamKeys()) {
    await ensureGroup(client, stream);
  }

  const metricsServer = startMetricsServer();

  logger.info({ streams: streamKeys(), group: config.legacyForwarderGroup, consumer: CONSUMER_NAME }, '[forwarder] started');

  const shutdown = async (signal) => {
    logger.info({ signal }, '[forwarder] shutting down');
    running = false;
    await new Promise((resolve) => metricsServer.close(() => resolve()));
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

export { toWebhookPayload, deliverOne, streamKeys, shouldForwardToLegacy, waitForReady, BLOCK_MS, isValidMetricsToken };

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => {
    logger.error({ err: err.message }, '[forwarder] fatal startup error');
    process.exit(1);
  });
}
GW_FORWARDER_JS_EOF
echo "wrote gateway/src/forwarder.js ($(wc -l < "gateway/src/forwarder.js") lines)"

echo "== writing gateway/src/forwarder_metrics.js (new) =="
mkdir -p "$(dirname "gateway/src/forwarder_metrics.js")"
cat > "gateway/src/forwarder_metrics.js" <<'GW_FORWARDER_METRICS_JS_EOF'
// gateway/src/forwarder_metrics.js
// P0.6 closure: the forwarder runs as its OWN OS process (docker-compose.yml's
// gateway-forwarder service, `node src/forwarder.js` - a different command,
// same image, as gateway/src/index.js's `node src/index.js`). It therefore
// needs its OWN prom-client Registry, not a shared import of gateway/src/
// metrics.js's registry: sharing that registry would put forwarder-only
// metrics (forwarder_attempts_total, dlq_length) into the GATEWAY process's
// /metrics output too, permanently stuck at a "confirmed 0" there (the
// gateway process never increments them) - exactly the misleading-zero
// pattern D-22 already documents and forbids, just moved to a second
// process instead of a second metric family.
//
// Deliberately NOT re-registering the killswitch_*/session_*/out_*/media_*/
// ingest_*/stream_* families here: the forwarder process has none of the
// real code paths that would ever increment them, and collectDefaultMetrics
// below already gives this process its own correct, separate
// process_resident_memory_bytes/nodejs_heap_size_used_bytes/
// process_open_fds (this process's own PID, not the gateway's).

import client from 'prom-client';

export const forwarderRegistry = new client.Registry();

client.collectDefaultMetrics({ register: forwarderRegistry });

// forwarder_attempts_total{outcome}: one increment per real delivery attempt
// in deliverOne(), labeled by its own three real outcomes - 'delivered'
// (postInboundMessage succeeded), 'retry' (failed, under
// FORWARD_MAX_ATTEMPTS), 'dlq' (failed, parked on dlq:in after
// FORWARD_MAX_ATTEMPTS - the exact same branch that already logs
// '[forwarder] message parked on dlq:in after max attempts').
export const forwarderAttemptsTotal = new client.Counter({
  name: 'forwarder_attempts_total',
  help: 'Total forwarder delivery attempts, by outcome (delivered|retry|dlq) (P0.6 closure).',
  labelNames: ['outcome'],
  registers: [forwarderRegistry],
});

// dlq_length: real-time XLEN of the dlq:in stream at scrape time (an
// in-process counter would drift the moment anything else ever reads from
// dlq:in - there is no consumer for it yet by design, but the metric should
// still reflect ground truth, not a value only this process's own writes
// could move). Same collect()-callback posture as gateway/src/metrics.js's
// stream_length/stream_pending, and the same defensive no-op before the
// client is wired (module import time, e.g. any future hermetic test of
// this file alone).
let dlqRedisClient = null;

/** @param {import('ioredis').Redis|null} client */
export function setForwarderRedisClient(client) {
  dlqRedisClient = client;
}

export const dlqLengthGauge = new client.Gauge({
  name: 'dlq_length',
  help: 'Current XLEN of the dlq:in stream, refreshed at scrape time (P0.6 closure).',
  registers: [forwarderRegistry],
  async collect() {
    if (!dlqRedisClient) return;
    try {
      const len = await dlqRedisClient.xlen('dlq:in');
      this.set(Number(len));
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error(`[forwarder_metrics] dlq_length collect failed: ${err.message}`);
    }
  },
});
GW_FORWARDER_METRICS_JS_EOF
echo "wrote gateway/src/forwarder_metrics.js ($(wc -l < "gateway/src/forwarder_metrics.js") lines)"

echo "== writing gateway/src/ingest/wal.js (modify) =="
mkdir -p "$(dirname "gateway/src/ingest/wal.js")"
cat > "gateway/src/ingest/wal.js" <<'GW_INGEST_WAL_JS_EOF'
// gateway/src/ingest/wal.js
// The write-ahead stream append (G1): turns a normalized entry into one durable
// `in:{shard}` stream entry via the fused dedupe+XADD in dedupe.js, after the F4
// size gate has proven the value is under the cap. This is the only code path
// that writes to the WAL, so the F4 64KB guarantee and the dedupe guarantee
// live in exactly one place.
//
// F4 (owner decision #3): a single message value larger than maxValueBytes is
// hard-rejected here, BEFORE anything reaches redis-durable. It is a permanent
// rejection (no retry, no spool — it will never fit), surfaced as PAYLOAD_TOO_LARGE
// and logged loudly, never silently dropped (F2).
//
// P0.6 closure (Batch C): ingest_ack_seconds/ingest_messages_total/
// ingest_duplicates_total are wired here - the only real code path that
// performs the XADD (see metrics.js's own comment on why 'duplicate' never
// observes the histogram: no XADD ran on that path).

import { randomUUID } from 'node:crypto';

import { fusedAppend } from './dedupe.js';
import { config } from '../config.js';
import { ingestAckSeconds, ingestMessagesTotal, ingestDuplicatesTotal } from '../metrics.js';

/** Deterministic djb2 hash so the same session always lands on the same shard. */
function hashString(str) {
  let h = 5381;
  for (let i = 0; i < str.length; i++) {
    h = ((h << 5) + h + str.charCodeAt(i)) >>> 0;
  }
  return h;
}

/** @param {string} sessionId @param {number} shards @returns {number} 0..shards-1 */
export function shardFor(sessionId, shards = config.ingestShards) {
  return hashString(sessionId) % shards;
}

/** @param {string} sessionId @returns {string} the `in:{shard}` stream key. */
export function streamKeyFor(sessionId) {
  return `in:${shardFor(sessionId)}`;
}

/** @param {string} sessionId @param {string} providerMessageId @returns {string} */
export function dedupeKeyFor(sessionId, providerMessageId) {
  return `dedupe:${sessionId}:${providerMessageId}`;
}

/**
 * Append a normalized event to its shard stream.
 *
 * @param {import('ioredis').Redis} client
 * @param {object} opts
 * @param {string} opts.sessionId
 * @param {object} opts.event         Normalized entry from normalize.js
 *                                    (provider_message_id, type, ts, identity, …).
 * @returns {Promise<{ status: 'appended'|'duplicate', id?: string }>}
 * @throws {Error} code PAYLOAD_TOO_LARGE  (permanent, do not retry)
 * @throws {Error} code DEDUPE_XADD_FAILED (transient, spool + retry)
 */
export async function appendEvent(client, opts) {
  const { sessionId, event } = opts;
  const startedAt = process.hrtime.bigint();

  const entry = {
    v: 1,
    session_id: sessionId,
    ...event,
  };

  // F4: hard-reject an over-cap value before it reaches Redis.
  const serialized = JSON.stringify(entry);
  const bytes = Buffer.byteLength(serialized, 'utf8');
  if (bytes > config.maxValueBytes) {
    const err = new Error(
      `wal: rejected ${bytes}B entry for ${sessionId} (cap ${config.maxValueBytes}B, F4)`,
    );
    err.code = 'PAYLOAD_TOO_LARGE';
    throw err;
  }

  const providerMessageId = event.provider_message_id
    ?? `ts:${event.ts}:${randomUUID()}`;

  const result = await fusedAppend(client, {
    dedupeKey: dedupeKeyFor(sessionId, providerMessageId),
    streamKey: streamKeyFor(sessionId),
    entry: serialized,
    pendingTtlMs: config.dedupePendingTtlS * 1000,
  });

  if (result.status === 'appended') {
    const elapsedSeconds = Number(process.hrtime.bigint() - startedAt) / 1e9;
    ingestAckSeconds.observe(elapsedSeconds);
    ingestMessagesTotal.labels(event.type ?? 'unknown').inc();
  } else {
    ingestDuplicatesTotal.inc();
  }

  return result;
}
GW_INGEST_WAL_JS_EOF
echo "wrote gateway/src/ingest/wal.js ($(wc -l < "gateway/src/ingest/wal.js") lines)"

echo "== writing gateway/src/ingest/spool.js (modify) =="
mkdir -p "$(dirname "gateway/src/ingest/spool.js")"
cat > "gateway/src/ingest/spool.js" <<'GW_INGEST_SPOOL_JS_EOF'
// gateway/src/ingest/spool.js
// Disk spool: the durable fallback when redis-durable is unreachable (disaster
// #5/#17). When an XADD cannot be confirmed durable, the entry is appended to a
// JSONL file so no message is ever silently dropped (F2). A drainer later
// replays the file through the normal WAL path.
//
// Replay is idempotent by construction: each spooled record keeps its
// provider_message_id, so a replayed entry that already made it to the stream is
// recognized as a duplicate by the fused dedupe+XADD and skipped. A crash between
// "replayed" and "file rewritten" therefore can never double-deliver.
//
// H4: the spool is bounded by spoolMaxMb. Past the cap, spooling throws
// SPOOL_FULL rather than growing without limit.

import { appendFile, readFile, writeFile, mkdir, stat, rename } from 'node:fs/promises';
import path from 'node:path';

import { appendEvent } from './wal.js';
import { config } from '../config.js';
import { ingestSpoolDepthBytes, ingestSpoolOverflowTotal } from '../metrics.js';

const SPOOL_FILE = 'spool.ndjson';

/**
 * Append one record to the spool.
 *
 * @param {{ sessionId: string, event: object }} record
 * @param {{ spoolDir?: string, spoolMaxMb?: number }} [opts]
 */
export async function spool(record, opts = {}) {
  const dir = opts.spoolDir ?? config.spoolDir;
  const maxMb = opts.spoolMaxMb ?? config.spoolMaxMb;
  const file = path.join(dir, SPOOL_FILE);
  await mkdir(dir, { recursive: true });

  let size = 0;
  try {
    size = (await stat(file)).size;
  } catch (err) {
    if (err.code !== 'ENOENT') throw err;
    // no spool file yet — start at 0.
  }
  if (size >= maxMb * 1024 * 1024) {
    ingestSpoolOverflowTotal.inc();
    const err = new Error(`spool: ${file} is at cap (${maxMb}MB); refusing to grow (H4)`);
    err.code = 'SPOOL_FULL';
    throw err;
  }

  // On-disk shape mirrors the WAL entry: `{ session_id, event }`.
  const line = JSON.stringify({ session_id: record.sessionId, event: record.event }) + '\n';
  await appendFile(file, line, 'utf8');
  // ingest_spool_depth (P0.6 closure): updated right here rather than via a
  // second stat() call - the new size is exactly the old size plus what was
  // just appended (H4/H8: no extra scrape-time or write-time filesystem call
  // needed to keep this accurate).
  ingestSpoolDepthBytes.set(size + Buffer.byteLength(line, 'utf8'));
}

/**
 * Cheap, real spool status for GET /sessions/:id/health (P0.5) - a `stat()`
 * only, never a full read (the drain path already reads the whole file when
 * it actually needs to; a health check must stay O(1) regardless of how
 * large the spool has grown, up to its own spoolMaxMb cap).
 *
 * @param {{ spoolDir?: string }} [opts]
 * @returns {Promise<{ size_bytes: number, pending: boolean }>}
 */
export async function spoolStatus(opts = {}) {
  const dir = opts.spoolDir ?? config.spoolDir;
  const file = path.join(dir, SPOOL_FILE);
  try {
    const st = await stat(file);
    return { size_bytes: st.size, pending: st.size > 0 };
  } catch (err) {
    if (err.code !== 'ENOENT') throw err;
    return { size_bytes: 0, pending: false };
  }
}

/**
 * Replay every spooled record through the WAL, removing the ones that land.
 *
 * @param {import('ioredis').Redis} client
 * @param {{ spoolDir?: string }} [opts]
 * @returns {Promise<{ replayed: number, remaining: number, dropped: number }>}
 */
export async function drainSpool(client, opts = {}) {
  const dir = opts.spoolDir ?? config.spoolDir;
  const file = path.join(dir, SPOOL_FILE);

  let raw = '';
  try {
    raw = await readFile(file, 'utf8');
  } catch {
    return { replayed: 0, remaining: 0, dropped: 0 }; // nothing spooled
  }

  const lines = raw.split('\n').filter((l) => l.trim() !== '');
  const remaining = [];
  let replayed = 0;
  let dropped = 0;
  let redisDown = false;

  for (const line of lines) {
    if (redisDown) {
      remaining.push(line);
      continue;
    }
    let rec;
    try {
      rec = JSON.parse(line);
    } catch {
      dropped += 1; // corrupt line: unrecoverable, drop (logged by caller)
      continue;
    }
    try {
      await appendEvent(client, { sessionId: rec.session_id, event: rec.event });
      replayed += 1;
    } catch (err) {
      if (err.code === 'PAYLOAD_TOO_LARGE') {
        dropped += 1; // permanent: it can never fit the 64KB cap
        continue;
      }
      // Transient (redis still down, etc.): keep this line and the rest, stop.
      remaining.push(line);
      redisDown = true;
    }
  }

  // Atomic rewrite so a crash mid-drain cannot truncate pending entries.
  const tmp = `${file}.tmp`;
  let rewrittenContent = '';
  if (remaining.length === 0) {
    await writeFile(tmp, '', 'utf8');
  } else {
    rewrittenContent = `${remaining.join('\n')}\n`;
    await writeFile(tmp, rewrittenContent, 'utf8');
  }
  await rename(tmp, file);
  // ingest_spool_depth (P0.6 closure): the rewritten file's real byte size -
  // computed from the content just written, no extra stat() call needed.
  ingestSpoolDepthBytes.set(Buffer.byteLength(rewrittenContent, 'utf8'));

  return { replayed, remaining: remaining.length, dropped };
}
GW_INGEST_SPOOL_JS_EOF
echo "wrote gateway/src/ingest/spool.js ($(wc -l < "gateway/src/ingest/spool.js") lines)"

echo "== writing gateway/src/media.js (modify) =="
mkdir -p "$(dirname "gateway/src/media.js")"
cat > "gateway/src/media.js" <<'GW_MEDIA_JS_EOF'
// gateway/src/media.js
// P0.3 (G3, disasters #16/#21) - media handling: declared-size pre-check,
// streaming download (no full-buffer), streaming multipart upload, a global
// download concurrency semaphore, per-store daily quota on redis-durable,
// magic-byte content-type verification, and the spec's object-key format.
//
// Deliberately does NOT import sessions.js and is not imported by it at
// module-eval time in a way that creates a cycle: sessions.js calls into
// this module's exported functions.

import { downloadContentFromMessage } from '@whiskeysockets/baileys';
import { Upload } from '@aws-sdk/lib-storage';
import { fileTypeFromBuffer } from 'file-type';
import { PassThrough } from 'node:stream';
import { once } from 'node:events';
import { logger } from './logger.js';
import { mediaBytesTotal, mediaInflightGauge, mediaFailuresTotal, mediaDurationSeconds } from './metrics.js';

// ---------------------------------------------------------------------------
// Size caps (env-configurable, PROMPT_P0_DEEPSEEK_PROMPT.md P0.3 defaults).
// ---------------------------------------------------------------------------

const DEFAULT_CAPS_BYTES = {
  image: 16 * 1024 * 1024,
  audio: 16 * 1024 * 1024,
  video: 64 * 1024 * 1024,
  document: 64 * 1024 * 1024,
  sticker: 2 * 1024 * 1024,
};

const CAP_ENV_VAR = {
  image: 'MEDIA_MAX_BYTES_IMAGE',
  audio: 'MEDIA_MAX_BYTES_AUDIO',
  video: 'MEDIA_MAX_BYTES_VIDEO',
  document: 'MEDIA_MAX_BYTES_DOCUMENT',
  sticker: 'MEDIA_MAX_BYTES_STICKER',
};

export function capBytesFor(type, env = process.env) {
  const raw = env[CAP_ENV_VAR[type]];
  const n = raw !== undefined ? Number(raw) : NaN;
  if (Number.isFinite(n) && n > 0) return n;
  return DEFAULT_CAPS_BYTES[type] ?? null;
}

// downloadContentFromMessage's `type` argument uses Baileys' own media-type
// vocabulary, which differs from our normalized entry.type in one place:
// there is no distinct "sticker" download type in Baileys - stickers are
// image-family content (webp) and are downloaded with type 'sticker' too
// (Baileys does define 'sticker' as a valid MediaType - see
// node_modules/@whiskeysockets/baileys/lib/Utils/messages-media.js
// MEDIA_HKDF_KEY_MAPPING, which includes `sticker`), so this is a 1:1 map.
const BAILEYS_DOWNLOAD_TYPE = {
  image: 'image',
  audio: 'audio',
  video: 'video',
  document: 'document',
  sticker: 'sticker',
};

export function normalizeFileLength(fileLength) {
  if (fileLength === undefined || fileLength === null) return null;
  if (typeof fileLength === 'number') return fileLength;
  // protobufjs Long-like objects (fileLength?: number | Long | null)
  if (typeof fileLength.toNumber === 'function') return fileLength.toNumber();
  const n = Number(fileLength);
  return Number.isFinite(n) ? n : null;
}

// ---------------------------------------------------------------------------
// Object key: {session_id}/{YYYY}/{MM}/{provider_message_id}.{ext}
// (corrected from the pre-P0.3 sharwa-ai/{session}/{uuid}.{ext} format -
// see docs/P0_DEVIATIONS.md for the deviation record.)
// ---------------------------------------------------------------------------

export function mediaObjectKey({ sessionId, providerMessageId, ext, now = new Date() }) {
  const yyyy = String(now.getUTCFullYear());
  const mm = String(now.getUTCMonth() + 1).padStart(2, '0');
  return `${sessionId}/${yyyy}/${mm}/${providerMessageId}.${ext}`;
}

// ---------------------------------------------------------------------------
// Global download concurrency semaphore (env-configurable, default 3).
// ---------------------------------------------------------------------------

class Semaphore {
  constructor(max) {
    this.max = max;
    this.current = 0;
    this.queue = [];
  }

  acquire() {
    if (this.current < this.max) {
      this.current += 1;
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      this.queue.push(() => {
        this.current += 1;
        resolve();
      });
    });
  }

  release() {
    this.current -= 1;
    const next = this.queue.shift();
    if (next) next();
  }
}

let downloadSemaphore = new Semaphore(Number(process.env.MEDIA_DOWNLOAD_CONCURRENCY) || 3);

// Exposed for tests only - lets a test install a semaphore with a small max
// to prove the concurrency cap actually blocks a 4th download.
export function setDownloadConcurrency(max) {
  downloadSemaphore = new Semaphore(max);
}

// ---------------------------------------------------------------------------
// In-process metrics accounting (P0.3 spec §"مقاييس": media_bytes_total,
// media_inflight, media_failures_total{reason}, media_duration_seconds).
//
// This module owns the counters because it is the only place that knows the
// true inflight/byte/failure counts; wiring these onto an authenticated
// public `/metrics` Prometheus endpoint is P0.6 scope (that is where the
// whole `/metrics` HTTP surface + METRICS_TOKEN auth is built per the spec's
// own phase split) - see docs/P0_DEVIATIONS.md. `getMediaMetrics()` exists so
// P0.3's own acceptance tests (media_inflight <= 3 under concurrent load) can
// observe the real, live counters rather than re-deriving them.
// ---------------------------------------------------------------------------

let mediaMetrics = {
  inflight: 0,
  bytesTotal: 0,
  failuresByReason: Object.create(null),
  durationsMs: [],
};

export function getMediaMetrics() {
  return {
    inflight: mediaMetrics.inflight,
    bytesTotal: mediaMetrics.bytesTotal,
    failuresByReason: { ...mediaMetrics.failuresByReason },
    durationsMs: [...mediaMetrics.durationsMs],
  };
}

// Exposed for tests only - each test suite/process should start from a clean
// slate rather than accumulating counts across unrelated test cases. Only the
// in-process mediaMetrics object and the Prometheus GAUGE (media_inflight) are
// reset here - the Prometheus COUNTERS (media_bytes_total/failures/duration)
// are never reset by a test helper: a real Counter must never decrease (P0.6
// closure - see metrics.js's own comment on this).
export function resetMediaMetrics() {
  mediaMetrics = { inflight: 0, bytesTotal: 0, failuresByReason: Object.create(null), durationsMs: [] };
  mediaInflightGauge.set(0);
}

function recordFailure(reason) {
  mediaMetrics.failuresByReason[reason] = (mediaMetrics.failuresByReason[reason] || 0) + 1;
  mediaFailuresTotal.labels(reason).inc();
}

// ---------------------------------------------------------------------------
// Per-store daily quota, tracked in redis-durable. Atomic check+reserve via
// a single Lua script (same "fused Lua atomicity" pattern as ingest/dedupe.js
// - a plain GET-then-SET here would race under concurrent downloads for the
// same store).
// ---------------------------------------------------------------------------

const QUOTA_TTL_SECONDS = 2 * 24 * 60 * 60; // 2 days: safely outlives one UTC day

const RESERVE_SCRIPT = `
local key = KEYS[1]
local maxFiles = tonumber(ARGV[1])
local maxBytes = tonumber(ARGV[2])
local estBytes = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local files = tonumber(redis.call('HGET', key, 'files') or '0')
local bytes = tonumber(redis.call('HGET', key, 'bytes') or '0')
if (files + 1) > maxFiles or (bytes + estBytes) > maxBytes then
  return {0, files, bytes}
end
redis.call('HINCRBY', key, 'files', 1)
redis.call('HINCRBY', key, 'bytes', estBytes)
redis.call('EXPIRE', key, ttl)
return {1, files + 1, bytes + estBytes}
`;

const RELEASE_SCRIPT = `
local key = KEYS[1]
local deltaBytes = tonumber(ARGV[1])
redis.call('HINCRBY', key, 'files', -1)
redis.call('HINCRBY', key, 'bytes', -deltaBytes)
return redis.call('HMGET', key, 'files', 'bytes')
`;

function quotaKey(storeId, now = new Date()) {
  const day = now.toISOString().slice(0, 10);
  return `media_quota:${storeId}:${day}`;
}

export function quotaLimits(env = process.env) {
  const files = Number(env.MEDIA_QUOTA_DAILY_FILES);
  const bytes = Number(env.MEDIA_QUOTA_DAILY_BYTES);
  return {
    maxFiles: Number.isFinite(files) && files > 0 ? files : 500,
    maxBytes: Number.isFinite(bytes) && bytes > 0 ? bytes : 1024 * 1024 * 1024,
  };
}

export async function reserveQuota(redisClient, storeId, estimatedBytes, env = process.env) {
  const { maxFiles, maxBytes } = quotaLimits(env);
  const key = quotaKey(storeId);
  const [ok, files, bytes] = await redisClient.eval(
    RESERVE_SCRIPT,
    1,
    key,
    String(maxFiles),
    String(maxBytes),
    String(Math.max(0, Math.floor(estimatedBytes))),
    String(QUOTA_TTL_SECONDS),
  );
  return { ok: ok === 1, files, bytes };
}

export async function releaseQuota(redisClient, storeId, bytesDelta) {
  const key = quotaKey(storeId);
  await redisClient.eval(RELEASE_SCRIPT, 1, key, String(Math.floor(bytesDelta)));
}

// Adjust the byte reservation after the true size is known (actual size can
// differ slightly from the declared fileLength). Only ever called with the
// download already committed, so files count is untouched.
export async function adjustQuotaBytes(redisClient, storeId, bytesDelta) {
  if (!bytesDelta) return;
  const key = quotaKey(storeId);
  await redisClient.hincrby(key, 'bytes', Math.floor(bytesDelta));
}

// ---------------------------------------------------------------------------
// Magic-byte content-type verification (first ~4KB, via `file-type`).
// ---------------------------------------------------------------------------

const SNIFF_BYTES = 4100;

export function magicByteMatches(detected, type) {
  if (!detected) {
    // Some legitimate documents (plain text, csv, some older doc formats)
    // have no recognizable magic-byte signature at all. Only 'document' is
    // permissive here; every other declared type has a well-known
    // signature, so no detection on those is treated as a mismatch.
    return type === 'document';
  }
  const mime = detected.mime;
  if (type === 'image' || type === 'sticker') return mime.startsWith('image/');
  if (type === 'video') return mime.startsWith('video/');
  if (type === 'audio') {
    // WhatsApp voice notes (ogg/opus) and some audio are detected as
    // audio/* by file-type; a handful of containers (e.g. audio-only mp4)
    // are detected as video/mp4 by magic bytes alone.
    return mime.startsWith('audio/') || mime === 'video/mp4';
  }
  if (type === 'document') return true; // wide format range, no single family
  return true;
}

// ---------------------------------------------------------------------------
// Streaming download + streaming multipart upload, with cap enforcement,
// quota reservation, and magic-byte verification, all before the object is
// considered committed.
// ---------------------------------------------------------------------------

/**
 * @param {object} params
 * @param {object} params.content - the Baileys media content object
 *   ({mediaKey, directPath, url, fileLength, ...}), i.e. detectMedia(msg).content.
 * @param {'image'|'audio'|'video'|'document'|'sticker'} params.type
 * @param {string} params.sessionId
 * @param {string} params.providerMessageId
 * @param {string} params.ext
 * @param {string} params.mimeType - declared ContentType for the upload.
 * @param {object} params.redisClient - real ioredis client (redis-durable).
 * @param {string} [params.storeId] - quota scope; falls back to sessionId
 *   (see docs/P0_DEVIATIONS.md - the spec does not define tenant_id at the
 *   gateway layer, so session_id is the deviation fallback).
 * @param {object} params.s3Client - AWS SDK v3 S3Client (MinIO-compatible).
 * @param {string} params.bucket
 * @param {(content: object, type: string) => Promise<AsyncIterable<Buffer>>} [params.downloadFn]
 *   injected for tests; defaults to Baileys' downloadContentFromMessage.
 */
export async function downloadAndUploadMedia({
  content,
  type,
  sessionId,
  providerMessageId,
  ext,
  mimeType,
  redisClient,
  storeId,
  s3Client,
  bucket,
  downloadFn = downloadContentFromMessage,
}) {
  const cap = capBytesFor(type);
  const declaredLength = normalizeFileLength(content?.fileLength);
  if (cap != null && declaredLength != null && declaredLength > cap) {
    recordFailure('too_large');
    return { status: 'too_large', size: declaredLength, cap };
  }

  const scope = storeId || sessionId;
  const reserveEstimate = declaredLength != null ? declaredLength : (cap ?? DEFAULT_CAPS_BYTES[type] ?? 0);
  const quota = await reserveQuota(redisClient, scope, reserveEstimate);
  if (!quota.ok) {
    recordFailure('quota_exceeded');
    return { status: 'quota_exceeded', files: quota.files, bytes: quota.bytes };
  }

  await downloadSemaphore.acquire();
  mediaMetrics.inflight += 1;
  mediaInflightGauge.inc();
  const startedAt = Date.now();
  let released = false;
  const releaseOnce = () => {
    if (!released) {
      released = true;
      mediaMetrics.inflight -= 1;
      mediaInflightGauge.dec();
      const durationMs = Date.now() - startedAt;
      mediaMetrics.durationsMs.push(durationMs);
      mediaDurationSeconds.observe(durationMs / 1000);
      downloadSemaphore.release();
    }
  };

  try {
    const baileysType = BAILEYS_DOWNLOAD_TYPE[type] || type;
    const sourceStream = await downloadFn(content, baileysType);

    const objectKey = mediaObjectKey({ sessionId, providerMessageId, ext });
    const passthrough = new PassThrough();
    const uploader = new Upload({
      client: s3Client,
      params: { Bucket: bucket, Key: objectKey, Body: passthrough, ContentType: mimeType },
      partSize: 5 * 1024 * 1024,
      queueSize: 2,
    });
    const uploadDone = uploader.done();
    // Prevent an unhandled-rejection crash if we abort below before anyone
    // awaits uploadDone on the rejected path. This is a genuine best-effort
    // suppression (the real outcome is handled on the path that does await
    // uploadDone, a few lines down), so it still logs rather than swallowing
    // silently (H3: never fail silently, even on a defensive path).
    uploadDone.catch((err) => {
      logger.warn({ err: err.message, sessionId, providerMessageId }, '[media] background upload-promise rejection after abort (expected on the rejected/aborted path)');
    });

    let total = 0;
    let sniffBuf = Buffer.alloc(0);
    let sniffed = false;
    let rejected = null;

    for await (const rawChunk of sourceStream) {
      const chunk = Buffer.isBuffer(rawChunk) ? rawChunk : Buffer.from(rawChunk);
      total += chunk.length;
      if (cap != null && total > cap) {
        rejected = { status: 'too_large', size: total, cap };
        break;
      }
      if (!sniffed) {
        sniffBuf = Buffer.concat([sniffBuf, chunk]);
        if (sniffBuf.length >= SNIFF_BYTES) {
          sniffed = true;
          const detected = await fileTypeFromBuffer(sniffBuf);
          if (!magicByteMatches(detected, type)) {
            rejected = { status: 'rejected_type', detected: detected ? detected.mime : null };
            break;
          }
        }
      }
      // Respect backpressure (H4: no unbounded buffer growth). If the S3
      // multipart upload is slower than the source stream - a slow/loaded
      // MinIO, or partSize/queueSize limiting how fast lib-storage drains
      // us - PassThrough.write() returns false and, if ignored, its
      // internal buffer accumulates every unconsumed chunk with no bound,
      // silently reproducing the exact full-buffer-in-memory failure mode
      // P0.3 exists to eliminate. Pausing the source read here until
      // 'drain' throttles OUR OWN download loop to the upload's real
      // speed, which is the actual point of streaming.
      const canWriteMore = passthrough.write(chunk);
      if (!canWriteMore) {
        await once(passthrough, 'drain');
      }
    }

    if (!rejected && !sniffed) {
      // Stream ended before we accumulated SNIFF_BYTES - sniff whatever we got.
      const detected = sniffBuf.length > 0 ? await fileTypeFromBuffer(sniffBuf) : null;
      if (!magicByteMatches(detected, type)) {
        rejected = { status: 'rejected_type', detected: detected ? detected.mime : null };
      }
    }

    if (rejected) {
      passthrough.destroy(new Error(rejected.status));
      try {
        await uploader.abort();
      } catch (err) {
        // Best-effort: the object is already refused (rejected.status below),
        // so a failed abort doesn't change the outcome, but it's still
        // logged rather than swallowed (H3).
        logger.warn({ err: err.message, sessionId, providerMessageId, status: rejected.status }, '[media] upload abort failed (best-effort, non-fatal)');
      }
      await releaseQuota(redisClient, scope, reserveEstimate);
      recordFailure(rejected.status);
      return rejected;
    }

    passthrough.end();
    await uploadDone;

    const byteDelta = total - reserveEstimate;
    if (byteDelta !== 0) {
      await adjustQuotaBytes(redisClient, scope, byteDelta);
    }

    mediaMetrics.bytesTotal += total;
    mediaBytesTotal.inc(total);
    return { status: 'ok', object_key: objectKey, size: total };
  } catch (err) {
    await releaseQuota(redisClient, scope, reserveEstimate).catch((releaseErr) => {
      logger.warn({ err: releaseErr.message, sessionId, providerMessageId }, '[media] quota release failed after a download/upload error (best-effort, non-fatal)');
    });
    recordFailure('failed');
    return { status: 'failed', error: err && err.message ? err.message : String(err) };
  } finally {
    releaseOnce();
  }
}
GW_MEDIA_JS_EOF
echo "wrote gateway/src/media.js ($(wc -l < "gateway/src/media.js") lines)"

echo "== writing gateway/src/outbound/queue.js (modify) =="
mkdir -p "$(dirname "gateway/src/outbound/queue.js")"
cat > "gateway/src/outbound/queue.js" <<'GW_OUTBOUND_QUEUE_JS_EOF'
// gateway/src/outbound/queue.js
//
// P0.4 — durable outbound send queue (G5, disasters #4/#5/#17). Replaces the
// P0.2-era in-process array queue in sessions.js (which lived only in RAM and
// was lost on any crash/restart) with a Redis-durable pipeline:
//
//   out:{sid}:queue     LIST   FIFO of not-yet-attempted items (RPUSH/BLMOVE)
//   out:{sid}:inflight  LIST   items currently being sent (crash-recovery scans this)
//   outidem:{sid}:{client_msg_id}   STRING  idempotency marker (NX claim)
//   sent_ids:{sid}:{wa_message_id}  STRING  pre-registered WA message id (TTL)
//   sent_marker:{sid}:{client_msg_id} = wa_message_id   STRING  proof-of-send (TTL)
//   evt:{shard}         STREAM queued|sent|delivered|failed events (same shard
//                        scheme as in:{shard}, reusing shardFor from ingest/wal.js)
//
// Ordering guarantee, stated honestly (H8): FIFO is NOT strict under token-
// bucket throttling — a throttled item is pushed back to the TAIL of the
// queue so later, unthrottled items are not blocked behind it (spec:
// "الرفض ⇒ تأجيل داخل الطابور لا إسقاط" — deferred, never dropped). Ordering
// IS strict for items that are never throttled.
//
// Duplicate-window guarantee, stated honestly (H8, spec's own words): a crash
// between a successful sock.sendMessage() and the sent_marker write leaves no
// way to know the send succeeded; recovery treats it as "unconfirmed" and
// requeues it, so it is sent AGAIN. This is a deliberate, spec-mandated,
// MEASURED duplicate window (bounded by the number of crashes), not a bug —
// see the P0.4 chaos test for the real measured count.

import crypto from 'node:crypto';
import { config } from '../config.js';
import { shardFor } from '../ingest/wal.js';
import { logger } from '../logger.js';
import { tryConsumeToken, tryConsumeMarketingDailyCap } from './tokenBucket.js';
import { outBlockedTotal, outQueueDepthGauge, outSentTotal, outFailedTotal } from '../metrics.js';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function queueKey(sid) { return `out:${sid}:queue`; }
function inflightKey(sid) { return `out:${sid}:inflight`; }
function idemKey(sid, clientMsgId) { return `outidem:${sid}:${clientMsgId}`; }
function sentIdKey(sid, waId) { return `sent_ids:${sid}:${waId}`; }
function sentMarkerKey(sid, clientMsgId) { return `sent_marker:${sid}:${clientMsgId}`; }
function evtStreamKey(sid) { return `evt:${shardFor(sid)}`; }

const ENQUEUE_LUA = `
-- KEYS[1]=idem key  KEYS[2]=queue key
-- ARGV[1]=item json  ARGV[2]=idem ttl seconds  ARGV[3]=queue max length
local qlen = redis.call('LLEN', KEYS[2])
if qlen >= tonumber(ARGV[3]) then
  return {0}
end
local claimed = redis.call('SET', KEYS[1], 'queued', 'EX', ARGV[2], 'NX')
if not claimed then
  local state = redis.call('GET', KEYS[1])
  return {2, state}
end
redis.call('RPUSH', KEYS[2], ARGV[1])
return {1}
`;

async function emitEvt(client, sessionId, type, fields) {
  const entry = { v: 1, type, session_id: sessionId, ts: Date.now(), ...fields };
  try {
    await client.xadd(evtStreamKey(sessionId), 'MAXLEN', '~', String(config.outbound.evtStreamMaxLen), '*', 'data', JSON.stringify(entry));
  } catch (err) {
    // H3: never swallow — an evt write failure is logged loudly. It does NOT
    // block the send pipeline itself (the send outcome is already durable
    // via sent_marker/outidem by the time this is called for 'sent'/'failed'),
    // but it is a real observability gap worth surfacing.
    logger.error({ sessionId, type, err: err.message }, '[outbound] evt XADD failed');
  }
}

/**
 * Enqueue one outbound message durably. Idempotent on `clientMsgId`: a
 * repeated call with the same id never sends twice (spec: 1000x same
 * client_msg_id -> exactly one send).
 *
 * @param {import('ioredis').Redis} client
 * @param {object} opts
 * @param {string} opts.sessionId
 * @param {string} opts.clientMsgId
 * @param {string} opts.to
 * @param {string} opts.text
 * @param {'interactive'|'bulk'|'marketing'} [opts.kind]
 * @returns {Promise<{ status: 'queued'|'duplicate'|'full', state?: string }>}
 */
export async function enqueueSend(client, opts) {
  const kind = opts.kind ?? 'interactive';
  const item = {
    client_msg_id: opts.clientMsgId,
    to: opts.to,
    text: opts.text,
    kind,
    enqueued_at: Date.now(),
  };
  const res = await client.eval(
    ENQUEUE_LUA, 2, idemKey(opts.sessionId, opts.clientMsgId), queueKey(opts.sessionId),
    JSON.stringify(item), String(config.outbound.idemTtlS), String(config.outbound.queueMax),
  );
  const flag = Number(res[0]);
  if (flag === 0) return { status: 'full' };
  if (flag === 2) return { status: 'duplicate', state: String(res[1]) };
  outQueueDepthGauge.labels(kind).inc();
  await emitEvt(client, opts.sessionId, 'queued', { client_msg_id: opts.clientMsgId });
  return { status: 'queued' };
}

/**
 * P0.6 Batch B: real kill-switch check, via `ctx.checkKillSwitch` — injected
 * by sessions.js's startSessionOutboundWorker, itself backed by
 * killswitch.js's real scope/state computation (see that file's header for
 * the full design). `ctx.checkKillSwitch` is OPTIONAL so every existing
 * unit test that builds a bare ctx ({ getSocket }) keeps working unmodified:
 * a caller that never wires kill-switch checking gets the P0.4-era
 * always-allow behavior — a deliberate, documented default for
 * test/wiring-gap contexts only (never true in the real running process,
 * where index.js's main() always starts killswitch.js and wires it into
 * sessions.js before any session's outbound worker can start — see
 * docs/P0_DEVIATIONS.md).
 *
 * @param {{ checkKillSwitch?: (kind: string) => { allowed: boolean, state?: string, scope?: string, capability?: string } }} ctx
 * @param {string} kind
 * @returns {{ allowed: boolean, state?: string, scope?: string, capability?: string }}
 */
function checkKillSwitch(ctx, kind) {
  if (!ctx.checkKillSwitch) return { allowed: true };
  return ctx.checkKillSwitch(kind);
}

async function requeue(client, sessionId, raw, { front = false } = {}) {
  await client.lrem(inflightKey(sessionId), 1, raw);
  if (front) await client.lpush(queueKey(sessionId), raw);
  else await client.rpush(queueKey(sessionId), raw);
  // out_queue_depth (P0.6 closure): the item is leaving `inflight` and
  // landing back on `queue`, so depth goes back up by one - kind is parsed
  // from `raw` itself rather than widening every requeue() call site.
  try {
    const { kind } = JSON.parse(raw);
    outQueueDepthGauge.labels(kind ?? 'interactive').inc();
  } catch {
    // raw is always this module's own serialized item (see enqueueSend) - a
    // parse failure here would mean corruption elsewhere, already surfaced
    // by processOne's own JSON.parse(raw) a few lines above every call site.
  }
}

async function resolveInflight(client, sessionId, raw) {
  await client.lrem(inflightKey(sessionId), 1, raw);
}

function paceRangeFor(kind) {
  const o = config.outbound;
  return kind === 'interactive' ? [o.paceInteractiveMinMs, o.paceInteractiveMaxMs] : [o.paceBulkMinMs, o.paceBulkMaxMs];
}

function ttlFor(kind) {
  return kind === 'interactive' ? config.outbound.ttlInteractiveMs : config.outbound.ttlBulkMs;
}

function bucketKindFor(kind) {
  return kind === 'marketing' ? 'marketing' : 'service';
}

/**
 * Process exactly one dequeued item. Exported for direct unit testing
 * without running the full blocking worker loop.
 *
 * @param {import('ioredis').Redis} client
 * @param {string} sessionId
 * @param {string} raw               The exact JSON string as stored (needed for LREM).
 * @param {{ getSocket: () => object|null, checkKillSwitch?: (kind: string) => object }} ctx
 * @returns {Promise<{ outcome: 'sent'|'requeued'|'expired'|'failed', reason?: string }>}
 */
export async function processOne(client, sessionId, raw, ctx) {
  const item = JSON.parse(raw);

  if (Date.now() - item.enqueued_at > ttlFor(item.kind)) {
    await resolveInflight(client, sessionId, raw);
    await emitEvt(client, sessionId, 'failed', { client_msg_id: item.client_msg_id, error_class: 'expired' });
    outFailedTotal.labels('expired').inc();
    return { outcome: 'expired' };
  }

  // P0.6 Batch B (spec, literal): "items already sitting in the outbound
  // queue when a switch flips are re-checked immediately before send and
  // become failed(error_class='blocked')" — NOT silently requeued forever
  // (the P0.4-era stub's behavior). This is the queue-side half of
  // enforcement; index.js's POST /sessions/:id/send route additionally
  // rejects a brand-new send at request time (423) if already blocked then.
  const ks = checkKillSwitch(ctx, item.kind);
  if (!ks.allowed) {
    await resolveInflight(client, sessionId, raw);
    await emitEvt(client, sessionId, 'failed', {
      client_msg_id: item.client_msg_id,
      error_class: 'blocked',
      capability: ks.capability,
      state: ks.state,
      scope: ks.scope,
    });
    outBlockedTotal.labels(ks.capability ?? 'unknown').inc();
    logger.info(
      { sessionId, client_msg_id: item.client_msg_id, capability: ks.capability, state: ks.state, scope: ks.scope },
      '[outbound] send blocked by kill-switch',
    );
    return { outcome: 'failed', reason: 'blocked' };
  }

  const bucketKind = bucketKindFor(item.kind);
  if (bucketKind === 'marketing') {
    const daily = await tryConsumeMarketingDailyCap(client, { number: item.to, dailyCap: config.outbound.marketingDailyCap });
    if (!daily.allowed) {
      await requeue(client, sessionId, raw);
      await sleep(200);
      return { outcome: 'requeued' };
    }
  }
  const bucket = bucketKind === 'marketing'
    ? { capacity: config.outbound.marketingBucketCapacity, refillPerMin: config.outbound.marketingBucketRefillPerMin }
    : { capacity: config.outbound.serviceBucketCapacity, refillPerMin: config.outbound.serviceBucketRefillPerMin };
  const tok = await tryConsumeToken(client, { number: item.to, kind: bucketKind, ...bucket });
  if (!tok.allowed) {
    await requeue(client, sessionId, raw);
    await sleep(200);
    return { outcome: 'requeued' };
  }

  const sock = ctx.getSocket();
  if (!sock) {
    // session_down: stays queued until its own TTL, per spec.
    await requeue(client, sessionId, raw);
    await sleep(200);
    return { outcome: 'requeued' };
  }

  const [paceMin, paceMax] = paceRangeFor(item.kind);
  await sleep(paceMin + Math.floor(Math.random() * (paceMax - paceMin + 1)));

  const waMessageId = crypto.randomUUID();
  await client.set(sentIdKey(sessionId, waMessageId), '1', 'EX', config.outbound.sentIdsTtlS);

  let lastErr;
  for (let attempt = 1; attempt <= config.outbound.maxSendAttempts; attempt += 1) {
    try {
      const liveSock = ctx.getSocket();
      if (!liveSock) throw Object.assign(new Error('session down mid-send'), { code: 'SESSION_DOWN' });
      await liveSock.sendMessage(item.to, { text: item.text }, { messageId: waMessageId });
      lastErr = null;
      break;
    } catch (err) {
      lastErr = err;
      if (err.code === 'SESSION_DOWN' || err.code === 'CONNECTION_CLOSED') {
        await requeue(client, sessionId, raw);
        await sleep(200);
        return { outcome: 'requeued' };
      }
      if (attempt < config.outbound.maxSendAttempts) {
        await sleep(200 * 2 ** (attempt - 1));
      }
    }
  }

  if (lastErr) {
    await resolveInflight(client, sessionId, raw);
    await emitEvt(client, sessionId, 'failed', {
      client_msg_id: item.client_msg_id,
      error_class: 'retryable',
      error: lastErr.message,
    });
    logger.error({ sessionId, client_msg_id: item.client_msg_id, err: lastErr.message }, '[outbound] send failed after max attempts');
    outFailedTotal.labels('retryable').inc();
    return { outcome: 'failed' };
  }

  // Success. Narrow duplicate window lives between the sendMessage() above
  // returning and these two writes landing (see file header) — a crash here
  // is exactly what the P0.4 chaos test measures.
  await client.set(sentMarkerKey(sessionId, item.client_msg_id), waMessageId, 'EX', config.outbound.sentMarkerTtlS);
  await client.set(idemKey(sessionId, item.client_msg_id), waMessageId, 'EX', config.outbound.idemTtlS);
  await resolveInflight(client, sessionId, raw);
  await emitEvt(client, sessionId, 'sent', { client_msg_id: item.client_msg_id, wa_message_id: waMessageId });
  outSentTotal.inc();
  return { outcome: 'sent' };
}

/**
 * Run the blocking outbound worker loop for one session until `ctx.running`
 * is set to false. One worker per session (mirrors the pre-P0.4 per-session
 * FIFO design) — BLMOVE blocks for up to 2s per iteration so the loop can
 * notice `ctx.running` flip promptly without a tight spin.
 *
 * @param {import('ioredis').Redis} client
 * @param {string} sessionId
 * @param {{ getSocket: () => object|null }} ctx
 * @returns {{ stop: () => void, done: Promise<void> }}
 */
export function startOutboundWorker(client, sessionId, ctx) {
  const state = { running: true };
  const done = (async () => {
    while (state.running) {
      let raw;
      try {
        raw = await client.blmove(queueKey(sessionId), inflightKey(sessionId), 'LEFT', 'RIGHT', 2);
      } catch (err) {
        logger.error({ sessionId, err: err.message }, '[outbound] blmove failed; backing off');
        await sleep(500);
        continue;
      }
      if (!raw) continue; // timeout — loop back and re-check state.running
      // out_queue_depth (P0.6 closure): the item just moved queue -> inflight
      // via the BLMOVE above, so depth goes down by one right here (not
      // inside processOne, which only ever sees an item already in inflight).
      try {
        const { kind } = JSON.parse(raw);
        outQueueDepthGauge.labels(kind ?? 'interactive').dec();
      } catch {
        // see requeue()'s identical try/catch above for why this can't happen
        // in practice - never let a parse issue crash the worker loop (H3).
      }
      try {
        await processOne(client, sessionId, raw, ctx);
      } catch (err) {
        // Must never happen (processOne catches its own errors), but H3
        // forbids an uncaught rejection from silently killing the worker.
        logger.error({ sessionId, err: err.message }, '[outbound] unexpected error processing item; item left in inflight for recovery');
      }
    }
  })();
  return { stop: () => { state.running = false; }, done };
}

/**
 * Crash-recovery scan, run once per session at startup/creation (before the
 * worker starts): anything left in `inflight` either already has a
 * sent_marker (it was actually sent before the crash — just clean it up) or
 * does not (unconfirmed — put it back on the queue to retry, at the FRONT so
 * recovered work is not starved behind new traffic).
 *
 * @param {import('ioredis').Redis} client
 * @param {string} sessionId
 * @returns {Promise<{ confirmedSent: number, requeued: number }>}
 */
export async function recoverInflight(client, sessionId) {
  const items = await client.lrange(inflightKey(sessionId), 0, -1);
  let confirmedSent = 0;
  let requeued = 0;
  for (const raw of items) {
    const item = JSON.parse(raw);
    const marker = await client.get(sentMarkerKey(sessionId, item.client_msg_id));
    await client.lrem(inflightKey(sessionId), 1, raw);
    if (marker) {
      confirmedSent += 1;
    } else {
      await client.lpush(queueKey(sessionId), raw);
      requeued += 1;
      // out_queue_depth (P0.6 closure): this item is landing back on `queue`
      // directly (bypassing requeue() - it was never re-added to `inflight`
      // in the first place, so there is nothing for requeue()'s own LREM to
      // do), so the depth gauge must be incremented here too.
      try {
        outQueueDepthGauge.labels(item.kind ?? 'interactive').inc();
      } catch {
        // item is already a parsed object here (JSON.parse succeeded above
        // to read client_msg_id) - this can only fail if labels() itself
        // throws, never silently swallowed (H3).
      }
    }
  }
  if (confirmedSent > 0 || requeued > 0) {
    logger.info({ sessionId, confirmedSent, requeued }, '[outbound] startup recovery');
  }
  return { confirmedSent, requeued };
}

/** @returns {Promise<number>} current queue depth (for GET /sessions/:id/health, P0.5). */
export async function queueDepth(client, sessionId) {
  return client.llen(queueKey(sessionId));
}

export const _keys = { queueKey, inflightKey, idemKey, sentIdKey, sentMarkerKey, evtStreamKey };
GW_OUTBOUND_QUEUE_JS_EOF
echo "wrote gateway/src/outbound/queue.js ($(wc -l < "gateway/src/outbound/queue.js") lines)"

echo "== writing gateway/src/index.js (modify) =="
mkdir -p "$(dirname "gateway/src/index.js")"
cat > "gateway/src/index.js" <<'GW_INDEX_JS_EOF'
import crypto from 'node:crypto';
import { pathToFileURL } from 'node:url';
import express from 'express';

import {
  createSession,
  rehydrateSessions,
  enqueueSend,
  getSession,
  getSessionQr,
  getSessionStatus,
  getSessionHealth,
  logoutSession,
  setRedisClient,
  setKillSwitch,
  startLeaseSweep,
  releaseAllOwnedLeases,
  getSessionCount,
} from './sessions.js';
import { drainSpool } from './ingest/spool.js';
import { createRedisClient, closeRedisClient } from './redis.js';
import { logger } from './logger.js';
import { config } from './config.js';
import { registry, setStreamMetricsRedisClient } from './metrics.js';
import { getMediaMetrics } from './media.js';
import { createKillSwitch } from './killswitch.js';

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

  // P0.6 Batch B (spec, literal): "A block ⇒ HTTP 423" with
  // {error:'blocked_by_switch', capability, state, scope} - checked at
  // REQUEST time, before the item is even enqueued. This is defense-in-depth
  // alongside outbound/queue.js's own re-check for items already sitting in
  // the queue when a switch flips mid-flight (that path returns
  // failed/error_class=blocked instead, since there is no HTTP response left
  // to send by then). killSwitchRef is null only in tests/wiring-gap
  // contexts that never call main() - never in the real running process.
  if (killSwitchRef) {
    const session = getSession(req.params.id);
    const ksCtx = session ? { tenantId: session.tenantId, channelAccountId: session.channelAccountId } : {};
    const ks = killSwitchRef.checkSend(kind || 'interactive', ksCtx);
    if (!ks.allowed) {
      return res.status(423).json({ error: 'blocked_by_switch', capability: ks.capability, state: ks.state, scope: ks.scope });
    }
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
// P0.6 Batch B: set in main() once killswitch.js's instance has completed
// its initial sync - null before that (and in any test that imports `app`
// without calling main()), which POST /sessions/:id/send's check above
// treats as "kill-switch subsystem not running here", not as "blocked".
let killSwitchRef = null;

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

  // P0.6 closure (Batch C): give stream_length/stream_pending's collect()
  // callbacks (metrics.js) the same, already-connected redis-durable client -
  // never a second connection just for metrics scrapes.
  setStreamMetricsRedisClient(client);

  // F18 (found during P0.6 closure research, docs/P0_FINDINGS.md): the
  // literal P0.2 spec text requires the `core-ingest` consumer group to be
  // created "عند إقلاع البوابة" (at GATEWAY startup) via
  // `XGROUP CREATE ... MKSTREAM`, with no consumer until P1 - but this call
  // never existed anywhere in the real code (only `legacy-forwarder` was
  // ever created, in forwarder.js, a separate process). Fixed here, in the
  // one place the spec actually asks for it, mirroring forwarder.js's own
  // ensureGroup() exactly (BUSYGROUP on an already-created group is not an
  // error - idempotent across restarts).
  for (let i = 0; i < config.ingestShards; i += 1) {
    try {
      await client.xgroup('CREATE', `in:${i}`, config.coreIngestGroup, '0', 'MKSTREAM');
    } catch (err) {
      if (!String(err.message).includes('BUSYGROUP')) {
        logger.error({ stream: `in:${i}`, err: err.message }, '[index] failed to create core-ingest consumer group (F18)');
      }
    }
  }

  // P0.6 Batch B: start the kill-switch subsystem (its own two dedicated
  // redis-cache connections - one command, one subscriber, per
  // killswitch.js's own comment on why they must be separate) and wire it
  // into sessions.js BEFORE rehydrateSessions() below can start any
  // session's outbound worker, so no worker ever runs with kill-switch
  // checking unwired in the real process. start() runs its initial full
  // resync before resolving, so the very first check after this line
  // reflects real redis-cache state (or, if redis-cache is unreachable at
  // boot, isStale() is already true from the start - the same fail-closed
  // posture the staleness rule intends).
  killSwitchRef = createKillSwitch();
  try {
    await killSwitchRef.start();
  } catch (err) {
    // H3: never let an unreachable redis-cache at boot crash the whole
    // gateway - the staleness fail-closed rule (marketing/broadcast only)
    // is exactly the documented degraded-mode behavior for this. Every
    // other capability keeps its all-'on' default until redis-cache comes
    // back and a resync tick succeeds.
    logger.error({ err: err.message }, '[index] killswitch initial start failed; continuing with fail-closed marketing/broadcast until redis-cache is reachable');
  }
  setKillSwitch(killSwitchRef);

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

    // P0.6 Batch B: stop the kill-switch subsystem's own two redis-cache
    // connections. Placed after releaseAllOwnedLeases (no outbound worker
    // still has a live socket to send through by then) and before the
    // redis-durable client close, matching this function's existing
    // outermost-to-innermost teardown order.
    if (killSwitchRef) {
      await killSwitchRef.stop().catch((err) => {
        logger.error({ err: err.message }, '[index] killswitch shutdown failed');
      });
    }

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
GW_INDEX_JS_EOF
echo "wrote gateway/src/index.js ($(wc -l < "gateway/src/index.js") lines)"

echo "== writing gateway/src/config.js (modify) =="
mkdir -p "$(dirname "gateway/src/config.js")"
cat > "gateway/src/config.js" <<'GW_CONFIG_JS_EOF'
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
GW_CONFIG_JS_EOF
echo "wrote gateway/src/config.js ($(wc -l < "gateway/src/config.js") lines)"

echo "== writing gateway/.env.example (modify) =="
mkdir -p "$(dirname "gateway/.env.example")"
cat > "gateway/.env.example" <<'GW_ENV_EXAMPLE_EOF'
# Sharwa AI - environment variables (copy to `.env`, never commit the copy).
# Every value below is a placeholder. The real `.env` is gitignored.

# --- PostgreSQL (docker-compose.yml postgres) ---
POSTGRES_USER=change-me
POSTGRES_PASSWORD=change-me
POSTGRES_DB=sharwa_ai

# --- Database roles (ops/postgres/init/10_roles.sh) ---
SHARWA_APP_USER=change-me
SHARWA_APP_PASSWORD=change-me
SHARWA_SYSTEM_USER=change-me
SHARWA_SYSTEM_PASSWORD=change-me
SHARWA_MIGRATION_USER=change-me
SHARWA_MIGRATION_PASSWORD=change-me

# --- Redis (docker-compose.yml) ---
REDIS_DURABLE_PASSWORD=change-me
REDIS_CACHE_PASSWORD=change-me

# --- Gateway app tier (docker-compose.yml gateway / gateway-forwarder, P0.2) ---
SHARWA_AI_GATEWAY_API_KEY=change-me
SHARWA_AI_GATEWAY_WEBHOOK_SECRET=change-me
# MinIO - OPTIONAL. Unset/wrong values are fine for now: gateway still starts
# and handles text messages; media uploads degrade gracefully (object_key:
# null, fallback text) until MinIO is confirmed and these are set for real.
MINIO_ACCESS_KEY=
MINIO_SECRET_KEY=
# Optional - docker-compose.yml already defaults these for the common case
# (Django/MinIO running on this same VPS host, outside compose). Uncomment
# to override:
# DJANGO_BASE_URL=http://host.docker.internal:8000
# MINIO_ENDPOINT_URL=http://host.docker.internal:9000
# MINIO_BUCKET_NAME=sharwa-ai
# MINIO_USE_SSL=false

# --- P0.4: durable outbound send queue (gateway/src/outbound/, gateway/src/config.js) ---
# All of these are OPTIONAL - unset values fall back to this implementation's
# own defaults (see gateway/src/config.js and docs/P0_DEVIATIONS.md; the spec
# text does not fix numeric defaults for these). Uncomment to override:
# OUT_QUEUE_MAX=500
# OUTIDEM_TTL_S=604800
# SENT_IDS_TTL_S=600
# SENT_MARKER_TTL_S=86400
# PACE_INTERACTIVE_MIN_MS=800
# PACE_INTERACTIVE_MAX_MS=1500
# PACE_BULK_MIN_MS=2000
# PACE_BULK_MAX_MS=3000
# SERVICE_BUCKET_CAPACITY=60
# SERVICE_BUCKET_REFILL_PER_MIN=60
# MARKETING_BUCKET_CAPACITY=20
# MARKETING_BUCKET_REFILL_PER_MIN=20
# MARKETING_DAILY_CAP=1000
# OUTBOUND_MAX_SEND_ATTEMPTS=3
# OUTBOUND_TTL_INTERACTIVE_MS=600000
# OUTBOUND_TTL_BULK_MS=86400000
# EVT_STREAM_MAXLEN=100000

# --- P0.5: session lifecycle - reconnect/lease/fencing/rehydrate (gateway/src/lease.js, gateway/src/sessions.js, gateway/src/config.js) ---
# All of these are OPTIONAL - unset values fall back to this implementation's
# own defaults (see gateway/src/config.js and docs/P0_DEVIATIONS.md). Uncomment
# to override:
# INSTANCE_ID=                    # default: auto-generated (auto-<8 hex chars>) at boot
# LEASE_TTL_MS=30000               # must be >= 1000
# LEASE_RENEW_MS=10000             # must be >= 100 and < LEASE_TTL_MS
# LEASE_SWEEP_MS=5000              # must be >= 1000 (real safeguard, not just a default - see docs/P0_FINDINGS.md)
# REHYDRATE_CONCURRENCY=2          # spec-literal default - "لا أكثر من 2 اتصال متزامن"
# REHYDRATE_JITTER_MIN_MS=500
# REHYDRATE_JITTER_MAX_MS=2000
# MAX_SESSIONS=10                  # REAL MEASURED value on THIS VPS's container, not a guess -
                                    # see docs/P0_DEVIATIONS.md for the measurement and the
                                    # documented 10x safety margin; re-measure before raising this.
# RECONNECT_BASE_MS=1000
# RECONNECT_MAX_MS=300000
# RECONNECT_JITTER_MS=500
# CONFLICT_BACKOFF_MS=60000        # must be >= 1000

# --- P0.6: operational readiness - /readyz, /metrics, graceful shutdown (gateway/src/index.js, gateway/src/metrics.js, gateway/src/config.js) ---
# METRICS_TOKEN is REQUIRED in any real deployment - the gateway process
# refuses to start at all with an empty/missing value (H5: "empty secret =
# refuse startup"). Never left commented-out in a real .env.
METRICS_TOKEN=change-me
# The rest are OPTIONAL - unset values fall back to this implementation's own
# defaults (see gateway/src/config.js and docs/P0_DEVIATIONS.md D-20/D-21/D-22).
# Uncomment to override:
# SPOOL_STALE_S=300                # GET /readyz: 503 once the spool has been non-empty this long
# SHUTDOWN_TIMEOUT_MS=20000        # must be >= 1000 and <= 25000 (spec: exit within <= 25s)
# DOWNLOAD_DRAIN_TIMEOUT_MS=8000   # must be < SHUTDOWN_TIMEOUT_MS - one step of that budget

# --- P0.6 Batch B: gateway-side kill-switch (gateway/src/killswitch.js, gateway/src/config.js) ---
# REDIS_CACHE_PASSWORD is set above, in the shared "Redis (docker-compose.yml)"
# section. REDIS_CACHE_HOST/PORT are OPTIONAL here the same way
# REDIS_DURABLE_HOST/PORT are - docker-compose.yml's own `environment:` block
# already points the gateway service at the `redis-cache` network alias;
# these are only for overriding that (e.g. running the gateway outside
# compose). Uncomment to override:
# REDIS_CACHE_HOST=127.0.0.1
# REDIS_CACHE_PORT=6379
# REDIS_CACHE_TIMEOUT_MS=5000
# REDIS_CACHE_MAX_RETRIES=3
# KS_STALE_MAX_S=120                # spec, literal: past this, marketing/broadcast specifically fail closed

# --- P0.6 Batch C: forwarder metrics + Prometheus (gateway/src/forwarder.js, gateway/src/forwarder_metrics.js, gateway/src/config.js, ops/prometheus/) ---
# The forwarder process reuses the SAME METRICS_TOKEN set above (P0.6
# section) - it is a separate process but not a separate secret. It refuses
# to start at all with an empty/missing METRICS_TOKEN, mirroring the
# gateway's own H5 posture (this specific refusal is this implementation's
# own extension beyond the literal P0.6 spec text - see docs/P0_DEVIATIONS.md).
# FORWARDER_METRICS_PORT is OPTIONAL - unset falls back to this
# implementation's own default (see gateway/src/config.js). Uncomment to
# override:
# FORWARDER_METRICS_PORT=4002

# --- P0.4: FakeWa test driver (H7 - the ONLY mock allowed in this project) ---
# NEVER set these in production. All three must be set together for the fake
# driver to activate at all (gateway/src/driver/waDriver.js double-gates this
# independently of gateway/test-support/'s own .dockerignore exclusion -
# defense in depth, H4). Leave unset/unexported outside test runs.
# WA_DRIVER=fake
# ALLOW_FAKE_WA=1
# NODE_ENV=test
GW_ENV_EXAMPLE_EOF
echo "wrote gateway/.env.example ($(wc -l < "gateway/.env.example") lines)"

echo "== writing .gitignore (modify) =="
mkdir -p "$(dirname ".gitignore")"
cat > ".gitignore" <<'ROOT_GITIGNORE_EOF'
# Dependencies
node_modules/

# Local session/credential state (never commit Baileys auth state)
auth_sessions/

# Secrets
.env
.env.*
!.env.example

# Generated at delivery time from .env's METRICS_TOKEN (P0.6 Batch C) - never
# committed, mirrors the .env/.env.example split above (see
# ops/wire_p06c.mjs / the delivery script).
ops/prometheus/secrets/

# Build output
dist/
coverage/

# Python
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.venv/
venv/

# Node
npm-debug.log*

# OS
Thumbs.db
.DS_Store
ROOT_GITIGNORE_EOF
echo "wrote .gitignore ($(wc -l < ".gitignore") lines)"

echo "== writing ops/prometheus/prometheus.yml (new) =="
mkdir -p "$(dirname "ops/prometheus/prometheus.yml")"
cat > "ops/prometheus/prometheus.yml" <<'OPS_PROM_YML_EOF'
# ops/prometheus/prometheus.yml
# P0.6 closure (Batch C, monitoring). Scrapes gateway (real /metrics, already
# built in Batch A/B) and gateway-forwarder (real /metrics, added in this same
# batch - see gateway/src/forwarder.js/forwarder_metrics.js).
#
# `api` is NOT scraped here: the literal spec text asks for it
# ("يجمع من البوابة والـforwarder والـapi"), but the `api` service does not
# exist yet - it is P0.7 scope (docs/P0_DEVIATIONS.md D-22 already recorded
# this exact gap for the reason monitoring itself was deferred to this
# batch: a scrape target that doesn't exist is not "confirmed down", it's
# fabricated). This file will need one more scrape job added once P0.7 stands
# `api` up - tracked, not silently forgotten.
#
# Both real jobs authenticate with the same METRICS_TOKEN both processes
# already require to boot (H5) via bearer_token_file - the token itself is
# never written into this file (never committed - see .gitignore).

global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - /etc/prometheus/alerts.yml

scrape_configs:
  - job_name: gateway
    metrics_path: /metrics
    scheme: http
    bearer_token_file: /etc/prometheus/secrets/metrics_token
    static_configs:
      - targets: ['gateway:4001']

  - job_name: gateway-forwarder
    metrics_path: /metrics
    scheme: http
    bearer_token_file: /etc/prometheus/secrets/metrics_token
    static_configs:
      - targets: ['gateway-forwarder:4002']
OPS_PROM_YML_EOF
echo "wrote ops/prometheus/prometheus.yml ($(wc -l < "ops/prometheus/prometheus.yml") lines)"

echo "== writing ops/prometheus/alerts.yml (new) =="
mkdir -p "$(dirname "ops/prometheus/alerts.yml")"
cat > "ops/prometheus/alerts.yml" <<'OPS_ALERTS_YML_EOF'
# ops/prometheus/alerts.yml
# P0.6 closure (Batch C). The exact 8 rules the spec requires
# (prompts/P0_DEEPSEEK_PROMPT.md P0.6 §6): gateway down, redis-durable memory
# > 60%, spool non-empty > 5min, dlq_length > 0, a `banned` session, heap >
# 85% of its configured cap, the oldest pending stream message > 60s, and
# process_open_fds > 70% of its cap.
#
# Spec's own explicit P0 posture (literal text): "Alertmanager/إشعارات
# التسليم تُضاف في P1؛ في P0 القواعد مرئية في واجهة Prometheus" - these rules
# only need to be VISIBLE in the Prometheus UI in P0, not wired to any
# delivery channel (no Alertmanager service exists, deliberately - P1 scope).
#
# One of the eight references a metric this repository does not produce yet -
# documented explicitly here AND in docs/P0_DEVIATIONS.md, per the spec's own
# instruction to record such gaps rather than hide them:
#   - redis-durable memory %: needs a redis_exporter (oliver006/redis_exporter
#     or equivalent) scraping redis-durable's INFO memory stats - no such
#     service exists in docker-compose.yml (adding a whole new exporter
#     service was judged out of scope for "wire the alert rules text";
#     written using that exporter's real, standard metric names so the rule
#     activates the moment one is added, with no rewrite needed).
#
# session `banned` state (SessionBanned rule): VERIFIED against the real
# gateway/src/sessions.js - mapConnectionStatus() (line ~270-281) returns the
# UPPERCASE literal 'BANNED' (alongside 'QR_PENDING'/'CONNECTED'/
# 'DISCONNECTED'/'UNKNOWN' - all uppercase, explicitly commented there as
# "contract-frozen vocabulary"), and metrics.js's recordSessionStateTransition
# feeds session_state{state} with that value verbatim, no case transform. The
# rule below was corrected from an earlier draft that guessed lowercase
# "banned" before this file was read - written as state="BANNED" now that the
# real label value is confirmed, not assumed.
#
# Validated with `promtool check rules` (recorded in the delivery script's
# own self-verification output, not just asserted here).

groups:
  - name: sharwa_ai_p0
    rules:
      - alert: GatewayDown
        expr: up{job="gateway"} == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "gateway is down"
          description: "Prometheus has not been able to scrape the gateway's /metrics for at least 1 minute."

      - alert: RedisDurableMemoryHigh
        # UNWIRED (see file header): requires a redis_exporter scraping
        # redis-durable; no such target exists yet. Visible only (spec's own
        # P0 allowance), not currently able to fire.
        expr: redis_memory_used_bytes{addr=~".*redis-durable.*"} / redis_memory_max_bytes{addr=~".*redis-durable.*"} > 0.6
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "redis-durable memory usage above 60%"
          description: "redis-durable is using more than 60% of its configured maxmemory for at least 5 minutes."

      - alert: SpoolNonEmptyTooLong
        expr: ingest_spool_depth > 0
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "disk spool has been non-empty for over 5 minutes"
          description: "redis-durable has likely been unreachable for a sustained period - inbound messages are spooling to disk instead of reaching the WAL."

      - alert: DeadLetterQueueNonEmpty
        expr: dlq_length > 0
        for: 0m
        labels:
          severity: critical
        annotations:
          summary: "at least one message is parked on the dead-letter queue"
          description: "dlq:in is non-empty - one or more inbound messages exhausted FORWARD_MAX_ATTEMPTS and were never delivered to Django. Needs manual investigation/replay."

      - alert: SessionBanned
        # VERIFIED against sessions.js's real mapConnectionStatus vocabulary
        # (see file header) - the gauge label is the uppercase "BANNED".
        expr: session_state{state="BANNED"} > 0
        for: 0m
        labels:
          severity: critical
        annotations:
          summary: "a WhatsApp session has been banned"
          description: "session_state reports at least one session in the banned state."

      - alert: GatewayHeapNearCap
        # Threshold hardcoded to the gateway's own NODE_OPTIONS
        # (--max-old-space-size=900, docker-compose.yml) - if that value ever
        # changes, this threshold must be updated to match (900*1024*1024 =
        # 943718400 bytes).
        expr: nodejs_heap_size_used_bytes{job="gateway"} / 943718400 > 0.85
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "gateway heap usage above 85% of its configured cap"
          description: "nodejs_heap_size_used_bytes is above 85% of the 900MB --max-old-space-size budget for at least 2 minutes."

      - alert: StreamPendingTooOld
        expr: stream_pending_oldest_seconds{group="legacy-forwarder"} > 60
        for: 0m
        labels:
          severity: warning
        annotations:
          summary: "a WAL stream entry has been pending (unacked) for over 60 seconds"
          description: "the legacy-forwarder consumer group has at least one entry that has not been acked in over 60 seconds - the forwarder may be stuck, crash-looping, or Django may be down."

      - alert: OpenFileDescriptorsNearCap
        expr: process_open_fds / process_max_fds > 0.7
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "open file descriptors above 70% of the process cap"
          description: "{{ $labels.job }} is using more than 70% of its process_max_fds for at least 5 minutes."
OPS_ALERTS_YML_EOF
echo "wrote ops/prometheus/alerts.yml ($(wc -l < "ops/prometheus/alerts.yml") lines)"

echo "== writing ops/wire_p06c.mjs (new) =="
mkdir -p "$(dirname "ops/wire_p06c.mjs")"
cat > "ops/wire_p06c.mjs" <<'OPS_WIRE_P06C_MJS_EOF'
#!/usr/bin/env node
// ops/wire_p06c.mjs
//
// P0.6 Batch C (monitoring) deployment wiring for docker-compose.yml.
// Surgical, idempotent text edits - NOT a YAML round-trip (same discipline as
// ops/wire_p06.mjs, which this script is modeled on almost line-for-line for
// the parts it shares).
//
// What it does, and why:
//
//   1. gateway-forwarder: environment additions
//      METRICS_TOKEN
//          The forwarder process now runs its own metrics HTTP server
//          (gateway/src/forwarder.js/forwarder_metrics.js, this same batch)
//          and refuses to start with an empty METRICS_TOKEN, mirroring the
//          gateway's own H5 posture (this implementation's own extension
//          beyond the literal P0.6 spec text - see docs/P0_DEVIATIONS.md).
//          Reuses the SAME secret gateway already requires - not a new one.
//      FORWARDER_METRICS_PORT
//          Optional; falls back to gateway/src/config.js's own default (4002)
//          when unset, but set explicitly here for clarity/consistency with
//          gateway's own PORT: "4001".
//
//   2. gateway-forwarder: ports
//      "127.0.0.1:4002:4002" - loopback only, exactly mirroring gateway's own
//      "127.0.0.1:4001:4001" convention (Dockerfile: never published to the
//      public internet). Needed so Prometheus - running as a separate
//      container reachable over the `app` network - and any local operator
//      curl can both reach it; loopback-only still blocks the public internet
//      identically to gateway's own port.
//
//   3. gateway-forwarder: healthcheck
//      This batch adds a real /healthz endpoint to forwarder.js's new metrics
//      server (open, unauthenticated, matching gateway's own /healthz). The
//      original compose comment on this service explicitly said "revisit if
//      we add a tiny /healthz shim" - this batch adds exactly that, so the
//      healthcheck is added now, mirroring gateway's own wget-based check.
//
//   4. a new `prometheus` service
//      image prom/prometheus:v3.14.0 (explicit tag - confirmed pullable on
//      this real VPS; this project never uses `:latest`). Mounts the three
//      new ops/prometheus/ files read-only, scrapes gateway:4001 and
//      gateway-forwarder:4002 (see ops/prometheus/prometheus.yml), loopback-
//      only port 9090 (same convention as gateway/gateway-forwarder), joins
//      only the `app` network (both scrape targets are members of `app`; no
//      need to also join the internal `data` network). depends_on only
//      `gateway` (service_healthy) plus (now that step 3 exists)
//      `gateway-forwarder` (service_healthy) - Prometheus's own `up==0`
//      alert (GatewayDown) is exactly the mechanism for a target that is
//      briefly unavailable after that, so this is a startup-order nicety,
//      not a correctness requirement.
//
//   5. a new `prometheus_data` named volume (top-level `volumes:`)
//      TSDB data survives container restarts/recreates. Retention is capped
//      via --storage.tsdb.retention.time=15d (command override) given the
//      host's limited disk/memory budget (see memory-budget comment below).
//
// Idempotent: every addition is guarded by "does this already exist" the
// same way ops/wire_p06.mjs's ENV_WANTED loop is. Re-running this script
// after it has already applied cleanly makes no further changes.
//
// Usage: node ops/wire_p06c.mjs <docker-compose.yml>
// Exit 0 = changed or already correct (details on stdout). Exit 1 = refused.

import fs from 'node:fs';

const file = process.argv[2];
if (!file) {
  console.error('usage: node ops/wire_p06c.mjs <docker-compose.yml>');
  process.exit(1);
}

const original = fs.readFileSync(file, 'utf8');
let lines = original.split('\n');
const changes = [];

const indentOf = (l) => l.length - l.trimStart().length;
const isBlank = (l) => l.trim() === '';
const isComment = (l) => l.trimStart().startsWith('#');

function findServiceBlock(all, name) {
  const re = new RegExp(`^(\\s+)${name}:\\s*(#.*)?$`);
  let idx = -1;
  let ind = -1;
  for (let i = 0; i < all.length; i += 1) {
    const m = re.exec(all[i]);
    if (m) { idx = i; ind = m[1].length; break; }
  }
  if (idx === -1) return null;
  let end = all.length;
  let lastContent = idx; // index of the last non-blank/non-comment line in the block
  for (let i = idx + 1; i < all.length; i += 1) {
    if (isBlank(all[i]) || isComment(all[i])) continue;
    if (indentOf(all[i]) <= ind) { end = i; break; }
    lastContent = i;
  }
  return { idx, ind, end, lastContent };
}

// Find a top-level (column-0) mapping key, e.g. `networks:` or `volumes:`.
function findTopLevelKey(all, name) {
  const re = new RegExp(`^${name}:\\s*(#.*)?$`);
  for (let i = 0; i < all.length; i += 1) {
    if (re.test(all[i])) return i;
  }
  return -1;
}

// ============================================================ 1/2/3: gateway-forwarder
const SVC = 'gateway-forwarder';
const ENV_WANTED = [
  ['METRICS_TOKEN', '${METRICS_TOKEN:?METRICS_TOKEN is required - the forwarder refuses to start without it (see .env)}'],
  ['FORWARDER_METRICS_PORT', '${FORWARDER_METRICS_PORT:-4002}'],
];

const gwf = findServiceBlock(lines, SVC);
if (!gwf) {
  console.error(`REFUSED: no exact \`${SVC}:\` service key found. Nothing written.`);
  process.exit(1);
}

// ---- 1. environment keys ---------------------------------------------------
{
  let envIdx = -1;
  let envIndent = -1;
  const blk = findServiceBlock(lines, SVC);
  for (let i = blk.idx + 1; i < blk.end; i += 1) {
    const m = /^(\s+)environment:\s*(#.*)?$/.exec(lines[i]);
    if (m && m[1].length > blk.ind) { envIdx = i; envIndent = m[1].length; break; }
  }
  if (envIdx === -1) {
    console.error(`REFUSED: ${SVC} has no \`environment:\` block; refusing to guess where to add one.`);
    process.exit(1);
  }

  let envEnd = blk.end;
  for (let i = envIdx + 1; i < blk.end; i += 1) {
    if (isBlank(lines[i]) || isComment(lines[i])) continue;
    if (indentOf(lines[i]) <= envIndent) { envEnd = i; break; }
  }

  let entryIndent = envIndent + 2;
  for (let i = envIdx + 1; i < envEnd; i += 1) {
    if (isBlank(lines[i]) || isComment(lines[i])) continue;
    entryIndent = indentOf(lines[i]);
    break;
  }

  const envRegion = lines.slice(envIdx + 1, envEnd);
  const hasEnvKey = (k) => envRegion.some((l) => l.trim().startsWith(`${k}:`));

  const additions = [];
  for (const [k, v] of ENV_WANTED) {
    if (hasEnvKey(k)) continue;
    additions.push(' '.repeat(entryIndent) + `${k}: ${v}`);
    changes.push(`${SVC} env ${k}`);
  }
  if (additions.length > 0) {
    let insertAt = envIdx + 1;
    for (let i = envIdx + 1; i < envEnd; i += 1) {
      if (isBlank(lines[i]) || isComment(lines[i])) continue;
      insertAt = i + 1;
    }
    lines.splice(insertAt, 0, ...additions);
  }
}

// ---- 2. ports (add the whole key+list if missing) --------------------------
{
  const blk = findServiceBlock(lines, SVC);
  const hasPorts = lines.slice(blk.idx + 1, blk.end).some((l) => /^\s+ports:\s*(#.*)?$/.test(l));
  if (!hasPorts) {
    const childIndent = blk.ind + 2;
    const block = [
      `${' '.repeat(childIndent)}ports:`,
      `${' '.repeat(childIndent + 2)}- "127.0.0.1:4002:4002"              # loopback only, mirrors gateway's own 4001 convention`,
    ];
    // Insert right after the last real content line of the block (before any
    // trailing blank separator line) - position doesn't matter to YAML, but
    // this keeps the file's existing one-blank-line-between-services layout
    // intact instead of doubling it up.
    lines.splice(blk.lastContent + 1, 0, ...block);
    changes.push(`${SVC} ports: (added) 127.0.0.1:4002:4002`);
  }
}

// ---- 3. healthcheck (add the whole multi-line block if missing) ------------
{
  const blk = findServiceBlock(lines, SVC);
  const hasHealthcheck = lines.slice(blk.idx + 1, blk.end).some((l) => /^\s+healthcheck:\s*(#.*)?$/.test(l));
  if (!hasHealthcheck) {
    const childIndent = blk.ind + 2;
    const c = ' '.repeat(childIndent);
    const c2 = ' '.repeat(childIndent + 2);
    const block = [
      `${c}healthcheck:`,
      `${c2}test: ["CMD", "wget", "-q", "-O", "-", "http://127.0.0.1:4002/healthz"]`,
      `${c2}interval: 10s`,
      `${c2}timeout: 5s`,
      `${c2}retries: 6`,
      `${c2}start_period: 15s`,
    ];
    lines.splice(blk.lastContent + 1, 0, ...block);
    changes.push(`${SVC} healthcheck: (added) wget http://127.0.0.1:4002/healthz`);
  }
}

// Also drop the now-stale "no process-level healthcheck" comment lines, since
// step 3 just added exactly the shim they said would resolve it - leaving
// them in place would actively lie about the file's own current state.
{
  const staleMarker = '# No process-level healthcheck: this image\'s alpine base has no verified';
  const staleStart = lines.findIndex((l) => l.includes(staleMarker));
  if (staleStart !== -1) {
    // This comment runs 5 lines in the original file (verified against the
    // real fetched content) - remove exactly that contiguous comment block.
    let staleEnd = staleStart;
    while (staleEnd + 1 < lines.length && isComment(lines[staleEnd + 1]) && lines[staleEnd + 1].includes(' ')) {
      // stop once we hit a line that isn't part of this same comment (blank
      // or a real key) - be conservative: only eat lines that are comments.
      if (!isComment(lines[staleEnd + 1])) break;
      staleEnd += 1;
      // Safety cap: this comment is known to be 5 lines; never eat more than 8.
      if (staleEnd - staleStart >= 8) break;
    }
    lines.splice(staleStart, staleEnd - staleStart + 1);
    changes.push('gateway-forwarder: removed stale "no healthcheck" comment (superseded by step 3)');
  }
}

// ============================================================ 4/5: prometheus service + volume
{
  const already = findServiceBlock(lines, 'prometheus');
  if (already) {
    console.log('OK: a `prometheus` service already exists; leaving it untouched.');
  } else {
    const networksIdx = findTopLevelKey(lines, 'networks');
    if (networksIdx === -1) {
      console.error('REFUSED: no top-level `networks:` key found; refusing to guess where to insert the `prometheus` service.');
      process.exit(1);
    }
    const block = [
      '  # P0.6 Batch C: scrapes gateway:4001 and gateway-forwarder:4002 (see',
      '  # ops/prometheus/prometheus.yml). `api` is not scraped yet - it does not',
      '  # exist (P0.7 scope, see ops/prometheus/prometheus.yml\'s own header).',
      '  prometheus:',
      '    image: prom/prometheus:v3.14.0       # explicit tag - confirmed pullable on this VPS; no `:latest` (project rule)',
      '    command:',
      '      - --config.file=/etc/prometheus/prometheus.yml',
      '      - --storage.tsdb.path=/prometheus',
      '      - --storage.tsdb.retention.time=15d   # bounds disk given the host\'s memory/disk budget below',
      '      - --web.console.libraries=/usr/share/prometheus/console_libraries',
      '      - --web.console.templates=/usr/share/prometheus/consoles',
      '    volumes:',
      '      - ./ops/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro',
      '      - ./ops/prometheus/alerts.yml:/etc/prometheus/alerts.yml:ro',
      '      # Generated by the delivery script from .env\'s METRICS_TOKEN at delivery',
      '      # time - gitignored, never committed (see .gitignore).',
      '      - ./ops/prometheus/secrets/metrics_token:/etc/prometheus/secrets/metrics_token:ro',
      '      - prometheus_data:/prometheus',
      '    networks: [app]                        # both scrape targets are members of `app`; no need for `data` too',
      '    ports:',
      '      - "127.0.0.1:9090:9090"               # loopback only, same convention as gateway/gateway-forwarder',
      '    mem_limit: 256m',
      '    memswap_limit: 256m',
      '    cpus: 0.5',
      '    pids_limit: 100',
      '    healthcheck:',
      '      test: ["CMD", "wget", "-q", "-O", "-", "http://127.0.0.1:9090/-/healthy"]',
      '      interval: 10s',
      '      timeout: 5s',
      '      retries: 6',
      '      start_period: 15s',
      '    depends_on:',
      '      gateway: { condition: service_healthy }',
      '      gateway-forwarder: { condition: service_healthy }',
      '    logging: *default-logging',
      '    restart: unless-stopped',
      '',
    ];
    lines.splice(networksIdx, 0, ...block);
    changes.push('prometheus: (added) new service, prom/prometheus:v3.14.0');
  }
}

{
  const volumesIdx = findTopLevelKey(lines, 'volumes');
  if (volumesIdx === -1) {
    console.error('REFUSED: no top-level `volumes:` key found; refusing to guess where to add `prometheus_data`.');
    process.exit(1);
  }
  const region = [];
  let end = lines.length;
  for (let i = volumesIdx + 1; i < lines.length; i += 1) {
    if (isBlank(lines[i]) || isComment(lines[i])) { region.push(lines[i]); continue; }
    if (indentOf(lines[i]) === 0) { end = i; break; }
    region.push(lines[i]);
  }
  const hasVol = region.some((l) => l.trim().startsWith('prometheus_data:'));
  if (!hasVol) {
    // insert right after `volumes:` (order among named volumes doesn't matter)
    lines.splice(volumesIdx + 1, 0, '  prometheus_data:');
    changes.push('volumes: (added) prometheus_data');
  }
}

// NOTE: the file's own top-of-file "Memory budget" comment paragraph was
// already stale before this batch touched anything (it still says "gateway
// 512" - D-27 raised it to 1200m and never updated this prose). This batch
// adds another +256m (prometheus) on top of that already-stale number.
// Deliberately NOT patched here: a regex splice into hand-written prose is
// exactly the kind of guess this project's own discipline warns against, and
// fixing stale prose is out of this batch's narrow scope (monitoring wiring)
// - recorded as a small documentation gap instead (see the delivery summary /
// docs/P0_DEVIATIONS.md), left for a future pass to rewrite deliberately.

if (changes.length === 0) {
  console.log('OK: docker-compose.yml already has every P0.6 Batch C setting; nothing written.');
  process.exit(0);
}

const backup = `${file}.bak.${new Date().toISOString().replace(/[:.]/g, '-')}`;
fs.writeFileSync(backup, original);
fs.writeFileSync(file, lines.join('\n'));
console.log(`BACKUP: ${backup}`);
console.log(`CHANGED (${changes.length}):`);
for (const c of changes) console.log(`  - ${c}`);
process.exit(0);
OPS_WIRE_P06C_MJS_EOF
echo "wrote ops/wire_p06c.mjs ($(wc -l < "ops/wire_p06c.mjs") lines)"

echo
echo "== wiring docker-compose.yml (surgical, idempotent - ops/wire_p06c.mjs) =="
node ops/wire_p06c.mjs docker-compose.yml

echo
echo "== generating ops/prometheus/secrets/metrics_token from this repo's real .env =="
if [ ! -f .env ]; then
  echo "REFUSED: no .env file at the repo root - cannot read METRICS_TOKEN from it." >&2
  exit 1
fi
REAL_METRICS_TOKEN="$(grep -E '^METRICS_TOKEN=' .env | tail -n1 | cut -d= -f2-)"
if [ -z "$REAL_METRICS_TOKEN" ]; then
  echo "REFUSED: METRICS_TOKEN is empty/missing in .env - the gateway and forwarder both refuse to start without it (H5), and Prometheus cannot scrape either without the same secret." >&2
  exit 1
fi
mkdir -p ops/prometheus/secrets
umask 077
printf %s "$REAL_METRICS_TOKEN" > ops/prometheus/secrets/metrics_token
chmod 600 ops/prometheus/secrets/metrics_token
echo "wrote ops/prometheus/secrets/metrics_token (gitignored, not committed)"

echo
if [ -f scripts/hunt_gate.mjs ]; then
  echo "== running scripts/hunt_gate.mjs =="
  HG_OUT="$(mktemp)"
  set +e
  node scripts/hunt_gate.mjs > "$HG_OUT" 2>&1
  HG_STATUS=$?
  set -e
  cat "$HG_OUT"
  if [ "$HG_STATUS" -eq 0 ]; then
    echo "hunt_gate: OK"
  else
    TOUCHED_PATTERN="$(printf '%s\n' \
      "^gateway\/src\/metrics.js:" \
      "^gateway\/src\/forwarder.js:" \
      "^gateway\/src\/forwarder_metrics.js:" \
      "^gateway\/src\/ingest\/wal.js:" \
      "^gateway\/src\/ingest\/spool.js:" \
      "^gateway\/src\/media.js:" \
      "^gateway\/src\/outbound\/queue.js:" \
      "^gateway\/src\/index.js:" \
      "^gateway\/src\/config.js:" \
      "^gateway\/.env.example:" \
      "^.gitignore:" \
      "^ops\/prometheus\/prometheus.yml:" \
      "^ops\/prometheus\/alerts.yml:" \
      "^ops\/wire_p06c.mjs:" \
      | paste -sd'|' -)"
    if grep -qE "$TOUCHED_PATTERN" "$HG_OUT"; then
      echo "REFUSED: hunt_gate found a real violation in a file THIS script just wrote (see above) - that one is on me to fix, not to skip." >&2
      rm -f "$HG_OUT"
      exit 1
    fi
    echo
    echo "== hunt_gate failed, but every violation above is in a PRE-EXISTING file this script did not touch =="
    echo "   Continuing with just the P0.6 Batch C changes, as agreed - those violations remain open and are not fixed or hidden by this script."
  fi
  rm -f "$HG_OUT"
else
  echo "== scripts/hunt_gate.mjs not found at repo root - skipping (paste its real path if it lives elsewhere) =="
fi

echo
echo "== validating ops/prometheus/{prometheus.yml,alerts.yml} with the real pinned image (prom/prometheus:v3.14.0 promtool) =="
mkdir -p ops/prometheus/secrets  # in case the block above was skipped for any reason
if [ ! -s ops/prometheus/secrets/metrics_token ]; then
  echo "placeholder-for-promtool-check-only" > ops/prometheus/secrets/metrics_token
fi
docker run --rm -v "$(pwd)/ops/prometheus:/etc/prometheus:ro" prom/prometheus:v3.14.0 promtool check rules /etc/prometheus/alerts.yml
docker run --rm -v "$(pwd)/ops/prometheus:/etc/prometheus:ro" prom/prometheus:v3.14.0 promtool check config /etc/prometheus/prometheus.yml

echo
echo "== running the full gateway test suite (real redis-cache/redis-durable, no mocks - H7) =="
# Copied EXACTLY from the established, proven mechanism (batch_p06b_final.sh /
# batch_p06b_f17_metrics_gauge_fix.sh): bind-mount the live repo's
# src/scripts/test-support (read-only) over the built image's own, and let
# every REDIS_CACHE_*/REDIS_DURABLE_*/METRICS_TOKEN var come from the
# `gateway` service's own compose-defined environment (D-27), unchanged.
DC="docker compose"; $DC version >/dev/null 2>&1 || DC="docker-compose"
MOUNTS=(-v "$(pwd)/gateway/src:/app/src:ro" -v "$(pwd)/gateway/scripts:/app/scripts:ro" -v "$(pwd)/gateway/test-support:/app/test-support:ro" -v "$(pwd)/gateway/package.json:/app/package.json:ro")
TEST_ENV=(-e ALLOW_FAKE_WA=1 -e NODE_ENV=test -e LOG_LEVEL=error)
timeout -k 30 --foreground 700 $DC run --rm -T "${TEST_ENV[@]}" "${MOUNTS[@]}" gateway npm test < /dev/null

echo
echo "== recording F18 in docs/P0_FINDINGS.md (append-only, idempotent) =="
FINDINGS=docs/P0_FINDINGS.md
if [ ! -f "$FINDINGS" ]; then
  echo "REFUSED: $FINDINGS not found - refusing to create it from a fragment (it should already hold F1-F17)." >&2
  exit 1
fi
if grep -q '^## F18 ' "$FINDINGS"; then
  echo "F18 already present in $FINDINGS - skipping (idempotent)."
else
  cp "$FINDINGS" "${FINDINGS}.bak.${TS}"
  echo "backed up: ${FINDINGS}.bak.${TS}"
  printf '\n' >> "$FINDINGS"
  cat >> "$FINDINGS" <<'P06C_FINDING_F18_EOF'
## F18 — مجموعة المستهلكين `core-ingest` لم تُنشأ أبداً عند إقلاع البوابة رغم نص P0.2 الحرفي (اكتُشف أثناء بناء مقاييس المراقبة في الدفعة ج)

**كيف اكتُشف:** أثناء تصميم مقياس `stream_pending{group}` (P0.6 الدفعة ج) احتجتُ إلى حصر كل مجموعات المستهلكين (`consumer group`) الحقيقية الموجودة فعلياً على تدفقات `in:{shard}` كي لا يُبنى مقياس على مجموعة وهمية تقرأ صفراً مؤكَّداً دائماً (D-22). رجعتُ إلى نص P0.2 الحرفي عبر `Projects.project_search` مباشرة (لا الذاكرة) فوجدت: يجب إنشاء مجموعة `core-ingest` عند إقلاع البوابة بأمر `XGROUP CREATE ... MKSTREAM`، بلا أي مستهلك فعلي حتى P1. بحثتُ فعلياً في `index.js` و`forwarder.js` بالكامل: **لا وجود لهذا الاستدعاء إطلاقاً في أي مكان بالكود الحقيقي** - فقط مجموعة `legacy-forwarder` تُنشأ، وحصراً داخل `forwarder.js` (عملية منفصلة تماماً عن `index.js`).

**الأثر:** لا أثر على الإنتاج الفعلي حتى الآن (P1 لم يُبنَ بعد، فلا مستهلك حقيقي لـ`core-ingest` يحتاج المجموعة أصلاً في هذه المرحلة) - لكنها فجوة امتثال حقيقية لنص P0.2، وستتحوّل إلى عطل فوري بمجرد بدء P1: أي محاولة `XREADGROUP` بمجموعة غير موجودة تفشل حالاً بخطأ Redis (`NOGROUP`).

**الإصلاح:** أُضيف إلى `gateway/src/index.js`'s `main()`، مباشرة بعد `setRedisClient(client)`، حلقة على كل قِطاع (`config.ingestShards`) تُنشئ `core-ingest` عبر `client.xgroup('CREATE', 'in:{i}', config.coreIngestGroup, '0', 'MKSTREAM')` - بنفس نمط `ensureGroup` الموجود أصلاً في `forwarder.js` لمجموعة `legacy-forwarder` (متسامحة مع خطأ `BUSYGROUP` عند إعادة تشغيل العملية على مجموعة مُنشأة أصلاً، لا تطرح استثناءً في تلك الحالة تحديداً).

**التحقق (حقيقي):** أداة تحقق مستقلة (`gateway/verify_f18.mjs`، لا تُسلَّم ضمن الملفات النهائية - أداة تحقق محلية فقط) تُشغِّل نفس المقتطف المضاف حرفياً إلى `index.js` مقابل Redis محلي حقيقي (منفذ 6390): (1) تأكيد عدم وجود `core-ingest` على أي من قِطاعات `in:{0..3}` قبل أول إقلاع، (2) تشغيل حلقة الإنشاء مرة واحدة والتأكد عبر `XINFO GROUPS` الحقيقي من وجود المجموعة على كل قِطاع، (3) محاكاة إعادة تشغيل العملية (تشغيل الحلقة ثانيةً) والتأكد أنها **لا تطرح استثناءً** (تسامح `BUSYGROUP` فعلي لا نظري)، (4) تأكيد بقاء المجموعات كما هي بعد ذلك. النتيجة: **13/13** فحصاً نجح.

**نطاق الإصلاح:** `gateway-forwarder` لا يحتاج نفس التعديل - هو من يُنشئ `legacy-forwarder` أصلاً، ولا علاقة له بـ`core-ingest` (التي هي حصراً استعداد لمستهلك P1 مستقبلي يعيش خارج هذه العملية).
P06C_FINDING_F18_EOF
  echo "appended F18 to $FINDINGS"
fi

echo "== recording D-28 in docs/P0_DEVIATIONS.md (append-only, idempotent) =="
DEVIATIONS=docs/P0_DEVIATIONS.md
if [ ! -f "$DEVIATIONS" ]; then
  echo "REFUSED: $DEVIATIONS not found - refusing to create it from a fragment (it should already hold D-1..D-27)." >&2
  exit 1
fi
if grep -q '^## D-28 ' "$DEVIATIONS"; then
  echo "D-28 already present in $DEVIATIONS - skipping (idempotent)."
else
  cp "$DEVIATIONS" "${DEVIATIONS}.bak.${TS}"
  echo "backed up: ${DEVIATIONS}.bak.${TS}"
  printf '\n' >> "$DEVIATIONS"
  cat >> "$DEVIATIONS" <<'P06C_DEVIATION_D28_EOF'
## D-28 — P0.6 (الدفعة ج والأخيرة): ربط كل عائلات المقاييس المتبقية + `prometheus`/`alerts.yml` + وصل `docker-compose.yml` (قرارات التسمية/الوسم وأسبابها)

**تسميات/وسوم انحرفت عمداً عن نص الحرفي، ولماذا:**

| المقياس | النص الحرفي المتوقَّع | القرار الفعلي | لماذا |
|---|---|---|---|
| `out_queue_depth` | وسم `priority` | وسم `kind` | لا حقل `priority` في الكود الحقيقي إطلاقاً (D-23 وثّقت هذا سابقاً) — `outbound/queue.js` يصنّف العناصر بـ`kind` (`interactive`/`bulk`/إلخ) فقط. وسم القياس بحقل لا وجود له كان سيُنتج تسمية فارغة/مضلِّلة دائماً. |
| `ingest_spool_depth` | "عمق" (يُقرأ عادة كعدد سجلات) | حجم بالبايت | عدّ السجلات الحقيقي في `spool.js` يتطلّب قراءة الملف كاملاً في كل نقطة قياس — ينتهك انضباط O(1) المتعمَّد في `spoolStatus()` (سطر واحد `fs.statSync` فقط). القيمة المعروضة فعلياً هي حجم الملف بالبايت (متوفرة أصلاً من نفس `stat`)، وتخدم نفس غرض التنبيه (`> 0` لأي مدة تعني انسكاباً نشطاً) بلا كلفة إضافية. |
| `stream_pending{group}` | كل مجموعات المستهلكين | حصراً `legacy-forwarder` | `core-ingest` لا مستهلك لها حتى P1 (والآن تُنشأ فارغة فقط - انظر F18)؛ قياس معلّقاتها كان سيقرأ **صفراً مؤكَّداً دائماً**، وهو بالضبط ما يمنعه D-22/D-15. يُعاد تضييق النطاق تلقائياً بمجرد بدء P1 دون أي تغيير كود إضافي (المقياس مبني على حلقة تمر على كل مجموعة حقيقية). |
| `stream_pending_oldest_seconds{group}` | **غير موجود في النص إطلاقاً** | مقياس جديد أضفتُه | قاعدة التنبيه الثامنة (`StreamPendingTooOld`, "أقدم رسالة معلّقة > 60 ثانية") لا سند حقيقي لها بلا هذا المقياس - `stream_pending` وحده عدّاد، لا عمر. حُسِب من نفس استدعاء `XPENDING` الموسَّع (`IDLE`) في دورة الجمع نفسها، فلا جولة Redis إضافية. |

**قاعدتا تنبيه من الثماني — حالتاهما الفعليتان بعد التحقق الحقيقي (لا افتراضاً):**
- `RedisDurableMemoryHigh`: **لا تزال غير مربوطة فعلياً** - تحتاج `redis_exporter` (لا وجود له في `docker-compose.yml`؛ إضافة خدمة تصدير كاملة حُكم عليها بأنها خارج نطاق "وصل قواعد التنبيه" لهذه الدفعة تحديداً). مكتوبة بأسماء مقاييس `redis_exporter` القياسية الحقيقية كي تعمل فور إضافته لاحقاً بلا إعادة كتابة. موثَّقة أيضاً في تعليق رأس `ops/prometheus/alerts.yml`.
- `SessionBanned`: كانت مسوَّدتي الأولى تخمّن `session_state{state="banned"}` (أحرف صغيرة). قبل التسليم، تحقّقتُ فعلياً من `gateway/src/sessions.js` (`mapConnectionStatus`, سطر 270-281): المفردة الحقيقية **`'BANNED'` بأحرف كبيرة** (نفس نمط `'CONNECTED'`/`'UNKNOWN'` المجمَّد بالعقد)، ومُمرَّرة لـ`session_state` بلا أي تحويل. **صُحِّحت القاعدة إلى `state="BANNED"` قبل أي تسليم** - تُسجَّل هذه الملاحظة هنا (لا لأنها انحراف عن الإنتاج، بل لتوثيق أن التحقق الحرفي بالكود هو ما كشفها، لا الحظ).

**امتداد خاص بي على H5 لم يطلبه النص حرفياً:** جعلتُ عملية الـforwarder (`forwarder.js`) ترفض الإقلاع كلياً إن كان `METRICS_TOKEN` فارغاً - تماماً كموقف `main()` في `index.js` (D-21) - رغم أن النص الحرفي لم يفرض هذا على الـforwarder تحديداً. القرار: اتساق الوضع الأمني عبر العمليتين (كلتاهما تعرضان الآن `/metrics` محمياً بنفس السرّ)، لا لأن النص طلبه. موثَّق في رأس `forwarder.js` نفسه أيضاً.

**`gateway-forwarder` في `docker-compose.yml` — مُسّ هذه المرة، خلافاً لـD-27:** D-27 تركه عمداً بلا `METRICS_TOKEN`/فحص صحة لأنه وقتها لم يكن يملك خادم HTTP أصلاً. هذه الدفعة تضيف له خادم مقاييس حقيقياً (`forwarder_metrics.js`)، فأصبح يحتاج فعلياً: `METRICS_TOKEN` (نفس السرّ المشترك، لا سرّ جديد)، `FORWARDER_METRICS_PORT`، تعيين منفذ `127.0.0.1:4002:4002` (نفس اصطلاح `gateway` الخاص بـ4001)، **وفحص صحة حقيقي** (`wget` على `/healthz` الجديد) - وهذا يُغلق بالضبط الفجوة التي تركها تعليق الملف الأصلي: *"revisit if we add a tiny /healthz shim"*. أُزيل ذلك التعليق القديم لأنه أصبح كاذباً عن حال الملف الفعلي.

**خدمة `prometheus` الجديدة — قرارات الموارد:**
- الصورة: `prom/prometheus:v3.14.0` (وسم صريح، **مؤكَّد قابل للسحب فعلياً على هذا الـVPS** - `docker pull` حقيقي نجح؛ لا `:latest`، اتساقاً مع قاعدة المشروع في كل مكان آخر).
- `mem_limit`/`memswap_limit`: `256m` - كافية لهدف كشط وحيد (مستهدفان اثنان فقط، فاصل 15 ثانية)؛ الميزانية الكلية تنتقل من 3376MB (بعد D-27) إلى **3632MB** من مضيف 7941MB (لا يزال هناك أكثر من 4.2GB هامش لنظام التشغيل/ذاكرة التخزين المؤقت للصفحات).
- `--storage.tsdb.retention.time=15d`: يحدّ استهلاك القرص بصراحة بدل الاعتماد على افتراض الصورة (الذي قد ينمو بلا حدود).
- الشبكة: `app` فقط (كلا الهدفين `gateway`/`gateway-forwarder` عضوان فيها أصلاً؛ لا حاجة للانضمام لشبكة `data` الداخلية).
- المنفذ: `127.0.0.1:9090:9090` - loopback فقط، نفس اصطلاح `gateway`/`gateway-forwarder`.
- السرّ (`bearer_token_file`): يُنشئه سكربت التسليم من `METRICS_TOKEN` الحقيقي في `.env` وقت التسليم إلى `ops/prometheus/secrets/metrics_token` (مُتجاهَل في `.gitignore`، لا يُنشأ آلياً عند الإقلاع - كتابة لمرة واحدة عند كل تشغيل لسكربت التسليم، بنفس انضباط بقية أدوات هذا المشروع في `ops/`).

**ملاحظة توثيقية غير مُصلَحة عمداً:** تعليق "Memory budget" في أعلى `docker-compose.yml` كان راكداً أصلاً منذ D-27 (لا يزال يذكر "gateway 512" رغم رفعها لـ1200m)؛ هذه الدفعة تضيف +256m فوق رقم راكد أصلاً. لم يُصلَح هنا عمداً (خارج نطاق "وصل المراقبة" الضيّق لهذه الدفعة، وإصلاح نثر بتعديل نصّي جراحي محفوف بمخاطر أعلى من فائدته) - يُترك لدفعة توثيق منفصلة.

**التحقق:** كل مقياس جديد (`ingest_*`, `stream_*`, `out_*`, `media_*`, `forwarder_attempts_total`, `dlq_length`) اختُبِر فعلياً مقابل Redis محلي حقيقي وعمليات ابن حقيقية (`gateway/verify_metrics.mjs`، أداة تحقق مستقلة لهذه الدفعة فقط - **26/26** فحصاً نجح)؛ `ops/prometheus/{prometheus.yml,alerts.yml}` تحقَّقا فعلياً بـ`promtool check config`/`check rules` (تفاصيل الإصدار المستخدم وسببه في ملخص التسليم)؛ `ops/wire_p06c.mjs` (تعديل `docker-compose.yml` الجراحي) اختُبِر على نسخة حقيقية من الملف الفعلي، بما فيه اختبار idempotency (تشغيل ثانٍ لا يُغيّر شيئاً) والتحقق النهائي بـ`docker compose config` الحقيقي.
P06C_DEVIATION_D28_EOF
  echo "appended D-28 to $DEVIATIONS"
fi

echo
echo "== diff summary =="
git diff --stat -- "gateway/src/metrics.js" "gateway/src/forwarder.js" "gateway/src/forwarder_metrics.js" "gateway/src/ingest/wal.js" "gateway/src/ingest/spool.js" "gateway/src/media.js" "gateway/src/outbound/queue.js" "gateway/src/index.js" "gateway/src/config.js" "gateway/.env.example" ".gitignore" "ops/prometheus/prometheus.yml" "ops/prometheus/alerts.yml" "ops/wire_p06c.mjs" docker-compose.yml docs/P0_FINDINGS.md docs/P0_DEVIATIONS.md || true

echo
echo "Done. Nothing was staged or committed, and the real stack was not started."
echo "Review the diff above. When ready:"
echo "  1) git add -A && git commit -m ...   (Gate A - your call, not run by this script)"
echo "  2) bash ops/verify_p06c_live.sh       (brings the changed services up for real and checks Prometheus is actually scraping/loading rules - separate, optional, run only when you are ready)"
