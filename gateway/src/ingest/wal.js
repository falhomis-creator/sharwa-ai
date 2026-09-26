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
