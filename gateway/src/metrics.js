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
// Deliberately NOT defined here yet: ingest_*, out_*, forwarder_*, dlq_length,
// stream_*, media_*, killswitch_* - the spec's full fixed-name list. A metric
// registered but never incremented reads as a confirmed "0" to whoever is
// watching it (an ops dashboard, an alert rule) when the true answer is
// "not wired" - that is exactly the kind of fabricated-looking data H1
// forbids, just wearing a metrics hat instead of a report hat. Each of those
// families is added in the same change that wires its real increment point
// (kill-switch metrics land with Batch B's real enforcement path;
// ingest/outbound/forwarder/media metrics land with Batch C's wiring pass
// across wal.js/queue.js/forwarder.js/media.js) - never speculatively ahead
// of that.

import client from 'prom-client';

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
