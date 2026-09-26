#!/usr/bin/env node
// ops/wire_p06.mjs
//
// P0.6 deployment wiring for the `gateway` service in docker-compose.yml.
// Surgical, idempotent text edits - NOT a YAML round-trip, which would
// reformat the file and strip every comment from real infrastructure.
//
// What it sets, and why each one is required rather than nice-to-have:
//
//   REDIS_CACHE_HOST/PORT/PASSWORD
//       killswitch.js has no address for redis-cache otherwise and falls back
//       to 127.0.0.1:6379 (docs/P0_DEVIATIONS.md D-26). redis-cache is on the
//       `data` network, which `gateway` already joins, and it REQUIRES a
//       password (verified: NOAUTH once REDISCLI_AUTH is cleared).
//   METRICS_TOKEN
//       CRITICAL. P0.6 Batch A made main() refuse to start with an empty
//       METRICS_TOKEN (H5, spec: "سرّ فارغ = رفض إقلاع"). compose never set
//       one, so the next `docker compose up -d gateway` on the new image would
//       fail to boot. See docs/P0_FINDINGS.md F16.
//   NODE_OPTIONS=--max-old-space-size=900
//       Spec P0.6 §3, and it must stay below mem_limit.
//   mem_limit / memswap_limit 512m -> 1200m
//       Spec P0.6: the heap cap above needs headroom over it. Host has 7941MB;
//       this takes the total compose budget from 2688MB to 3376MB.
//   stop_grace_period: 30s
//       config.js caps SHUTDOWN_TIMEOUT_MS at 25000 precisely so the process
//       always exits on its own before Docker SIGKILLs it. Without this key
//       compose uses its 10s default, which is SHORTER than our own budget -
//       so a graceful shutdown could be killed mid-flush.
//
// Only the exact `gateway:` service is touched - never `gateway-forwarder:`,
// which starts with the same characters. The forwarder deliberately gets
// nothing here: it runs src/forwarder.js, never index.js's main(), so it has
// no kill-switch and no METRICS_TOKEN requirement (that asymmetry is exactly
// why D-21 put the check in main() and not in config.js).
//
// Usage: node ops/wire_p06.mjs <docker-compose.yml>
// Exit 0 = changed or already correct (details on stdout). Exit 1 = refused.

import fs from 'node:fs';

const ENV_WANTED = [
  ['REDIS_CACHE_HOST', 'redis-cache'],
  ['REDIS_CACHE_PORT', '"6379"'],
  ['REDIS_CACHE_PASSWORD', '${REDIS_CACHE_PASSWORD:?REDIS_CACHE_PASSWORD is required (see .env)}'],
  ['METRICS_TOKEN', '${METRICS_TOKEN:?METRICS_TOKEN is required - the gateway refuses to start without it (see .env)}'],
  ['NODE_OPTIONS', '--max-old-space-size=900'],
];

const file = process.argv[2];
if (!file) {
  console.error('usage: node ops/wire_p06.mjs <docker-compose.yml>');
  process.exit(1);
}

const original = fs.readFileSync(file, 'utf8');
let lines = original.split('\n');

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
  for (let i = idx + 1; i < all.length; i += 1) {
    if (isBlank(all[i]) || isComment(all[i])) continue;
    if (indentOf(all[i]) <= ind) { end = i; break; }
  }
  return { idx, ind, end };
}

const gw = findServiceBlock(lines, 'gateway');
if (!gw) {
  console.error('REFUSED: no exact `gateway:` service key found. Nothing written.');
  process.exit(1);
}

const changes = [];

// ---- 1. environment keys ---------------------------------------------------
let envIdx = -1;
let envIndent = -1;
for (let i = gw.idx + 1; i < gw.end; i += 1) {
  const m = /^(\s+)environment:\s*(#.*)?$/.exec(lines[i]);
  if (m && m[1].length > gw.ind) { envIdx = i; envIndent = m[1].length; break; }
}
if (envIdx === -1) {
  console.error('REFUSED: the gateway service has no `environment:` block; refusing to guess where to add one.');
  process.exit(1);
}

let envEnd = gw.end;
for (let i = envIdx + 1; i < gw.end; i += 1) {
  if (isBlank(lines[i]) || isComment(lines[i])) continue;
  if (indentOf(lines[i]) <= envIndent) { envEnd = i; break; }
}

let entryIndent = envIndent + 2;
let listStyle = false;
for (let i = envIdx + 1; i < envEnd; i += 1) {
  if (isBlank(lines[i]) || isComment(lines[i])) continue;
  entryIndent = indentOf(lines[i]);
  listStyle = lines[i].trimStart().startsWith('- ');
  break;
}

const envRegion = lines.slice(envIdx + 1, envEnd);
const hasEnvKey = (k) => envRegion.some((l) => {
  const t = l.trim();
  return t.startsWith(`${k}:`) || t.startsWith(`- ${k}=`);
});

const additions = [];
for (const [k, v] of ENV_WANTED) {
  if (hasEnvKey(k)) continue;
  additions.push(' '.repeat(entryIndent) + (listStyle ? `- ${k}=${v.replace(/^"|"$/g, '')}` : `${k}: ${v}`));
  changes.push(`env ${k}`);
}
if (additions.length > 0) {
  let insertAt = envIdx + 1;
  for (let i = envIdx + 1; i < envEnd; i += 1) {
    if (isBlank(lines[i]) || isComment(lines[i])) continue;
    insertAt = i + 1;
  }
  lines.splice(insertAt, 0, ...additions);
}

// ---- 2. scalar keys on the service itself ----------------------------------
// Recompute the block after the env insertion above shifted line numbers.
function setServiceScalar(key, value, { addIfMissing = true } = {}) {
  const blk = findServiceBlock(lines, 'gateway');
  if (!blk) return;
  const re = new RegExp(`^(\\s+)${key}:\\s*(.*?)\\s*(#.*)?$`);
  for (let i = blk.idx + 1; i < blk.end; i += 1) {
    if (isBlank(lines[i]) || isComment(lines[i])) continue;
    // only direct children of the service, never keys nested deeper
    const m = re.exec(lines[i]);
    if (m && m[1].length === indentOf(lines[blk.idx]) + 2) {
      if (m[2] === value) return; // already correct
      lines[i] = `${m[1]}${key}: ${value}${m[3] ? ` ${m[3]}` : ''}`;
      changes.push(`${key}: ${m[2]} -> ${value}`);
      return;
    }
  }
  if (!addIfMissing) return;
  // add as a direct child, right after the service key line
  const childIndent = indentOf(lines[blk.idx]) + 2;
  lines.splice(blk.idx + 1, 0, `${' '.repeat(childIndent)}${key}: ${value}`);
  changes.push(`${key}: (added) ${value}`);
}

setServiceScalar('mem_limit', '1200m');
setServiceScalar('memswap_limit', '1200m');
setServiceScalar('stop_grace_period', '30s');

if (changes.length === 0) {
  console.log('OK: docker-compose.yml already has every P0.6 gateway setting; nothing written.');
  process.exit(0);
}

const backup = `${file}.bak.${new Date().toISOString().replace(/[:.]/g, '-')}`;
fs.writeFileSync(backup, original);
fs.writeFileSync(file, lines.join('\n'));
console.log(`BACKUP: ${backup}`);
console.log(`CHANGED (${changes.length}):`);
for (const c of changes) console.log(`  - ${c}`);
process.exit(0);
