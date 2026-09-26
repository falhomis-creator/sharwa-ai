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
