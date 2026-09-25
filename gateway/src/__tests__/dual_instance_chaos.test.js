import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// P0.5 official acceptance test (G7, spec literal): "نسختا بوابة على الجلسة
// نفسها ⇒ واحدة فقط تشغّلها؛ قتل الحاملة ⇒ الأخرى تستحوذ بعد انتهاء المهلة،
// والكاتب القديم مرفوض بالـfencing" - two gateway instances on the SAME
// session -> only one runs it; kill the carrier -> the other takes over
// after the lease timeout; the old writer is rejected by fencing.
//
// Two REAL child processes (never simulated in-process - same discipline as
// P0.4's own kill -9 chaos test), each a real gateway process pointed at a
// SHARED auth_sessions directory (simulating the real `gateway_auth_sessions`
// docker volume both real instances mount) and the SAME real redis-durable.
// Fencing itself (a stale token's renew/release being rejected) is unit-
// tested directly and more cheaply in lease.test.js; this test proves the
// end-to-end failover a unit test cannot: a real SIGKILL of the actual
// holder process, observed through the real public API only.

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CHILD_SCRIPT = path.join(__dirname, '_dual_instance_chaos_child.mjs');

// Shortened from the real 30000ms/10000ms/5000ms defaults - documented,
// legitimate config knobs (config.js), just tuned down for test wall-clock
// speed, not a different mechanism. LEASE_SWEEP_MS cannot go below 1000ms:
// config.js itself enforces that floor (a real production safeguard against
// hammering Redis with a sweep scan) and a child process that violates it
// fails to even boot - found by actually running this test for the first
// time, not guessed.
const LEASE_TTL_MS = 1500;
const LEASE_RENEW_MS = 400;
const LEASE_SWEEP_MS = 1000;

function spawnChild(instanceId, sessionId, authDir) {
  const child = spawn('node', [CHILD_SCRIPT, sessionId], {
    env: {
      ...process.env,
      INSTANCE_ID: instanceId,
      AUTH_SESSIONS_DIR: authDir,
      LEASE_TTL_MS: String(LEASE_TTL_MS),
      LEASE_RENEW_MS: String(LEASE_RENEW_MS),
      LEASE_SWEEP_MS: String(LEASE_SWEEP_MS),
      WA_DRIVER: 'fake',
      NODE_ENV: 'test',
      ALLOW_FAKE_WA: '1',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const lines = [];
  let latestHolder = false;
  child.stdout.on('data', (buf) => {
    for (const line of buf.toString('utf8').split('\n')) {
      if (!line) continue;
      lines.push(line);
      const m = line.match(/^TICK instance=\S+ holder=(true|false)/);
      if (m) latestHolder = m[1] === 'true';
    }
  });
  const stderrLines = [];
  child.stderr.on('data', (buf) => stderrLines.push(buf.toString('utf8')));
  return { child, lines, stderrLines, isHolder: () => latestHolder };
}

async function waitUntil(cond, timeoutMs, label) {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > timeoutMs) throw new Error(`waitUntil timed out: ${label}`);
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

test('(P0.5 dual-instance) two real gateway processes on the same session: only one holds the lease; kill it -> the other takes over after the TTL', async () => {
  const sessionId = `dual-instance-${crypto.randomUUID()}`;
  const tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'p05-dual-'));

  // Pre-register the one session both children will race for - same real
  // useMultiFileAuthState technique as rehydrate_staging.test.js.
  const { useMultiFileAuthState } = await import('../driver/waDriver.js');
  const dir = path.join(tmpDir, sessionId);
  await fs.mkdir(dir, { recursive: true });
  const { state, saveCreds } = await useMultiFileAuthState(dir);
  state.creds.registered = true;
  await saveCreds();

  const a = spawnChild('instance-a', sessionId, tmpDir);
  const b = spawnChild('instance-b', sessionId, tmpDir);

  try {
    // Both children run rehydrateSessions() + a lease sweep loop; within a
    // couple of sweep/renew cycles exactly one must report itself the
    // holder.
    //
    // P0.5 timing fix (found on the real VPS, not guessed): this test spawns
    // two REAL OS child processes (node boot + ESM module resolution + a
    // real Redis round trip each). Run in isolation that easily lands well
    // under a second; run as part of the FULL suite - where `node --test`'s
    // own default concurrency runs many other test files at the same time -
    // real CPU contention on a modest VPS pushed this past the original
    // 5000ms margin and failed the test, even though the lease/fencing
    // mechanism itself was never in question (lease.test.js's 6 unit tests,
    // and this same test run in isolation, both passed).
    //
    // P0.6 finding (F10, also real-VPS-observed, not guessed): 20000ms - which
    // held on the VPS's P0.5 full-suite run - was hit again (timed out at
    // exactly 20000ms) once P0.6's own real-process tests
    // (p06_operational_readiness.test.js) were added to the same suite: each
    // node --test file is scheduled as one unit of the runner's own
    // CPU-aware concurrency, but a file that ITSELF spawns 2-3 more real OS
    // processes (as this test, and now p06_operational_readiness.test.js,
    // both do) is invisible to that accounting - more heavy tests than node
    // --test's own scheduler ever planned for. The real, durable fix
    // (docs/P0_FINDINGS.md F10) is capping `--test-concurrency` in the
    // suite's own run command, not an ever-growing timeout here; 40000ms is
    // kept as a second line of defense on top of that cap, not the primary
    // fix.
    await waitUntil(() => a.isHolder() || b.isHolder(), 40000, 'initial lease acquisition by either instance');
    assert.notEqual(a.isHolder(), b.isHolder(), 'exactly one instance must hold the lease, never both, never neither');

    const [carrier, survivor] = a.isHolder() ? [a, b] : [b, a];
    assert.equal(survivor.isHolder(), false, 'the non-carrier must not also claim to hold the lease');

    // Kill -9 the carrier - no SIGTERM, no graceful release, exactly the
    // spec's "قتل الحاملة" (kill the carrier).
    carrier.child.kill('SIGKILL');

    // The survivor must take over once the dead carrier's lease expires
    // (LEASE_TTL_MS) and its own next sweep tick (LEASE_SWEEP_MS) runs -
    // give it real margin above that sum (widened alongside the initial-
    // acquisition wait above; F10, same reasoning - see the comment there).
    await waitUntil(() => survivor.isHolder(), LEASE_TTL_MS + LEASE_SWEEP_MS + 20000, 'failover to the surviving instance');
    assert.equal(survivor.isHolder(), true, 'the surviving instance must have taken over the lease after the TTL');
  } finally {
    for (const c of [a.child, b.child]) {
      if (!c.killed) {
        try {
          c.kill('SIGTERM');
        } catch (err) {
          // Already exited between the .killed check and kill() - a real,
          // benign race in cleanup code, not silently swallowed.
          process.stderr.write(`CLEANUP_KILL_RACE ${err.message}\n`);
        }
      }
    }
    await Promise.all([a.child, b.child].map((c) => new Promise((resolve) => {
      if (c.exitCode !== null || c.signalCode !== null) return resolve();
      c.once('exit', resolve);
      setTimeout(resolve, 2000); // don't hang the suite on a stuck child
    })));
    await fs.rm(tmpDir, { recursive: true, force: true }).catch((err) => {
      process.stderr.write(`TMPDIR_CLEANUP_FAILED ${err.message}\n`);
    });
  }
});
