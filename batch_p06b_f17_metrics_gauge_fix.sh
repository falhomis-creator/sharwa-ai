#!/usr/bin/env bash
# ops/batch_p06b_f17_metrics_gauge_fix.sh
#
# Fixes ONE real bug (F17), found by actually running the P0.6 Batch B live
# verification against the real, already-deployed gateway container and its
# real redis-cache:
#
#   killswitch_state{scope,capability} is a Prometheus Gauge. killswitch.js's
#   fetchScope() only ever calls .set() for capabilities CURRENTLY present in
#   the redis-cache hash. If an operator removes a capability (HDEL - turning
#   a restriction back off), the gauge for that (scope,capability) label is
#   simply never touched again, so it keeps reporting the OLD severity
#   forever - even though the internal cache (and therefore
#   checkCapability()/checkSend(), which only ever read that cache) already
#   reverted correctly. Enforcement was never wrong; only the exposed metric
#   was stale and misleading to anyone reading /metrics or a future alert
#   rule.
#
# Observed for real: after a live ks:global.ai_reply off->restore round-trip
# on the VPS, `killswitch_state{scope="global",capability="ai_reply"}`
# stayed at 2 (off) even after the field was HDEL'd and PUBLISHed, while
# checkCapability('ai_reply', {}) itself correctly returned allowed:true.
#
# The fix: fetchScope() now diffs the previous cache entry against the fresh
# HGETALL result and explicitly resets the gauge to severity 0 for any
# capability that disappeared. Proven locally against a REAL local Redis
# (not mocked - H7): a new test in killswitch.test.js was first confirmed to
# FAIL against the unfixed code (2 !== 0), then confirmed to PASS once the
# fix was applied - so this is a real regression test, not a tautology.
#
# What this script does, and nothing else:
#   1. backs up both touched files with a timestamp
#   2. writes the fixed killswitch.js and the updated killswitch.test.js
#      (one new test added; nothing else in either file changed)
#   3. runs scripts/hunt_gate.mjs if present (constitution static checks)
#   4. runs the FULL gateway test suite for real, against the real
#      redis-cache/redis-durable this repo is already wired to
#   5. prints a diff --stat summary
#
# It does NOT run git add/commit. Gate A (the still-open decision on
# committing the 15 P0.6 Batch B files, now 16 after this) remains yours,
# exactly as batch_p06b_final.sh left it.
#
# Usage: bash ops/batch_p06b_f17_metrics_gauge_fix.sh   (run from the repo root)

set -euo pipefail

if [ ! -f docker-compose.yml ] || [ ! -d gateway ]; then
  echo "REFUSED: this doesn't look like the repo root (no docker-compose.yml / gateway/ here)." >&2
  echo "Run from the repo root, e.g. /home/sharwa/sharwa_ai" >&2
  exit 1
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
KS_JS=gateway/src/killswitch.js
KS_TEST=gateway/src/__tests__/killswitch.test.js

for f in "$KS_JS" "$KS_TEST"; do
  if [ ! -f "$f" ]; then
    echo "REFUSED: expected file not found: $f" >&2
    echo "This script only patches the exact P0.6 Batch B files already delivered by batch_p06b_final.sh; if the path moved, tell me rather than let me guess." >&2
    exit 1
  fi
  cp "$f" "${f}.bak.${TS}"
  echo "backed up: ${f}.bak.${TS}"
done

echo
echo "== writing the fixed killswitch.js =="
cat > "$KS_JS" <<'GATEWAY_KILLSWITCH_JS_EOF'
// gateway/src/killswitch.js
//
// P0.6 Batch B (kill-switch, spec literal — prompts/P0_DEEPSEEK_PROMPT.md,
// architecture/02_RISK_SOLUTIONS_21.md, re-verified via Projects.project_search
// before writing this file, not recalled from memory): the gateway-side
// enforcement half of the kill-switch. The source of truth is a Postgres
// `kill_switches` table written only by core/api (P0.7 scope, not built yet).
// This module only ever reads the FAST COPY on `redis-cache`:
//
//   ks:global                 HASH  field=capability, value='on'|'degraded'|'off'
//   ks:tenant:{tenant_id}     HASH  same shape, tenant scope
//   ks:channel:{channel_id}   HASH  same shape, channel scope
//   ks:changes                PUBSUB  change-notification channel
//
// Effective state = the STRICTEST among (global, tenant, channel) scopes,
// each scope itself the strictest of (the capability's own key, the '*'
// wildcard key) — severity order 'on' < 'degraded' < 'off' (spec, literal:
// matches `app.effective_switch` semantics). A scope/capability with no
// entry defaults to 'on' (unrestricted) — this is also the real, expected,
// spec-documented fallback for every session in THIS codebase today, since
// no session anywhere yet carries tenant_id/channel_account_id
// (architecture/02_RISK_SOLUTIONS_21.md, its own words: "الكود الحالي: لا
// شيء ينطبق بعد - البوابة لا تعرف المتجر أصلاً"). Every real check
// currently resolves through the `global`-only path — not a shortcut, the
// spec's own prescribed behavior for this exact situation.
//
// ks:changes message shape (OUR OWN design decision — the spec fixes the
// hash/pub-sub design but not a wire format for P0.7, which does not exist
// yet; documented in docs/P0_DEVIATIONS.md): `{"scope":"global"}` |
// `{"scope":"tenant","id":"<tenant_id>"}` | `{"scope":"channel","id":"<channel_account_id>"}`.
// On receipt we re-fetch (HGETALL) exactly that one hash rather than trust a
// state payload inside the message itself — a single extra round-trip
// (well under the spec's <1s propagation budget) buys correctness even if
// a future publisher's message payload is stale or wrong; the hash is the
// only source of truth we trust.
//
// Staleness (spec, literal): if redis-cache becomes unreachable, the last
// known cached state is used. Once that state's age exceeds
// config.killswitch.staleMaxS (KS_STALE_MAX_S, default 120s), marketing and
// broadcast SPECIFICALLY fail closed (blocked) — every other capability
// keeps using its last-known cached value regardless of age. Human-
// originated sends are never subject to any of this: they never reach
// outbound/queue.js at all (G8's human_takeover_signal inbound pipeline
// captures fromMe:true messages separately — see sessions.js), so this
// module is only ever consulted for origin=bot traffic, by construction.
//
// Deliberately a FACTORY (createKillSwitch), not a module-level singleton
// (unlike sessions.js's session registry): every acceptance-matrix test
// case needs its own independent cache/staleness state, and a shared
// singleton across `node --test`'s files/cases would leak state between
// them (H7: tests must be real and independent, never order-dependent).
// The one production instance is created once, in index.js's main().

