// gateway/src/lease.js
//
// P0.5 (G7, disaster #16): per-session lease + fencing on redis-durable, so
// exactly one gateway instance ever holds a real Baileys socket + writes
// `creds.json` for a given session, even when >1 instance is running (a
// rolling deploy, or two replicas both scanning the same shared
// `auth_sessions` volume).
//
// Spec (literal, prompts/P0_DEEPSEEK_PROMPT.md P0.5 §3): `lease:{sid}` =
// `{instance_id}:{fencing_token}` (fencing token from `INCR fence:{sid}`),
// acquired with `SET NX PX 30000` and renewed every 10s via a comparing Lua
// script; `creds` writes verify lease ownership first; losing the lease means
// closing the socket immediately WITHOUT logout() and stopping sends.
//
// All mutating operations here are single Lua EVALs (H3/H4: no
// read-modify-write race across two round-trips) - same pattern already
// established in outbound/queue.js and outbound/tokenBucket.js.

function leaseKey(sessionId) { return `lease:${sessionId}`; }
function fenceKey(sessionId) { return `fence:${sessionId}`; }

function leaseValue(instanceId, fencingToken) {
  return `${instanceId}:${fencingToken}`;
}

/**
 * Try to acquire the lease for a session. A fresh fencing token is drawn
 * (INCR) on every attempt, whether or not the SET NX below succeeds - gaps in
 * the token sequence are fine (H4: it only needs to be monotonically
 * increasing, never reused), and drawing it first (rather than only on
 * success) keeps this a single extra O(1) call with no added race.
 *
 * @param {import('ioredis').Redis} client
 * @param {string} sessionId
 * @param {string} instanceId
 * @param {number} ttlMs
 * @returns {Promise<{ acquired: boolean, fencingToken?: number, value?: string }>}
 */
export async function acquireLease(client, sessionId, instanceId, ttlMs) {
  const fencingToken = await client.incr(fenceKey(sessionId));
  const value = leaseValue(instanceId, fencingToken);
  const ok = await client.set(leaseKey(sessionId), value, 'PX', ttlMs, 'NX');
  if (ok === 'OK') {
    return { acquired: true, fencingToken, value };
  }
  return { acquired: false };
}

// KEYS[1] = lease:{sid}, ARGV[1] = our own "{instance_id}:{fencing_token}",
// ARGV[2] = new PX (ms). Only extends the TTL when we still own the key -
// this is exactly what stops a gateway instance that has already lost the
// lease (another instance's SET NX won the race after our TTL expired) from
// clobbering the new holder's lease by blindly re-extending it.
const RENEW_SCRIPT = `
local cur = redis.call('GET', KEYS[1])
if cur == ARGV[1] then
  redis.call('PEXPIRE', KEYS[1], ARGV[2])
  return 1
else
  return 0
end
`;

/**
 * Renew a lease this instance believes it holds. Returns false the moment
 * that belief is wrong (lease expired and was re-acquired elsewhere, or
 * never held) - the caller (sessions.js) treats false as "lease lost" and
 * closes the socket immediately without logout (spec, literal).
 *
 * @returns {Promise<boolean>}
 */
export async function renewLease(client, sessionId, instanceId, fencingToken, ttlMs) {
  const value = leaseValue(instanceId, fencingToken);
  const res = await client.eval(RENEW_SCRIPT, 1, leaseKey(sessionId), value, String(ttlMs));
  return res === 1;
}

// Same compare-then-act shape as RENEW_SCRIPT, but deletes instead of
// extending - used for a graceful release (clean shutdown, explicit logout)
// so the NEXT holder does not have to wait out the full TTL.
const RELEASE_SCRIPT = `
local cur = redis.call('GET', KEYS[1])
if cur == ARGV[1] then
  redis.call('DEL', KEYS[1])
  return 1
else
  return 0
end
`;

/** @returns {Promise<boolean>} true if we owned it and released it; false if we no longer owned it (nothing to do). */
export async function releaseLease(client, sessionId, instanceId, fencingToken) {
  const value = leaseValue(instanceId, fencingToken);
  const res = await client.eval(RELEASE_SCRIPT, 1, leaseKey(sessionId), value);
  return res === 1;
}

/**
 * Read-only ownership check (spec: "كتابة creds تتحقق من ملكية الـlease" -
 * a creds write verifies lease ownership first). A plain GET, not a Lua
 * script: this only gates whether saveCreds proceeds: a check-then-write race
 * against a lease handover happening in the same few milliseconds is an
 * accepted, documented residual risk (the renew loop's own failure already
 * closes the socket promptly on loss - this is defense in depth on top of
 * that, not the only guard).
 *
 * @returns {Promise<boolean>}
 */
export async function isLeaseOwner(client, sessionId, instanceId, fencingToken) {
  const cur = await client.get(leaseKey(sessionId));
  return cur === leaseValue(instanceId, fencingToken);
}

/** @returns {Promise<string|null>} the raw `{instance_id}:{fencing_token}` value, or null if unheld - surfaced verbatim as `lease_holder` on GET /sessions/:id/health. */
export async function getLeaseHolder(client, sessionId) {
  return client.get(leaseKey(sessionId));
}

export const _keys = { leaseKey, fenceKey, leaseValue };
