import test from 'node:test';
import assert from 'node:assert/strict';

import { checkEnv, parseEnv } from '../check_env.mjs';

function strongPasswords() {
  return {
    POSTGRES_PASSWORD: 'a'.repeat(24),
    SHARWA_APP_PASSWORD: 'b'.repeat(24),
    SHARWA_SYSTEM_PASSWORD: 'c'.repeat(24),
    SHARWA_MIGRATION_PASSWORD: 'd'.repeat(24),
    REDIS_DURABLE_PASSWORD: 'e'.repeat(24),
    REDIS_CACHE_PASSWORD: 'f'.repeat(24),
  };
}

test('checkEnv accepts strong, unique passwords', () => {
  assert.deepEqual(checkEnv(strongPasswords()), []);
});

test('checkEnv rejects the change-me stub value', () => {
  const e = strongPasswords();
  e.POSTGRES_PASSWORD = 'change-me';
  const errors = checkEnv(e);
  assert.ok(errors.some((msg) => msg.includes('POSTGRES_PASSWORD') && msg.includes('stub value')));
});

test('checkEnv rejects short passwords', () => {
  const e = strongPasswords();
  e.REDIS_DURABLE_PASSWORD = 'short';
  const errors = checkEnv(e);
  assert.ok(errors.some((msg) => msg.includes('REDIS_DURABLE_PASSWORD') && msg.includes('too short')));
});

test('checkEnv rejects a password reused across roles', () => {
  const e = strongPasswords();
  e.SHARWA_SYSTEM_PASSWORD = e.POSTGRES_PASSWORD;
  const errors = checkEnv(e);
  assert.ok(errors.some((msg) => msg.includes('reused')));
});

test('checkEnv rejects missing passwords', () => {
  const e = strongPasswords();
  delete e.SHARWA_MIGRATION_PASSWORD;
  const errors = checkEnv(e);
  assert.ok(errors.some((msg) => msg.includes('SHARWA_MIGRATION_PASSWORD') && msg.includes('missing')));
});

test('parseEnv parses KEY=VALUE and skips comments/blank lines, strips quotes', () => {
  const entries = parseEnv('# comment\nPOSTGRES_PASSWORD=abc\nREDIS_DURABLE_PASSWORD="quoted"\n\n');
  assert.equal(entries.POSTGRES_PASSWORD, 'abc');
  assert.equal(entries.REDIS_DURABLE_PASSWORD, 'quoted');
});