import { config } from './config.js';
import { createRedisClient, closeRedisClient } from './redis.js';
import { logger } from './logger.js';
import { killswitchStateGauge, killswitchStaleSecondsGauge } from './metrics.js';

const DEFAULT_CHANGES_CHANNEL = 'ks:changes';
const DEFAULT_RESYNC_INTERVAL_MS = 30000;
const DEFAULT_STALE_GAUGE_INTERVAL_MS = 5000;
// H4 (every wait time has a written cap) + the F13 measurement: start() must
// NEVER hold up gateway startup waiting on redis-cache. A healthy local
// HGETALL lands in ~1ms; 1500ms is orders of magnitude above that while
// still being invisible next to the gateway's own ~1s boot.
const DEFAULT_START_TIMEOUT_MS = 1500;
// H4 + the F13 measurement: stop() must not eat the graceful-shutdown budget
// either. A Redis QUIT needs a round-trip to a server that may never answer;
// past this cap the socket is torn down locally instead (disconnect(), which
// needs no round-trip at all).
const DEFAULT_STOP_TIMEOUT_MS = 1000;

function redisHashKeyFor(scopeKey) {
  return `ks:${scopeKey}`;
}

function severityOf(state) {
  if (state === 'off') return 2;
  if (state === 'degraded') return 1;
  return 0; // 'on', or missing/undefined -> default allow (spec's documented fallback)
}

function stateFromSeverity(sev) {
  if (sev >= 2) return 'off';
  if (sev === 1) return 'degraded';
  return 'on';
}

/** Same pattern as sessions.js's own (module-private) waitForRedisReady - a
 * dedicated connection's first command must wait for 'ready' first, since
 * createRedisClient() sets enableOfflineQueue:false (a command issued
 * before then throws immediately rather than queuing). Resolves on the
 * FIRST 'ready' or 'error' event - callers here treat "it errored" as "give
 * up waiting and proceed" (H3: never block startup forever), not as fatal. */
function waitForReady(client) {
  if (client.status === 'ready') return Promise.resolve();
  return new Promise((resolve, reject) => {
    const onReady = () => { client.off('error', onError); resolve(); };
    const onError = (err) => { client.off('ready', onReady); reject(err); };
    client.once('ready', onReady);
    client.once('error', onError);
  });
}

function scopeKeyFromChangePayload(payload) {
  if (!payload || typeof payload !== 'object') return null;
  if (payload.scope === 'global') return 'global';
  if (payload.scope === 'tenant' && payload.id) return `tenant:${payload.id}`;
  // 'channel_account' is the value the DB's own CHECK constraint uses
  // (docs/reference/schema.sql: scope IN ('global','tenant','channel_account')),
  // while the Redis key the spec fixes is `ks:channel:{channel_account_id}`.
  // P0.7's publisher will most naturally send the DB's own scope value, so
  // BOTH spellings are accepted here and map to the same cache scope. Cheap
  // now; a silent, hard-to-find integration bug if left until P0.7.
  if ((payload.scope === 'channel' || payload.scope === 'channel_account') && payload.id) {
    return `channel:${payload.id}`;
  }
  return null;
}

/**
 * @param {object} [options]
 * @param {object} [options.redisConfig]        Defaults to config.redisCache.
 * @param {number} [options.staleMaxS]           Defaults to config.killswitch.staleMaxS.
 * @param {number} [options.resyncIntervalMs]    Defaults to 30000 (spec, literal: "إعادة مزامنة كاملة كل 30 ثانية").
 * @param {string} [options.changesChannel]      Defaults to 'ks:changes'.
 * @param {number} [options.staleGaugeIntervalMs] How often killswitch_stale_seconds is refreshed between resync ticks.
 * @param {number} [options.startTimeoutMs]      Hard cap on how long start() may block its caller (default 1500 - see F13).
 */
