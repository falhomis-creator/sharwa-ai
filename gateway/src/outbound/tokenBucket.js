// gateway/src/outbound/tokenBucket.js
//
// Per-number, per-kind token bucket (P0.4 §"التباطؤ وToken Bucket"), atomic
// via a single Lua EVAL (H3/H4: no read-modify-write race across two
// commands — two concurrent workers checking the same number's bucket must
// never both see "1 token left" and both consume it). Two independent
// buckets exist per phone number: `service` (high capacity/refill — normal
// replies) and `marketing` (low, rate-limited, plus a separate daily cap).
//
// This is the LAST line of defense independent of the P3 "Send Policy"
// engine (which does not exist yet — out of scope for P0, see the spec's own
// note). Rejection here means "wait and retry", never "drop" — the caller
// (outbound/queue.js) re-queues the item rather than discarding it.

const TOKEN_BUCKET_LUA = `
-- KEYS[1] = bucket hash key   bucket:{kind}:{number}
-- ARGV[1] = capacity
-- ARGV[2] = refill_per_min
-- ARGV[3] = now_ms
-- ARGV[4] = key ttl seconds (bounds idle-number memory, H4)
local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
local capacity = tonumber(ARGV[1])
local now = tonumber(ARGV[3])
if tokens == nil then
  tokens = capacity
  ts = now
end
local elapsed = now - ts
if elapsed > 0 then
  local refill = elapsed * tonumber(ARGV[2]) / 60000.0
  tokens = tokens + refill
  if tokens > capacity then tokens = capacity end
end
local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
end
redis.call('HMSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('EXPIRE', KEYS[1], ARGV[4])
return {allowed, tostring(tokens)}
`;

/**
 * Attempt to consume one token from a number's bucket for the given kind.
 *
 * @param {import('ioredis').Redis} client
 * @param {object} opts
 * @param {string} opts.number     E.164-ish destination number/JID (bucket key scope).
 * @param {'service'|'marketing'} opts.kind
 * @param {number} opts.capacity
 * @param {number} opts.refillPerMin
 * @param {number} [opts.now]      Injectable clock for deterministic tests.
 * @returns {Promise<{ allowed: boolean, tokensRemaining: number }>}
 */
export async function tryConsumeToken(client, opts) {
  const now = opts.now ?? Date.now();
  const key = `bucket:${opts.kind}:${opts.number}`;
  // Idle-bucket TTL: generous relative to the refill window so a burst
  // straddling the expiry doesn't reset unfairly, but still bounded (H4) —
  // an hour past capacity/refill-rate is always enough to be "long idle".
  const ttlS = Math.max(3600, Math.ceil((opts.capacity / Math.max(opts.refillPerMin, 1)) * 60) + 3600);
  const res = await client.eval(
    TOKEN_BUCKET_LUA, 1, key,
    String(opts.capacity), String(opts.refillPerMin), String(now), String(ttlS),
  );
  return { allowed: Number(res[0]) === 1, tokensRemaining: Number(res[1]) };
}

/**
 * Marketing-only daily cap, independent of the per-minute bucket above
 * (spec: "سقف يومي MARKETING_DAILY_CAP"). A simple atomic INCR bounded by a
 * 25h TTL (H4) so the counter always self-clears even across a slow day
 * boundary, keyed per calendar day (UTC) per number.
 *
 * @param {import('ioredis').Redis} client
 * @param {object} opts
 * @param {string} opts.number
 * @param {number} opts.dailyCap
 * @param {number} [opts.now]
 * @returns {Promise<{ allowed: boolean, countToday: number }>}
 */
export async function tryConsumeMarketingDailyCap(client, opts) {
  const now = opts.now ?? Date.now();
  const day = new Date(now).toISOString().slice(0, 10);
  const key = `bucket:marketing:daily:${opts.number}:${day}`;
  const count = await client.incr(key);
  if (count === 1) {
    await client.expire(key, 25 * 3600);
  }
  if (count > opts.dailyCap) {
    // Do not let a rejected send count against the cap forever — undo the
    // increment so a subsequent legitimate day-boundary reset stays exact.
    await client.decr(key);
    return { allowed: false, countToday: opts.dailyCap };
  }
  return { allowed: true, countToday: count };
}
