// gateway/src/ingest/dedupe.js
// Owner decision #1: the dedupe marker and the XADD are fused into ONE atomic,
// fsync'd write on redis-durable (AOF fsync=always) via a Lua script. Never on
// redis-cache — that tier is lossy (allkeys-lru) and may evict a marker, which
// would let a duplicate through exactly when it matters.
//
// Claim-then-append order (owner-specified): the `SET … NX PX` reserves the
// message id first, and XADD runs only when that claim succeeds, both inside one
// EVAL. Because Redis does not roll back a Lua script on error, a plain
// "claim then XADD" would leave the marker set if XADD failed — so the retry
// would see a phantom duplicate and drop the message (F2 violation). The `pcall`
// around XADD closes that window: on failure it releases the claim (DEL) and
// returns sentinel {2, err}, so the caller spools and a later retry re-claims
// cleanly.

export const FUSED_APPEND_LUA = `
-- KEYS[1] = dedupe key  dedupe:{session_id}:{provider_message_id}
-- KEYS[2] = stream key  in:{shard}
-- ARGV[1] = entry id    ("*" or explicit)
-- ARGV[2] = field name  ("data")
-- ARGV[3] = entry value (serialized JSON)
-- ARGV[4] = pending marker TTL (ms)
-- ARGV[5] = streamMaxLen (applied as MAXLEN ~)
local claimed = redis.call('SET', KEYS[1], 'pending', 'PX', ARGV[4], 'NX')
if not claimed then
  return {0}
end
local id = redis.pcall('XADD', KEYS[2], 'MAXLEN', '~', ARGV[5], ARGV[1], ARGV[2], ARGV[3])
if type(id) == 'table' then
  redis.call('DEL', KEYS[1])
  return {2, tostring(id.err or 'xadd_failed')}
end
return {1, id}
`;

/**
 * Append an entry to a stream iff its dedupe key is not already taken, in one
 * atomic EVAL. Three outcomes:
 *
 *  - `{ status: 'duplicate' }` — key already existed; nothing written (success).
 *  - `{ status: 'appended', id }` — claimed and appended.
 *  - throws — the claim was released and XADD failed; the caller MUST spool and
 *    retry (H3: never report success, never swallow).
 *
 * @param {import('ioredis').Redis} client
 * @param {object} opts
 * @param {string} opts.dedupeKey
 * @param {string} opts.streamKey
 * @param {string} opts.entryId        Usually "*".
 * @param {string} opts.entry          Serialized entry value.
 * @param {number} opts.pendingTtlMs   Short dedupe window while in flight.
 * @param {number} [opts.streamMaxLen] Approximate MAXLEN backstop.
 * @returns {Promise<{ status: 'appended'|'duplicate', id?: string }>}
 */
export async function fusedAppend(client, opts) {
  const res = await client.eval(
    FUSED_APPEND_LUA,
    2,
    opts.dedupeKey,
    opts.streamKey,
    opts.entryId ?? '*',
    'data',
    opts.entry,
    String(opts.pendingTtlMs),
    String(opts.streamMaxLen ?? 100000),
  );

  const flag = Number(res?.[0]);
  if (flag === 0) return { status: 'duplicate' };
  if (flag === 1) return { status: 'appended', id: String(res[1]) };

  // flag 2 (or any unexpected sentinel): the Lua released the claim and the
  // message is NOT in the stream. Surface a hard error so the caller spools.
  const err = new Error(
    `dedupe: XADD failed for ${opts.streamKey}: ${String(res?.[1] ?? 'unknown')}`,
  );
  err.code = 'DEDUPE_XADD_FAILED';
  throw err;
}

/**
 * Mark an already-appended message as successfully forwarded: extend its dedupe
 * marker to the long "done" TTL so late redeliveries are still caught (G2).
 *
 * @param {import('ioredis').Redis} client
 * @param {string} dedupeKey
 * @param {number} doneTtlMs
 */
export async function markDone(client, dedupeKey, doneTtlMs) {
  // Only extend if the marker still exists; a fresh SET would resurrect a
  // correctly-expired marker and wrongly block a legitimate re-delivery.
  const exists = await client.exists(dedupeKey);
  if (exists) {
    await client.psetex(dedupeKey, doneTtlMs, 'done');
  }
}
