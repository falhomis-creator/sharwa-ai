// Child process for the P0.4 chaos acceptance test (parent: outbound_chaos.test.js).
//
// Runs ONE session's real recovery + worker cycle against real Redis. Every
// message that actually reaches the FakeWa socket's sendMessage() is recorded
// to a Redis LIST (`chaos:confirmed:{sid}`) synchronously, BEFORE
// sendMessage's own latency delay resolves — this is the durable, external
// proof that survives this process being SIGKILLed, since FakeWaSocket's own
// in-memory sentMessages array dies with the process (by design — it is not
// meant to be durable; the queue module is what's under test).
process.env.ALLOW_FAKE_WA = '1';
process.env.NODE_ENV = 'test';

import { recoverInflight, startOutboundWorker, queueDepth } from '../outbound/queue.js';
import { makeFakeWaSocket } from '../../test-support/fakeWaDriver.js';
import { createRedisClient } from '../redis.js';

const [, , sessionId] = process.argv;

// Real redis-durable (never a mock - H7), via the project's own connection
// helper - inherits the parent's env (REDIS_DURABLE_HOST/PORT), so it always
// points at the same Redis the parent test process used.
const redis = createRedisClient();

// createRedisClient() uses lazyConnect:false + enableOfflineQueue:false, so
// the very first command (recoverInflight below) must wait for the
// TCP+AUTH handshake to finish first (same ordering fix as forwarder.js).
function waitForReady(client) {
  if (client.status === 'ready') return Promise.resolve();
  return new Promise((resolve, reject) => {
    const onReady = () => { client.off('error', onError); resolve(); };
    const onError = (err) => { client.off('ready', onReady); reject(err); };
    client.once('ready', onReady);
    client.once('error', onError);
  });
}
await waitForReady(redis);

const sock = makeFakeWaSocket({ sendLatencyMs: 15 });
const realSend = sock.sendMessage.bind(sock);
sock.sendMessage = async (jid, content, options) => {
  // Record BEFORE awaiting the (latency-delayed) real send — this call must
  // land durably even if the process is killed a few ms later. Recorded by
  // wa_message_id (options.messageId), which queue.js always sets; the
  // parent correlates total confirmed sends against the 50 sent_marker keys
  // to compute the duplicate count (see outbound_chaos.test.js).
  await redis.rpush(`chaos:confirmed:${sessionId}`, JSON.stringify({ wa_message_id: options.messageId, at: Date.now(), pid: process.pid }));
  return realSend(jid, content, options);
};

const recovery = await recoverInflight(redis, sessionId);
process.stdout.write(`RECOVERY confirmedSent=${recovery.confirmedSent} requeued=${recovery.requeued}\n`);

const worker = startOutboundWorker(redis, sessionId, { getSocket: () => sock });

process.stdout.write(`CHILD_READY pid=${process.pid}\n`);

// Periodically report queue depth so the parent can see real progress and
// decide when/whether to kill mid-flight.
const reportTimer = setInterval(async () => {
  try {
    const depth = await queueDepth(redis, sessionId);
    const inflight = await redis.llen(`out:${sessionId}:inflight`);
    const confirmed = await redis.llen(`chaos:confirmed:${sessionId}`);
    process.stdout.write(`PROGRESS depth=${depth} inflight=${inflight} confirmed=${confirmed}\n`);
  } catch (err) {
    // Best-effort progress reporting only: the parent's chaos harness can
    // SIGKILL this process (or the Redis connection can drop) between any
    // two ticks, and a report-tick failure here carries no signal beyond
    // "skip this tick" - it must never crash the child mid-test. Not
    // silent, though (hunt_gate H1/H3): recorded on stderr so a real,
    // unexpected failure mode here is still visible in the test output.
    process.stderr.write(`PROGRESS_TICK_SKIPPED ${err?.message ?? err}\n`);
  }
}, 100);
reportTimer.unref?.();

process.on('SIGTERM', async () => {
  worker.stop();
  clearInterval(reportTimer);
  try {
    await redis.quit();
  } catch (err) {
    // Best-effort: we're already shutting down on SIGTERM and process.exit(0)
    // below runs unconditionally regardless of whether QUIT succeeded.
    // Recorded on stderr instead of silently swallowed (hunt_gate H1/H3).
    process.stderr.write(`REDIS_QUIT_ON_SIGTERM_FAILED ${err?.message ?? err}\n`);
  }
  process.exit(0);
});
