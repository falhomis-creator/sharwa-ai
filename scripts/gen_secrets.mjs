// scripts/gen_secrets.mjs
// Optional secret generator (audit B6): writes a .env with strong random
// passwords. Pair with scripts/check_env.mjs, which must pass before `docker
// compose up`.
//
// Run: node scripts/gen_secrets.mjs [outPath] [--force]
//   - defaults to ./.env; refuses to overwrite an existing file unless --force.

import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');

function randomSecret() {
  // 24 random bytes -> 32 URL-safe base64 characters (>= 24 chars).
  return crypto.randomBytes(24).toString('base64url');
}

// [key, valueFactory] — user names are fixed, passwords are random.
const TEMPLATE = [
  ['POSTGRES_USER', () => 'sharwa_admin'],
  ['POSTGRES_PASSWORD', randomSecret],
  ['POSTGRES_DB', () => 'sharwa_ai'],
  ['SHARWA_APP_USER', () => 'sharwa_app_login'],
  ['SHARWA_APP_PASSWORD', randomSecret],
  ['SHARWA_SYSTEM_USER', () => 'sharwa_system_login'],
  ['SHARWA_SYSTEM_PASSWORD', randomSecret],
  ['SHARWA_MIGRATION_USER', () => 'sharwa_migration'],
  ['SHARWA_MIGRATION_PASSWORD', randomSecret],
  ['REDIS_DURABLE_PASSWORD', randomSecret],
  ['REDIS_CACHE_PASSWORD', randomSecret],
];

function main() {
  const args = process.argv.slice(2);
  const force = args.includes('--force');
  const outArg = args.find((a) => !a.startsWith('--'));
  const outPath = outArg ? path.resolve(outArg) : path.join(ROOT, '.env');

  if (fs.existsSync(outPath) && !force) {
    console.error(`gen_secrets: ${outPath} already exists (use --force to overwrite)`);
    process.exitCode = 1;
    return;
  }

  const lines = TEMPLATE.map(([key, factory]) => `${key}=${factory()}`);
  fs.writeFileSync(outPath, lines.join('\n') + '\n');
  console.error(`gen_secrets: wrote ${outPath}`);
  process.exitCode = 0;
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isMain) {
  main();
}
