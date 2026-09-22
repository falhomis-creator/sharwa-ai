// gateway/src/ingest/spool.js
// Disk spool: the durable fallback when redis-durable is unreachable (disaster
// #5/#17). When an XADD cannot be confirmed durable, the entry is appended to a
// JSONL file so no message is ever silently dropped (F2). A drainer later
// replays the file through the normal WAL path.
//
// Replay is idempotent by construction: each spooled record keeps its
// provider_message_id, so a replayed entry that already made it to the stream is
// recognized as a duplicate by the fused dedupe+XADD and skipped. A crash between
// "replayed" and "file rewritten" therefore can never double-deliver.
//
// H4: the spool is bounded by spoolMaxMb. Past the cap, spooling throws
// SPOOL_FULL rather than growing without limit.

import { appendFile, readFile, writeFile, mkdir, stat, rename } from 'node:fs/promises';
import path from 'node:path';

import { appendEvent } from './wal.js';
import { config } from '../config.js';

const SPOOL_FILE = 'spool.ndjson';

/**
 * Append one record to the spool.
 *
 * @param {{ sessionId: string, event: object }} record
 * @param {{ spoolDir?: string, spoolMaxMb?: number }} [opts]
 */
export async function spool(record, opts = {}) {
  const dir = opts.spoolDir ?? config.spoolDir;
  const maxMb = opts.spoolMaxMb ?? config.spoolMaxMb;
  const file = path.join(dir, SPOOL_FILE);
  await mkdir(dir, { recursive: true });

  let size = 0;
  try {
    size = (await stat(file)).size;
  } catch {
    // no spool file yet — start at 0.
  }
  if (size >= maxMb * 1024 * 1024) {
    const err = new Error(`spool: ${file} is at cap (${maxMb}MB); refusing to grow (H4)`);
    err.code = 'SPOOL_FULL';
    throw err;
  }

  // On-disk shape mirrors the WAL entry: `{ session_id, event }`.
  await appendFile(file, JSON.stringify({ session_id: record.sessionId, event: record.event }) + '\n', 'utf8');
}

/**
 * Replay every spooled record through the WAL, removing the ones that land.
 *
 * @param {import('ioredis').Redis} client
 * @param {{ spoolDir?: string }} [opts]
 * @returns {Promise<{ replayed: number, remaining: number, dropped: number }>}
 */
export async function drainSpool(client, opts = {}) {
  const dir = opts.spoolDir ?? config.spoolDir;
  const file = path.join(dir, SPOOL_FILE);

  let raw = '';
  try {
    raw = await readFile(file, 'utf8');
  } catch {
    return { replayed: 0, remaining: 0, dropped: 0 }; // nothing spooled
  }

  const lines = raw.split('\n').filter((l) => l.trim() !== '');
  const remaining = [];
  let replayed = 0;
  let dropped = 0;
  let redisDown = false;

  for (const line of lines) {
    if (redisDown) {
      remaining.push(line);
      continue;
    }
    let rec;
    try {
      rec = JSON.parse(line);
    } catch {
      dropped += 1; // corrupt line: unrecoverable, drop (logged by caller)
      continue;
    }
    try {
      await appendEvent(client, { sessionId: rec.session_id, event: rec.event });
      replayed += 1;
    } catch (err) {
      if (err.code === 'PAYLOAD_TOO_LARGE') {
        dropped += 1; // permanent: it can never fit the 64KB cap
        continue;
      }
      // Transient (redis still down, etc.): keep this line and the rest, stop.
      remaining.push(line);
      redisDown = true;
    }
  }

  // Atomic rewrite so a crash mid-drain cannot truncate pending entries.
  const tmp = `${file}.tmp`;
  if (remaining.length === 0) {
    await writeFile(tmp, '', 'utf8');
  } else {
    await writeFile(tmp, `${remaining.join('\n')}\n`, 'utf8');
  }
  await rename(tmp, file);

  return { replayed, remaining: remaining.length, dropped };
}
