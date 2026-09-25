import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// P0.6 (G10, disasters 19/21 baseline) real acceptance tests. Two of the
// three real-process cases below (METRICS_TOKEN startup refusal, graceful
// shutdown within budget) genuinely need a real running gateway process -
// same discipline as dual_instance_chaos.test.js's real child processes
// (H7: no mocks). /readyz and /metrics contract behavior is tested directly
// against the real Express app (contract.test.js's own app.listen(0) pattern)
// since those do not need a full rehydrate/lease-sweep boot.
//
// F10 (real VPS finding, docs/P0_FINDINGS.md): this file's own real child
// processes add to the exact same full-suite CPU-contention effect
// dual_instance_chaos.test.js's F9 fix already documented - confirmed on the
// VPS the first time this file ran alongside it (this file's gateway boot
// did not become reachable within the original 10000ms margin under that
// combined load). The durable fix is capping the suite's own
// --test-concurrency; the margins below are widened as a second line of
// defense on top of that, not the primary fix.

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const INDEX_SCRIPT = path.join(__dirname, '..', 'index.js');

function spawnGateway(extraEnv) {
  return spawn('node', [INDEX_SCRIPT], {
    env: {
      ...process.env,
      PORT: '0', // overridden per-test below where a fixed port is needed
      WA_DRIVER: 'fake',
      NODE_ENV: 'test',
      ALLOW_FAKE_WA: '1',
      SHARWA_AI_GATEWAY_API_KEY: 'p06-test-key',
      REDIS_DURABLE_HOST: process.env.REDIS_DURABLE_HOST ?? '127.0.0.1',
      REDIS_DURABLE_PORT: process.env.REDIS_DURABLE_PORT ?? '6399',
      // pino defaults to 'silent' (logger.js) and writes to STDOUT, not
      // stderr - both matter for the assertions below, which need to see the
      // real fatal-startup log line.
      LOG_LEVEL: 'error',
      ...extraEnv,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
}

async function waitForExit(child, timeoutMs) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`process did not exit within ${timeoutMs}ms`)), timeoutMs);
    child.once('exit', (code, signal) => {
      clearTimeout(timer);
      resolve({ code, signal });
    });
  });
}

test('(P0.6) main() refuses to start with an empty/missing METRICS_TOKEN (H5)', async () => {
  const child = spawnGateway({ METRICS_TOKEN: '', PORT: '4501' });
  let stdout = '';
  let stderr = '';
  child.stdout.on('data', (buf) => { stdout += buf.toString('utf8'); });
  child.stderr.on('data', (buf) => { stderr += buf.toString('utf8'); });

  const { code } = await waitForExit(child, 20000);
  assert.notEqual(code, 0, 'process must exit non-zero when METRICS_TOKEN is empty');
  assert.match(stdout + stderr, /METRICS_TOKEN/, 'the fatal log must name METRICS_TOKEN as the reason (H3: fail loud, not silent)');
});

test('(P0.6) real process: /healthz stays up, /readyz becomes ready, /metrics requires the bearer token, then SIGTERM exits within the shutdown budget', async () => {
  const PORT = 4502;
  const METRICS_TOKEN = 'p06-metrics-secret';
  // DOWNLOAD_DRAIN_TIMEOUT_MS must stay < SHUTDOWN_TIMEOUT_MS (config.js
  // validation - found by actually running this test, not guessed): the
  // default 8000ms drain budget does not fit inside a 5000ms test-speed
  // shutdown budget, so it must be tightened here too.
  const child = spawnGateway({
    METRICS_TOKEN, PORT: String(PORT), SHUTDOWN_TIMEOUT_MS: '5000', DOWNLOAD_DRAIN_TIMEOUT_MS: '1000',
  });
  let stderr = '';
  child.stderr.on('data', (buf) => { stderr += buf.toString('utf8'); });

  try {
    // Wait for the real HTTP server to accept connections (poll /healthz -
    // the one frozen, dependency-free route - rather than a fixed sleep).
    const base = `http://127.0.0.1:${PORT}`;
    const start = Date.now();
    let up = false;
    while (Date.now() - start < 40000) {
      try {
        const r = await fetch(`${base}/healthz`);
        if (r.status === 200) { up = true; break; }
      } catch {
        // not listening yet
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    assert.ok(up, `gateway did not become reachable on :${PORT} within 40s (stderr: ${stderr})`);

    // /readyz: with no sessions and a real reachable redis-durable, must be
    // ready (200) - no guessing what "ready" should look like once the real
    // process has actually booted.
    const readyRes = await fetch(`${base}/readyz`);
    const readyBody = await readyRes.json();
    assert.equal(readyRes.status, 200, `expected 200 ready, got ${readyRes.status}: ${JSON.stringify(readyBody)}`);
    assert.equal(readyBody.status, 'ready');

    // /metrics: no token -> 401; correct bearer token -> 200 with real
    // Prometheus exposition text containing the default + P0.6 metric names.
    const noAuthRes = await fetch(`${base}/metrics`);
    assert.equal(noAuthRes.status, 401, '/metrics without a bearer token must be 401');

    const authedRes = await fetch(`${base}/metrics`, { headers: { Authorization: `Bearer ${METRICS_TOKEN}` } });
    assert.equal(authedRes.status, 200);
    const body = await authedRes.text();
    assert.match(body, /^# HELP process_resident_memory_bytes/m, 'must expose the exact spec-required process_resident_memory_bytes metric');
    assert.match(body, /^# HELP nodejs_heap_size_used_bytes/m, 'must expose the exact spec-required nodejs_heap_size_used_bytes metric');
    assert.match(body, /^# HELP session_state /m, 'must expose the P0.5/P0.6 session_state gauge');
    assert.match(body, /^# HELP lease_lost_total /m, 'must expose the P0.5/P0.6 lease_lost_total counter');

    const wrongAuthRes = await fetch(`${base}/metrics`, { headers: { Authorization: 'Bearer wrong-token' } });
    assert.equal(wrongAuthRes.status, 401, '/metrics with a wrong bearer token must be 401, not leak metrics');

    // Graceful shutdown (spec, literal: exit within <= 25s; SHUTDOWN_TIMEOUT_MS
    // is set to 5000 above for test speed - a real, tighter budget than
    // production's default 20000, exercising the same code path).
    child.kill('SIGTERM');
    const { code, signal } = await waitForExit(child, 20000);
    assert.equal(code, 0, `graceful shutdown must exit 0, got code=${code} signal=${signal} (stderr: ${stderr})`);
  } finally {
    if (!child.killed && child.exitCode === null) {
      try { child.kill('SIGKILL'); } catch { /* already exited */ }
    }
  }
});
