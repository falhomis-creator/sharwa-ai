// Child process for the P0.5 dual-instance lease/fencing chaos test (parent:
// dual_instance_chaos.test.js). Args: [sessionId]. Env (set by the parent):
// INSTANCE_ID, AUTH_SESSIONS_DIR (shared with the sibling child - simulates
// the real `gateway_auth_sessions` docker volume both real instances would
// share), LEASE_TTL_MS/LEASE_RENEW_MS/LEASE_SWEEP_MS (shortened for test
// speed - same knobs config.js documents, just smaller real values), plus
// the usual WA_DRIVER=fake/NODE_ENV=test/ALLOW_FAKE_WA=1 triple gate.
//
// Repeatedly attempts to become (or stay) the lease holder for ONE
// pre-registered session via the real, public rehydrateSessions()/
// startLeaseSweep() API - no private helper is reached into. Prints one
// structured stdout line per tick so the parent can observe holder status
// over time without polling Redis itself.

process.env.ALLOW_FAKE_WA = '1';
process.env.NODE_ENV = 'test';

const [, , sessionId] = process.argv;

const sessions = await import('../sessions.js');
const { createRedisClient } = await import('../redis.js');

const redis = createRedisClient();
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
sessions.setRedisClient(redis);

process.stdout.write(`CHILD_READY pid=${process.pid} instance=${process.env.INSTANCE_ID}\n`);

// First attempt immediately (mirrors index.js's startup rehydrate), then keep
// sweeping on the same short interval a real instance would use, so this
// child can pick up the lease later if it does not win it right away.
await sessions.rehydrateSessions().catch((err) => {
  process.stderr.write(`REHYDRATE_ERROR ${err.message}\n`);
});
const sweep = sessions.startLeaseSweep();

const tick = setInterval(async () => {
  try {
    const health = await sessions.getSessionHealth(sessionId);
    const holder = health?.lease_holder ?? null;
    const isHolder = typeof holder === 'string' && holder.startsWith(`${process.env.INSTANCE_ID}:`);
    process.stdout.write(`TICK instance=${process.env.INSTANCE_ID} holder=${isHolder} lease_holder=${holder} state=${health?.state ?? 'null'}\n`);
  } catch (err) {
    process.stderr.write(`TICK_ERROR ${err.message}\n`);
  }
}, 200);
tick.unref?.();

process.on('SIGTERM', async () => {
  clearInterval(tick);
  sweep.stop();
  await sessions.releaseAllOwnedLeases().catch((err) => {
    process.stderr.write(`RELEASE_ON_SIGTERM_FAILED ${err.message}\n`);
  });
  await redis.quit().catch((err) => {
    process.stderr.write(`REDIS_QUIT_ON_SIGTERM_FAILED ${err.message}\n`);
  });
  process.exit(0);
});