export function createKillSwitch(options = {}) {
  const redisConfig = options.redisConfig ?? config.redisCache;
  const staleMaxS = options.staleMaxS ?? config.killswitch.staleMaxS;
  const resyncIntervalMs = options.resyncIntervalMs ?? DEFAULT_RESYNC_INTERVAL_MS;
  const changesChannel = options.changesChannel ?? DEFAULT_CHANGES_CHANNEL;
  const staleGaugeIntervalMs = options.staleGaugeIntervalMs ?? DEFAULT_STALE_GAUGE_INTERVAL_MS;
  const startTimeoutMs = options.startTimeoutMs ?? DEFAULT_START_TIMEOUT_MS;
  const stopTimeoutMs = options.stopTimeoutMs ?? DEFAULT_STOP_TIMEOUT_MS;

  /** @type {Map<string, Map<string, string>>} scopeKey -> (capability -> state) */
  const cache = new Map();
  const knownTenantIds = new Set();
  const knownChannelIds = new Set();
  let lastSyncOkAt = null; // null until the first successful HGETALL of any scope
  let commandClient = null;
  let subscriberClient = null;
  let resyncTimer = null;
  let staleGaugeTimer = null;

  function registerScope(tenantId, channelAccountId) {
    if (tenantId) knownTenantIds.add(tenantId);
    if (channelAccountId) knownChannelIds.add(channelAccountId);
  }

  function scopesFor({ tenantId, channelAccountId } = {}) {
    const scopes = ['global'];
    if (tenantId) scopes.push(`tenant:${tenantId}`);
    if (channelAccountId) scopes.push(`channel:${channelAccountId}`);
    return scopes;
  }

  async function fetchScope(scopeKey) {
    const hash = await commandClient.hgetall(redisHashKeyFor(scopeKey));
    const capMap = new Map(Object.entries(hash ?? {}));
    const previousCapMap = cache.get(scopeKey);
    cache.set(scopeKey, capMap);
    lastSyncOkAt = Date.now();
    killswitchStaleSecondsGauge.set(0);
    for (const [capability, state] of capMap) {
      killswitchStateGauge.labels(scopeKey, capability).set(severityOf(state));
    }
    // F17 (found via real-VPS live verification, not guessed): a Prometheus
    // Gauge keeps reporting the LAST value set for a label combination
    // forever - it does not disappear on its own. If a capability field is
    // HDEL'd from the hash (an operator turning a restriction back off),
    // the loop above simply stops mentioning that label, so
    // killswitch_state{scope,capability} would keep reporting the OLD
    // severity (e.g. 2/off) indefinitely even though `cache` (and therefore
    // checkCapability/checkSend, which only ever reads `cache`) already
    // reverted to the correct unrestricted default. Enforcement was never
    // wrong - only the exposed metric was stale - but a stale severity=2 on
    // a real dashboard/alert is exactly as misleading as a wrong one, so any
    // capability present in the PREVIOUS fetch but missing from this one is
    // explicitly reset to its default severity (0/on) here.
    if (previousCapMap) {
      for (const capability of previousCapMap.keys()) {
        if (!capMap.has(capability)) {
          killswitchStateGauge.labels(scopeKey, capability).set(severityOf(undefined));
        }
      }
    }
    return capMap;
  }

  async function fullResync() {
    const scopeKeys = [
      'global',
      ...[...knownTenantIds].map((id) => `tenant:${id}`),
      ...[...knownChannelIds].map((id) => `channel:${id}`),
    ];
    let anyOk = false;
    for (const scopeKey of scopeKeys) {
      try {
        // eslint-disable-next-line no-await-in-loop
        await fetchScope(scopeKey);
        anyOk = true;
      } catch (err) {
        logger.error({ scopeKey, err: err.message }, '[killswitch] resync fetch failed for scope; keeping last-known cached value');
      }
    }
    return anyOk;
  }

  function isStale() {
    if (lastSyncOkAt === null) return true;
    return (Date.now() - lastSyncOkAt) > staleMaxS * 1000;
  }

  function updateStaleGauge() {
    if (lastSyncOkAt === null) {
      // Never synced successfully - report just past the staleness
      // threshold so a scrape immediately reflects "stale", not "healthy".
      killswitchStaleSecondsGauge.set(staleMaxS + 1);
      return;
    }
    killswitchStaleSecondsGauge.set((Date.now() - lastSyncOkAt) / 1000);
  }

  function getEffectiveState(capability, ctx = {}) {
    let bestSeverity = 0;
    let bestScope = 'global';
    for (const scopeKey of scopesFor(ctx)) {
      const capMap = cache.get(scopeKey);
      if (!capMap) continue; // unknown/never-synced scope -> defaults to unrestricted (spec's documented fallback)
      const scopeSeverity = Math.max(severityOf(capMap.get(capability)), severityOf(capMap.get('*')));
      if (scopeSeverity > bestSeverity) {
        bestSeverity = scopeSeverity;
        bestScope = scopeKey;
      }
    }
    return { state: stateFromSeverity(bestSeverity), scope: bestScope, severity: bestSeverity };
  }

  /**
   * @param {string} capability  e.g. 'ai_reply' | 'marketing' | 'broadcast' | 'media'.
   * @param {{ tenantId?: string|null, channelAccountId?: string|null }} [ctx]
   * @returns {{ allowed: boolean, state: string, scope: string|null, capability: string }}
   */
  function checkCapability(capability, ctx = {}) {
    registerScope(ctx.tenantId, ctx.channelAccountId);
    const isMarketingLike = capability === 'marketing' || capability === 'broadcast';
    if (isStale() && isMarketingLike) {
      // Spec, literal: staleness fail-closed applies to marketing/broadcast
      // ONLY — every other capability keeps using its last-known cached
      // value (the getEffectiveState path below), age notwithstanding.
      return { allowed: false, state: 'off', scope: 'stale-failsafe', capability };
    }
    const { state, scope } = getEffectiveState(capability, ctx);
    if (state === 'off') return { allowed: false, state, scope, capability };
    if (state === 'degraded' && isMarketingLike) return { allowed: false, state, scope, capability };
    return { allowed: true, state, scope, capability };
  }

  /**
   * Derive the capability list to check from an outbound queue item's
   * `kind` and check all of them. This is OUR mapping decision (documented,
   * docs/P0_DEVIATIONS.md): the current `kind` enum
   * ('interactive'|'bulk'|'marketing') has no separate `origin`/
   * `message_class`/`priority` fields the spec's fuller model describes —
   * every item reaching outbound/queue.js is bot-authored by construction
   * (human-originated sends never enter that queue at all), so `ai_reply`
   * is always checked; `kind === 'marketing'` additionally checks BOTH
   * `marketing` and `broadcast` (the enum has no separate priority=bulk
   * signal to tell plain marketing from broadcast marketing apart — erring
   * toward MORE blocking, never less, is the safe direction for a
   * kill-switch).
   *
   * @param {'interactive'|'bulk'|'marketing'} kind
   * @param {{ tenantId?: string|null, channelAccountId?: string|null }} [ctx]
   */
  function checkSend(kind, ctx = {}) {
    const capabilities = ['ai_reply'];
    if (kind === 'marketing') capabilities.push('marketing', 'broadcast');
    for (const capability of capabilities) {
      const result = checkCapability(capability, ctx);
      if (!result.allowed) return result;
    }
    return { allowed: true, state: 'on', scope: null, capability: null };
  }

  async function handleMessage(channel, message) {
    if (channel !== changesChannel) return;
    let payload;
    try {
      payload = JSON.parse(message);
    } catch (err) {
      logger.error({ err: err.message, message }, '[killswitch] ks:changes: invalid JSON payload, ignoring');
      return;
    }
    const scopeKey = scopeKeyFromChangePayload(payload);
    if (!scopeKey) {
      logger.error({ payload }, '[killswitch] ks:changes: unrecognized payload shape, ignoring');
      return;
    }
    try {
      await fetchScope(scopeKey);
    } catch (err) {
      logger.error({ scopeKey, err: err.message }, '[killswitch] ks:changes: refetch failed after change notification');
    }
  }

  async function start() {
    commandClient = createRedisClient(redisConfig);
    subscriberClient = createRedisClient(redisConfig);
    commandClient.on('error', (err) => {
      logger.error({ err: err.message }, '[killswitch] redis-cache command client error');
    });
    subscriberClient.on('error', (err) => {
      logger.error({ err: err.message }, '[killswitch] redis-cache subscriber client error');
    });
    subscriberClient.on('message', handleMessage);

    // The periodic timers are armed FIRST, before any await: whatever happens
    // to the initial connect/subscribe/sync below (slow, failed, or timed
    // out), redis-cache coming back later must always be noticed. If they were
    // armed after the awaits, a failure or timeout on the way there would
    // leave the subsystem with no retry loop at all - permanently stale even
    // once redis-cache recovered.
    resyncTimer = setInterval(() => {
      fullResync().catch((err) => {
        logger.error({ err: err.message }, '[killswitch] periodic resync failed');
      });
    }, resyncIntervalMs);
    resyncTimer.unref?.();
    staleGaugeTimer = setInterval(updateStaleGauge, staleGaugeIntervalMs);
    staleGaugeTimer.unref?.();

    // The initial connect + subscribe + full sync. Deliberately written as a
    // promise that NEVER rejects, so racing it against the deadline below
    // can never leave an unhandled rejection behind when the deadline wins.
    const initialSync = (async () => {
      try {
        // Both clients are constructed with enableOfflineQueue:false +
        // lazyConnect:false (redis.js's own createRedisClient) - a command
        // issued before the underlying connection reaches 'ready' throws
        // immediately ("Stream isn't writeable ..."), it does not queue.
        // Every other dedicated-connection call site in this codebase waits
        // for 'ready' first (sessions.js's own waitForRedisReady, same
        // pattern duplicated here since that one is module-private) - found
        // by actually running this against a real redis-cache, not guessed
        // (docs/P0_FINDINGS.md F11).
        await Promise.allSettled([waitForReady(commandClient), waitForReady(subscriberClient)]);
        try {
          await subscriberClient.subscribe(changesChannel);
        } catch (err) {
          logger.error({ err: err.message, changesChannel }, '[killswitch] initial subscribe failed; the periodic resync keeps retrying');
        }
        // Syncing before start() returns means the first checkCapability()
        // call reflects real redis-cache state rather than the all-'on'
        // empty-cache default - worth waiting for, but only up to
        // startTimeoutMs (below). fullResync() never throws.
        await fullResync();
        return 'synced';
      } catch (err) {
        logger.error({ err: err.message }, '[killswitch] initial redis-cache sync failed; continuing with last-known state');
        return 'failed';
      }
    })();

    // H3 + F13 (a real, MEASURED defect in this file's first version): the
    // kill-switch is an enforcement layer, never a startup dependency. When
    // redis-cache is reachable at the TCP level but never answers (a wrong
    // password, a wrong service behind that host:port, a hung Redis),
    // ioredis does not fail fast - it burns connectTimeout (5s) and then
    // commandTimeout (5s) on the first HGETALL. Awaiting that directly in
    // main() measurably delayed a real gateway's readiness from 1057ms to
    // 10698ms, which also delays session rehydration and every outbound
    // worker - and inbound/WAL must never be gated on the kill-switch at
    // all (spec, literal). So the initial sync gets a hard cap here and
    // finishes in the background if it overruns: the timers armed above keep
    // retrying, and isStale() correctly reports "never synced" in the
    // meantime (marketing/broadcast fail closed, everything else defaults to
    // unrestricted - the spec's own documented degraded posture).
    let deadlineTimer = null;
    const deadline = new Promise((resolve) => {
      deadlineTimer = setTimeout(() => resolve('timeout'), startTimeoutMs);
      deadlineTimer.unref?.();
    });
    const outcome = await Promise.race([initialSync, deadline]);
    if (deadlineTimer) clearTimeout(deadlineTimer);
    if (outcome === 'timeout') {
      logger.warn(
        { startTimeoutMs },
        '[killswitch] initial redis-cache sync did not finish within its startup budget; continuing startup and syncing in the background (marketing/broadcast fail closed until a sync lands)',
      );
    }
    return outcome;
  }

  /**
   * Close one client without letting a hung server hold up shutdown.
   * closeRedisClient() already falls back to disconnect() when quit() REJECTS,
   * but the failure mode measured in F13 is quit() never settling at all
   * (redis-cache reachable at the TCP level, answering nothing) - an internal
   * try/catch cannot help with that, so the wait is capped out here and the
   * socket is then torn down locally.
   */
  async function closeBounded(client, label) {
    if (!client) return;
    let timer = null;
    const deadline = new Promise((resolve) => {
      timer = setTimeout(() => resolve('timeout'), stopTimeoutMs);
      timer.unref?.();
    });
    const closing = closeRedisClient(client)
      .then(() => 'closed')
      .catch((err) => {
        logger.error({ err: err.message, client: label }, '[killswitch] error closing redis-cache client');
        return 'failed';
      });
    const outcome = await Promise.race([closing, deadline]);
    if (timer) clearTimeout(timer);
    if (outcome !== 'closed') {
      client.disconnect();
      logger.warn({ client: label, stopTimeoutMs, outcome }, '[killswitch] graceful QUIT did not complete within its budget; socket disconnected locally instead');
    }
  }

  async function stop() {
    if (resyncTimer) clearInterval(resyncTimer);
    if (staleGaugeTimer) clearInterval(staleGaugeTimer);
    resyncTimer = null;
    staleGaugeTimer = null;
    // In parallel, not in sequence: the two clients are independent, and
    // serializing them would double the worst-case shutdown cost for no gain.
    await Promise.all([
      closeBounded(subscriberClient, 'subscriber'),
      closeBounded(commandClient, 'command'),
    ]);
  }

  function getStatus() {
    return {
      lastSyncOkAt,
      isStale: isStale(),
      knownScopes: [...cache.keys()],
    };
  }

  return {
    start,
    stop,
    registerScope,
    checkCapability,
    checkSend,
    getEffectiveState,
    isStale,
    getStatus,
    // exposed for direct, real-Redis test manipulation (publish a change,
    // then assert the cache picked it up) without reaching into closures.
    _fetchScope: fetchScope,
    _fullResync: fullResync,
  };
}
GATEWAY_KILLSWITCH_JS_EOF
echo "wrote $KS_JS ($(wc -l < "$KS_JS") lines)"

