// scripts/check_env.mjs
// Validates secrets in .env BEFORE `docker compose up` (audit B6 / H5).
//
// Why: `.env.example` ships with stub values (`change-me`) that `${VAR:?}` alone
// would accept — someone copying it as-is would start the system with well-known
// passwords. This gate rejects, with exit code 1:
//   - stub values such as `change-me`;
//   - passwords shorter than 24 characters;
//   - a password reused across two roles.
//
// Run: node scripts/check_env.mjs [path-to-env]   (defaults to ./.env)

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');

const PASSWORD_KEYS = [
  'POSTGRES_PASSWORD',
  'SHARWA_APP_PASSWORD',
  'SHARWA_SYSTEM_PASSWORD',
  'SHARWA_MIGRATION_PASSWORD',
  'REDIS_DURABLE_PASSWORD',
  'REDIS_CACHE_PASSWORD',
];

const MIN_PASSWORD_LENGTH = 24;

// Stub/demo values that must never reach a running system.
const STUB_VALUES = new Set([
  'change-me', 'changeme', 'change_me', 'replace-me', 'replace_me',
  'password', 'secret', 'x',
]);

/** Parse KEY=VALUE lines (skips blanks and # comments, strips surrounding quotes). */
export function parseEnv(text) {
  const entries = {};
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    let value = trimmed.slice(eq + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    entries[key] = value;
  }
  return entries;
}

/** @returns {string[]} list of problems (empty = valid). */
export function checkEnv(entries) {
  const errors = [];
  const seen = new Map();

  for (const key of PASSWORD_KEYS) {
    const value = entries[key];
    if (value === undefined || value === '') {
      errors.push(`${key}: missing or empty`);
      continue;
    }
    if (STUB_VALUES.has(value.toLowerCase())) {
      errors.push(`${key}: stub value "${value}" is not allowed`);
    }
    if (value.length < MIN_PASSWORD_LENGTH) {
      errors.push(`${key}: too short (${value.length} chars, minimum ${MIN_PASSWORD_LENGTH})`);
    }
    if (seen.has(value)) {
      errors.push(`${key}: password reused with ${seen.get(value)}`);
    }
    seen.set(value, key);
  }

  return errors;
}

function main() {
  const envPath = process.argv[2] ? path.resolve(process.argv[2]) : path.join(ROOT, '.env');
  let text;
  try {
    text = fs.readFileSync(envPath, 'utf8');
  } catch {
    console.error(`check_env: cannot read ${envPath}`);
    process.exitCode = 1;
    return;
  }

  const errors = checkEnv(parseEnv(text));
  if (errors.length === 0) {
    console.error('check_env: OK');
    process.exitCode = 0;
    return;
  }
  for (const e of errors) console.error(`check_env: ${e}`);
  console.error(`check_env: FAILED (${errors.length} problem(s))`);
  process.exitCode = 1;
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isMain) {
  main();
}
