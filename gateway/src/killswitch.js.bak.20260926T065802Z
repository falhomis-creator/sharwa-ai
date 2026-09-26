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
