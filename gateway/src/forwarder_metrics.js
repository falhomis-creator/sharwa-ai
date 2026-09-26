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
