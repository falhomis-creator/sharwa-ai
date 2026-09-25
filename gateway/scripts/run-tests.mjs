#!/usr/bin/env node
// gateway/scripts/run-tests.mjs
//
// P0.6/F10 (docs/P0_FINDINGS.md - a real, three-times-confirmed VPS finding,
// not guessed): the test files that spawn real OS child processes
// (dual_instance_chaos.test.js: 2 processes; outbound_chaos.test.js: up to 6
// sequential fork()s across its 5 kills; p06_operational_readiness.test.js:
// up to 3 processes) must never run concurrently with EACH OTHER.
// `node --test`'s own file-level concurrency is CPU-aware (roughly one file
// per core), but that accounting has no visibility into a file that spawns
// MORE real OS processes internally - so under `node --test`'s own default
// concurrency, or a blanket `--test-concurrency=2` (tried first, proven
// worse: it turned a ~190s suite into a ~24.6-minute one on the real VPS AND
// still produced a genuine failure in an unrelated heavy test paired against
// another - see F10 for the full record, kept even though that attempt
// failed), enough of these land in the same window to starve each other's
// real child processes under real CPU contention.
//
// The fix that actually holds (verified locally and on the real VPS): run
// the three heavy files sequentially among themselves
// (`--test-concurrency=1`), then run every other file (which spawns nothing
// extra) at node --test's own normal, unrestricted default - those have no
// hidden concurrency cost and lose nothing by staying fast.
//
// Written in plain Node rather than a shell script (bash/sh, arrays, etc.)
// deliberately: this project's own test container is node:24-alpine, which
// has no `bash` at all (confirmed for real - `sh: bash: not found` when a
// prior version of this file tried to run as a bash script inside it) and a
// POSIX-sh rewrite would still depend on assumptions about the shell that
// Node itself never needs to make. This file's only real dependency is the
// same `node` binary already running it.
//
// This is the SOURCE OF TRUTH for how this suite's tests are run - both
// `npm test` and every delivery/CI script call this file, so the safe
// invocation can never drift out of sync with what actually gets run.

import { spawnSync } from 'node:child_process';
import { readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const TEST_DIR = path.join(__dirname, '..', 'src', '__tests__');

const HEAVY = [
  'dual_instance_chaos.test.js',
  'outbound_chaos.test.js',
  'p06_operational_readiness.test.js',
];

// Build OTHER = every src/__tests__/*.test.js NOT in HEAVY, without assuming
// any file beyond what's actually on disk right now (H1: never guess the
// file list).
const all = readdirSync(TEST_DIR).filter((f) => f.endsWith('.test.js'));

for (const h of HEAVY) {
  if (!all.includes(h)) {
    console.error(`ERROR: expected heavy test file missing: ${h} (has it been renamed/removed? update HEAVY in this script)`);
    process.exit(1);
  }
}

const other = all.filter((f) => !HEAVY.includes(f));

function runNodeTest(label, files, { concurrency } = {}) {
  console.log(`\n=== ${label}: ${files.join(', ')} ===`);
  const args = ['--test'];
  if (concurrency !== undefined) args.push(`--test-concurrency=${concurrency}`);
  args.push(...files.map((f) => path.join(TEST_DIR, f)));
  const result = spawnSync(process.execPath, args, { stdio: 'inherit' });
  if (result.error) {
    console.error(`ERROR: failed to run node --test for [${label}]: ${result.error.message}`);
    return 1;
  }
  return result.status ?? 1;
}

const heavyStatus = runNodeTest(
  '[1/2] Heavy real-child-process tests, sequential (--test-concurrency=1)',
  HEAVY,
  { concurrency: 1 },
);

const otherStatus = runNodeTest('[2/2] Every other test file, default concurrency', other);

process.exit(heavyStatus !== 0 ? heavyStatus : otherStatus);