echo
echo "== writing the updated killswitch.test.js (one new test added) =="
cat > "$KS_TEST" <<'GATEWAY_KILLSWITCH_TEST_JS_EOF'
process.env.ALLOW_FAKE_WA = '1';
process.env.NODE_ENV = 'test';

import test, { before, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';

import { createRedisClient, closeRedisClient } from '../redis.js';
import { config } from '../config.js';
import { createKillSwitch } from '../killswitch.js';
import { killswitchStateGauge } from '../metrics.js';
import { enqueueSend, startOutboundWorker, processOne, recoverInflight, _keys } from '../outbound/queue.js';
import { makeFakeWaSocket } from '../../test-support/fakeWaDriver.js';

// Real redis-cache (never a mock - H7), via the same connection helper and
// env-configured host/port convention already used for redis-durable
// throughout this project (REDIS_CACHE_HOST/PORT - see config.js). In this
// sandbox/CI setup redis-cache and redis-durable are the SAME physical
// redis-server instance (no collision risk: `ks:*` keys are a disjoint
// namespace from `out:*`/`in:*`/etc) - on the real VPS they are the actual
// separate `redis-cache`/`redis-durable` compose services.
const admin = createRedisClient(config.redisCache);

function waitForReady(client) {
  if (client.status === 'ready') return Promise.resolve();
  return new Promise((resolve, reject) => {
    const onReady = () => { client.off('error', onError); resolve(); };
    const onError = (err) => { client.off('ready', onReady); reject(err); };
    client.once('ready', onReady);
    client.once('error', onError);
  });
}

before(async () => { await waitForReady(admin); });
test.after(async () => { await closeRedisClient(admin); });

function uid(prefix) { return `${prefix}-${crypto.randomUUID()}`; }

async function setState(scopeKey, capability, state) {
  await admin.hset(`ks:${scopeKey}`, capability, state);
}
async function clearScope(scopeKey) {
  await admin.del(`ks:${scopeKey}`);
}
async function publishChange(payload) {
  await admin.publish('ks:changes', JSON.stringify(payload));
}
async function gaugeValueFor(scopeKey, capability) {
  const snapshot = await killswitchStateGauge.get();
  const entry = snapshot.values.find(
    (v) => v.labels.scope === scopeKey && v.labels.capability === capability,
  );
  return entry ? entry.value : undefined;
}

// Every test gets its own createKillSwitch() instance (factory, not a
// singleton - see killswitch.js's own header comment on why) and its own
// randomized scope ids, so cases never interfere with each other even
// though they may run concurrently within this file.
async function freshKillSwitch(opts = {}) {
  const ks = createKillSwitch({ resyncIntervalMs: 3600000, ...opts }); // long resync interval: these tests drive state via ks:changes, not the periodic timer, except where noted
  await ks.start();
  return ks;
}

test('(P0.6B) global scope: off blocks ai_reply; on/absent allows it', async () => {
  const ks = await freshKillSwitch();
  try {
    let res = ks.checkCapability('ai_reply', {});
    assert.equal(res.allowed, true, 'no global entry -> default allow');

    await setState('global', 'ai_reply', 'off');
    await publishChange({ scope: 'global' });
    await new Promise((r) => setTimeout(r, 100)); // let the pubsub-triggered refetch land

    res = ks.checkCapability('ai_reply', {});
    assert.equal(res.allowed, false);
    assert.equal(res.state, 'off');
    assert.equal(res.scope, 'global');
  } finally {
    await clearScope('global');
    await ks.stop();
  }
});

test('(P0.6B/F17) removing a capability from the hash resets killswitch_state back to 0, not stuck at its last severity', async () => {
  const ks = await freshKillSwitch();
  const tenantId = uid('tenant');
  const scopeKey = `tenant:${tenantId}`;
  try {
    await setState(scopeKey, 'ai_reply', 'off');
    await publishChange({ scope: 'tenant', id: tenantId });
    await new Promise((r) => setTimeout(r, 100));

    assert.equal(ks.checkCapability('ai_reply', { tenantId }).allowed, false, 'sanity: the flip actually took effect');
    assert.equal(await gaugeValueFor(scopeKey, 'ai_reply'), 2, 'gauge must report severity 2 (off) while the flip is live');

    // The real bug (F17): HDEL-ing the field (an operator turning the
    // restriction back off) must not leave the Prometheus gauge stuck
    // reporting the old severity forever.
    await admin.hdel(`ks:${scopeKey}`, 'ai_reply');
    await publishChange({ scope: 'tenant', id: tenantId });
    await new Promise((r) => setTimeout(r, 100));

    assert.equal(ks.checkCapability('ai_reply', { tenantId }).allowed, true, 'enforcement correctly reverted (this half already worked before the fix)');
    assert.equal(await gaugeValueFor(scopeKey, 'ai_reply'), 0, 'the gauge must reset to 0 (on) once the capability is gone from the hash, not stay stuck at 2');
  } finally {
    await clearScope(scopeKey);
    await ks.stop();
  }
});

test('(P0.6B) strictest-wins: tenant=off overrides global=on for that tenant, leaves other tenants unaffected', async () => {
  const ks = await freshKillSwitch();
  const tenantId = uid('tenant');
  const otherTenantId = uid('tenant');
  try {
    await setState('global', 'ai_reply', 'on');
    await setState(`tenant:${tenantId}`, 'ai_reply', 'off');
    await publishChange({ scope: 'global' });
    await publishChange({ scope: 'tenant', id: tenantId });
    await new Promise((r) => setTimeout(r, 100));

    const blocked = ks.checkCapability('ai_reply', { tenantId });
    assert.equal(blocked.allowed, false);
    assert.equal(blocked.scope, `tenant:${tenantId}`);

    const allowed = ks.checkCapability('ai_reply', { tenantId: otherTenantId });
    assert.equal(allowed.allowed, true, 'a different, never-configured tenant must not inherit the first tenant\'s block');

    const globalOnly = ks.checkCapability('ai_reply', {});
    assert.equal(globalOnly.allowed, true, 'no tenant context at all -> only the (allowing) global scope applies');
  } finally {
    await clearScope('global');
    await clearScope(`tenant:${tenantId}`);
    await ks.stop();
  }
});

test('(P0.6B) channel scope + wildcard capability: channel *=degraded blocks marketing but allows ai_reply', async () => {
  const ks = await freshKillSwitch();
  const channelAccountId = uid('channel');
  try {
    await setState(`channel:${channelAccountId}`, '*', 'degraded');
    await publishChange({ scope: 'channel', id: channelAccountId });
    await new Promise((r) => setTimeout(r, 100));

    const marketing = ks.checkCapability('marketing', { channelAccountId });
    assert.equal(marketing.allowed, false, 'degraded blocks marketing (spec: degraded blocks marketing/broadcast only)');

    const reply = ks.checkCapability('ai_reply', { channelAccountId });
    assert.equal(reply.allowed, true, 'degraded still allows ordinary service replies');
  } finally {
    await clearScope(`channel:${channelAccountId}`);
    await ks.stop();
  }
});

test('(P0.6B) ks:changes accepts BOTH scope spellings: "channel" and the DB\'s own "channel_account"', async () => {
  const ks = await freshKillSwitch();
  const chA = uid('channel');
  const chB = uid('channel');
  try {
    await setState(`channel:${chA}`, 'ai_reply', 'off');
    await setState(`channel:${chB}`, 'ai_reply', 'off');
    // the spelling the Redis key uses...
    await publishChange({ scope: 'channel', id: chA });
    // ...and the spelling docs/reference/schema.sql's CHECK constraint uses,
    // which is what P0.7's publisher will most naturally send.
    await publishChange({ scope: 'channel_account', id: chB });
    await new Promise((r) => setTimeout(r, 150));

    assert.equal(ks.checkCapability('ai_reply', { channelAccountId: chA }).allowed, false, "'channel' spelling must take effect");
    assert.equal(ks.checkCapability('ai_reply', { channelAccountId: chB }).allowed, false, "'channel_account' spelling must take effect");
  } finally {
    await clearScope(`channel:${chA}`);
    await clearScope(`channel:${chB}`);
    await ks.stop();
  }
});

test('(P0.6B) checkSend: kind=marketing checks BOTH marketing and broadcast; either being off blocks the send', async () => {
  const ks = await freshKillSwitch();
  try {
    await setState('global', 'broadcast', 'off');
    await publishChange({ scope: 'global' });
    await new Promise((r) => setTimeout(r, 100));

    const res = ks.checkSend('marketing', {});
    assert.equal(res.allowed, false);
    assert.equal(res.capability, 'broadcast', 'broadcast is checked even though only marketing kind was sent - the conservative mapping (docs/P0_DEVIATIONS.md)');
  } finally {
    await clearScope('global');
    await ks.stop();
  }
});

test('(P0.6B) checkSend: kind=interactive/bulk never checks marketing/broadcast, only ai_reply', async () => {
  const ks = await freshKillSwitch();
  try {
    await setState('global', 'marketing', 'off');
    await setState('global', 'broadcast', 'off');
    await publishChange({ scope: 'global' });
    await new Promise((r) => setTimeout(r, 100));

    const res = ks.checkSend('interactive', {});
    assert.equal(res.allowed, true, 'marketing/broadcast being off must not block a plain interactive send');
  } finally {
    await clearScope('global');
    await ks.stop();
  }
});

test('(P0.6B) PUBLISH-to-block propagation is measurably under 1 second (spec acceptance criterion)', async () => {
  const ks = await freshKillSwitch();
  try {
    assert.equal(ks.checkCapability('ai_reply', {}).allowed, true);

    const start = Date.now();
    await setState('global', 'ai_reply', 'off');
    await publishChange({ scope: 'global' });

    let elapsedMs = null;
    for (let i = 0; i < 100; i += 1) {
      // eslint-disable-next-line no-await-in-loop
      await new Promise((r) => setTimeout(r, 10));
      if (!ks.checkCapability('ai_reply', {}).allowed) {
        elapsedMs = Date.now() - start;
        break;
      }
    }
    assert.ok(elapsedMs !== null, 'block never took effect within the 1s polling window');
    assert.ok(elapsedMs < 1000, `PUBLISH-to-block propagation took ${elapsedMs}ms, spec requires < 1000ms`);
  } finally {
    await clearScope('global');
    await ks.stop();
  }
});

test('(P0.6B) staleness: past KS_STALE_MAX_S, marketing/broadcast fail closed but ai_reply keeps using the last-known value', async () => {
  const ks = await freshKillSwitch({ staleMaxS: 1 }); // real 1s threshold - a real elapsed-time test, not simulated
  try {
    await setState('global', 'ai_reply', 'on');
    await publishChange({ scope: 'global' });
    await new Promise((r) => setTimeout(r, 100));
    assert.equal(ks.checkCapability('ai_reply', {}).allowed, true);
    assert.equal(ks.checkSend('marketing', {}).allowed, true, 'fresh sync -> marketing allowed (no restriction set)');

    // Simulate redis-cache going unreachable: stop the subscriber/command
    // clients' periodic resync from ever succeeding again by closing them,
    // then just wait past staleMaxS - isStale() is purely a function of
    // elapsed real time since the last successful sync, so this is a real
    // clock-driven test, not a mocked one.
    await ks.stop();
    await new Promise((r) => setTimeout(r, 1200));

    assert.equal(ks.isStale(), true);
    const marketingRes = ks.checkSend('marketing', {});
    assert.equal(marketingRes.allowed, false, 'stale + marketing -> fail closed');
    assert.equal(marketingRes.scope, 'stale-failsafe');

    const replyRes = ks.checkCapability('ai_reply', {});
    assert.equal(replyRes.allowed, true, 'stale but ai_reply keeps using its last-known cached value (on)');
  } finally {
    await clearScope('global');
  }
});

test('(P0.6B) redis-cache never reachable at all: isStale() is true from the start, marketing fails closed, ai_reply defaults to on', async () => {
  const ks = createKillSwitch({
    redisConfig: { host: '127.0.0.1', port: 1, password: '', timeoutMs: 200, maxRetries: 0 },
    staleMaxS: 1,
    resyncIntervalMs: 3600000,
  });
  try {
    await ks.start(); // must not throw (H3) even though redis-cache is unreachable
    assert.equal(ks.isStale(), true, 'never synced -> stale from the start');
    assert.equal(ks.checkSend('marketing', {}).allowed, false);
    assert.equal(ks.checkCapability('ai_reply', {}).allowed, true, 'no cached restriction -> default allow, per the documented fallback');
  } finally {
    await ks.stop();
  }
});

// --- integration with outbound/queue.js's real send pipeline -------------

const redisDurable = createRedisClient();
before(async () => { await waitForReady(redisDurable); });
test.after(async () => { await closeRedisClient(redisDurable); });

test('(P0.6B) outbound/queue.js: a blocked item becomes failed(error_class=blocked), never silently requeued forever', async () => {
  const ks = await freshKillSwitch();
  const sessionId = uid('sess');
  const sock = makeFakeWaSocket({ sendLatencyMs: 1 });
  try {
    await setState('global', 'ai_reply', 'off');
    await publishChange({ scope: 'global' });
    await new Promise((r) => setTimeout(r, 100));

    const clientMsgId = crypto.randomUUID();
    await enqueueSend(redisDurable, { sessionId, clientMsgId, to: `${uid('to')}@s.whatsapp.net`, text: 'hi', kind: 'interactive' });
    const raw = await redisDurable.blmove(_keys.queueKey(sessionId), _keys.inflightKey(sessionId), 'LEFT', 'RIGHT', 1);
    assert.ok(raw, 'item should be present in the queue');

    const ctx = {
      getSocket: () => sock,
      checkKillSwitch: (kind) => ks.checkSend(kind, {}),
    };
    const result = await processOne(redisDurable, sessionId, raw, ctx);
    assert.equal(result.outcome, 'failed');
    assert.equal(result.reason, 'blocked');

    // Confirmed NOT sent through the socket, and NOT left sitting in
    // inflight/queue forever (resolveInflight ran).
    assert.equal(sock._fake.sentMessages.length, 0);
    const inflightLen = await redisDurable.llen(_keys.inflightKey(sessionId));
    assert.equal(inflightLen, 0);
    const queueLen = await redisDurable.llen(_keys.queueKey(sessionId));
    assert.equal(queueLen, 0);
  } finally {
    await clearScope('global');
    await ks.stop();
  }
});

test('(P0.6B) outbound/queue.js: ctx without checkKillSwitch keeps the old always-allow behavior (backward compat)', async () => {
  const sessionId = uid('sess');
  const sock = makeFakeWaSocket({ sendLatencyMs: 1 });
  const clientMsgId = crypto.randomUUID();
  await enqueueSend(redisDurable, { sessionId, clientMsgId, to: `${uid('to')}@s.whatsapp.net`, text: 'hi', kind: 'interactive' });
  const raw = await redisDurable.blmove(_keys.queueKey(sessionId), _keys.inflightKey(sessionId), 'LEFT', 'RIGHT', 1);
  const result = await processOne(redisDurable, sessionId, raw, { getSocket: () => sock });
  assert.equal(result.outcome, 'sent');
  assert.equal(sock._fake.sentMessages.length, 1);
});
GATEWAY_KILLSWITCH_TEST_JS_EOF
echo "wrote $KS_TEST ($(wc -l < "$KS_TEST") lines)"

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
    # A hunt_gate failure only blocks THIS delivery if one of the two files
    # this script itself just wrote is among the violations - anything else
    # is pre-existing repo debt, unrelated to F17, and explicitly out of
    # scope for this patch (the user's own call, not mine to silently widen
    # or silently hide).
    if grep -qE "^${KS_JS//\//\\/}:|^${KS_TEST//\//\\/}:" "$HG_OUT"; then
      echo "REFUSED: hunt_gate found a real violation in a file THIS script just wrote (see above) - that one is on me to fix, not to skip." >&2
      rm -f "$HG_OUT"
      exit 1
    fi
    echo
    echo "== hunt_gate failed, but every violation above is in a PRE-EXISTING file this script did not touch =="
    echo "   (none of them are in $KS_JS or $KS_TEST). Continuing with just the F17 fix, as agreed -"
    echo "   the violations above remain open and are not fixed or hidden by this script."
  fi
  rm -f "$HG_OUT"
else
  echo "== scripts/hunt_gate.mjs not found at repo root - skipping (paste its real path if it lives elsewhere) =="
fi

echo
echo "== running the full gateway test suite (real redis-cache/redis-durable, no mocks - H7) =="
# Copied EXACTLY from batch_p06b_final.sh (grepped from the real file on the
# VPS, not re-guessed) - two earlier guesses in this same script both failed:
# a bare host-side `npm test` can't resolve the `redis-cache`/`redis-durable`
# Docker network aliases at all (falls back to 127.0.0.1:6379/ECONNREFUSED),
# and a plain `docker compose run gateway npm test` fails differently: the
# built image's Dockerfile only COPYs `src` and runs `npm ci --omit=dev`, so
# it has neither `scripts/run-tests.mjs` nor `test-support/` at all. The real,
# proven-working mechanism bind-mounts the live repo's src/scripts/test-support
# (read-only) over the image's own, and overrides three env vars - it does
# NOT override METRICS_TOKEN or any REDIS_CACHE_*/REDIS_DURABLE_* var, which
# come from the `gateway` service's own compose-defined environment (D-27),
# exactly as intended.
DC="docker compose"; $DC version >/dev/null 2>&1 || DC="docker-compose"
MOUNTS=(-v "$(pwd)/gateway/src:/app/src:ro" -v "$(pwd)/gateway/scripts:/app/scripts:ro" -v "$(pwd)/gateway/test-support:/app/test-support:ro" -v "$(pwd)/gateway/package.json:/app/package.json:ro")
TEST_ENV=(-e ALLOW_FAKE_WA=1 -e NODE_ENV=test -e LOG_LEVEL=error)
timeout -k 30 --foreground 700 $DC run --rm -T "${TEST_ENV[@]}" "${MOUNTS[@]}" gateway npm test < /dev/null

echo
echo "== recording F17 in docs/P0_FINDINGS.md (append-only, idempotent) =="
FINDINGS=docs/P0_FINDINGS.md
if [ ! -f "$FINDINGS" ]; then
  echo "REFUSED: $FINDINGS not found - refusing to create it from a fragment (it should already hold F1-F16)." >&2
  echo "Tell me if it moved rather than let me guess; nothing else in this script depends on it." >&2
  exit 1
fi
if grep -q '^## F17 ' "$FINDINGS"; then
  echo "F17 already present in $FINDINGS - skipping (idempotent)."
else
  cp "$FINDINGS" "${FINDINGS}.bak.${TS}"
  echo "backed up: ${FINDINGS}.bak.${TS}"
  printf '\n' >> "$FINDINGS"
  cat >> "$FINDINGS" <<'P06_FINDING_F17_EOF'
## F17 — `killswitch_state{scope,capability}` يبقى عالقاً على آخر شدّة بعد `HDEL` للقدرة — مقياس مضلِّل، لا خلل تنفيذ (اكتُشف بالتحقق الحي الحقيقي بعد النشر، لا بالمراجعة النظرية)

**كيف اكتُشف:** بعد نشر الدفعة ب فعلياً (`docker compose up -d --build gateway`) وتشغيل سكربت تحقق حي (`ops/verify_p06b_live.sh`) يقلب `ks:global.ai_reply` إلى `off` عبر `redis-cache` الحقيقي ثم يُعيده، أظهر `/metrics` **بعد** إزالة الحقل (`HDEL`) ونشر التغيير: `killswitch_state{scope="global",capability="ai_reply"} 2` — أي لا يزال يُبلِغ عن الشدّة القديمة (off) رغم أن الحقل لم يعد موجوداً في الهاش إطلاقاً.

**السبب الجذري (قراءة كود، لا تخمين):** `killswitch.js`'s `fetchScope()` كانت تكتفي بهذا:
```js
for (const [capability, state] of capMap) {
  killswitchStateGauge.labels(scopeKey, capability).set(severityOf(state));
}
```
أي أنها تضبط القيمة فقط للقدرات **الموجودة حالياً** في نتيجة `HGETALL`. عداد Prometheus (`Gauge`) لا يُنسي قيمة سبق ضبطها لمجموعة labels معيّنة من تلقاء نفسه — فحين يختفي حقل من الهاش (عملية `HDEL` تُعيد قدرة إلى الافتراضي المفتوح)، لا يلمس الحلقة أعلاه ذلك الـlabel إطلاقاً بعد الآن، فيبقى المقياس عالقاً على آخر قيمة **إلى الأبد** (حتى إعادة تشغيل العملية).

**التأكيد أن هذا خلل مقياس فقط، لا خلل تنفيذ حقيقي:** `cache.set(scopeKey, capMap)` في نفس الدالة **يستبدل** خريطة القدرات بالكامل بنتيجة `HGETALL` الجديدة — فحقل مُزال من الهاش يختفي أيضاً من `cache` فوراً. و`getEffectiveState`/`checkCapability`/`checkSend` تقرأ **فقط** من `cache`، فتُعيد `severityOf(undefined) = 0` (على/غير مقيَّد) بشكل صحيح تماماً منذ لحظة إعادة المزامنة التالية. أي أن **التنفيذ الفعلي كان سليماً طوال الوقت** — المشكلة حصراً في أن ما تعرضه `/metrics` كان يكذب على أي لوحة مراقبة أو قاعدة تنبيه مستقبلية.

**الإصلاح:** `fetchScope()` تحتفظ الآن بالخريطة **السابقة** قبل استبدالها، وبعد ضبط القيم الجديدة، تُصفِّر (`severityOf(undefined)` = 0) أي قدرة كانت موجودة في الخريطة السابقة واختفت من الخريطة الجديدة:
```js
const previousCapMap = cache.get(scopeKey);
cache.set(scopeKey, capMap);
// ...set() loop كما هو...
if (previousCapMap) {
  for (const capability of previousCapMap.keys()) {
    if (!capMap.has(capability)) {
      killswitchStateGauge.labels(scopeKey, capability).set(severityOf(undefined));
    }
  }
}
```

**التحقق (حقيقي، بإفشال مُتعمَّد أولاً — H7):** اختبار جديد (`killswitch.test.js`) يضبط قدرة إلى `off`، يتحقق من `killswitch_state=2` عبر قراءة سجل الـGauge الحقيقي مباشرةً (`killswitchStateGauge.get()`)، ثم يُزيل الحقل (`HDEL` حقيقي على Redis محلي حقيقي)، وينشر التغيير، ويتحقق من رجوع القيمة إلى `0`. أُزيل الإصلاح مؤقتاً فتأكّد الاختبار من **فشله فعلاً** (`2 !== 0`)، ثم أُعيد الإصلاح فنجح. سويّة الملف الكاملة: **12/12** (كانت 11)، والـsuite الكامل: **118/118** (كان 117 — الرقم الذي وصل إليه المستخدم فعلياً على الـVPS في `batch_p06b_final.sh`).

**الأثر:** لا تأثير على حركة الإنتاج الفعلية إطلاقاً — لم يُحظَر أي إرسال بسبب هذا الخلل ولا لحظة واحدة (تأكّدتُ بقراءة الكود لا بالتخمين). الأثر الوحيد: أي شخص يقرأ `/metrics` يدوياً بعد إزالة قيد، أو أي قاعدة تنبيه مستقبلية (Batch C، المراقبة) مبنية على هذا المقياس، كانت ستُخبَر بمعلومة قديمة خاطئة إلى أن تُعاد العملية. اكتُشف هذا **قبل** أن تُبنى أي قاعدة تنبيه عليه (المراقبة لم تُبنَ بعد — D-27)، فلا حاجة لأي تعديل على شيء آخر غير `killswitch.js` و`killswitch.test.js`.
P06_FINDING_F17_EOF
  echo "appended F17 to $FINDINGS ($(wc -c < "$FINDINGS") bytes total now)"
fi

echo
echo "== diff summary =="
git diff --stat -- "$KS_JS" "$KS_TEST" "$FINDINGS" || true

echo
echo "Done. Nothing was staged or committed - review the diff above, then it's"
echo "your call (same as Gate A) whether/when to 'git add' these files"
echo "alongside the rest of the still-uncommitted P0.6 Batch B changes."
