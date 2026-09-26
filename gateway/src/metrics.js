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
