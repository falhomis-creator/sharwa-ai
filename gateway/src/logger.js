// gateway/src/logger.js
import pino from 'pino';

// P0.6/H5 ("أرقام الهواتف في السجلات مقنّعة - آخر 3 أرقام فقط ظاهرة"): phone
// numbers appear in log objects under many different field names across this
// codebase (phoneNumber, phone_number, connected_phone_number, phone_e164,
// `to` on an outbound send, the JID embedded in a webhook detail string, ...)
// - grepped, not guessed (see the P0.6 wiring pass). Enumerating every field
// name pino's own `redact` option needs a literal path for would be both
// incomplete today and silently stale the next time a new field is added.
// Instead this masks any digit run that LOOKS like a phone number, wherever
// it appears in the log object - conservatively, only sequences of 7-15
// digits (optionally `+`-prefixed), which covers real E.164 numbers while
// leaving short IDs/counts alone. This can still mask a non-phone numeric
// string that happens to fall in that range (a long internal id, say) - that
// false-positive direction is the deliberately-chosen safer failure mode for
// H5 (over-masking loses a little log readability; under-masking leaks a
// real phone number to disk).
const PHONE_RE = /\+?\d{7,15}/g;

function maskPhoneDigits(str) {
  return str.replace(PHONE_RE, (match) => {
    const digitsOnly = match.replace(/\D/g, '');
    const last3 = digitsOnly.slice(-3);
    const prefix = match.startsWith('+') ? '+' : '';
    return `${prefix}***${last3}`;
  });
}

// Bounded recursion (H4: no unbounded walk of an attacker/bug-shaped object).
const MAX_REDACT_DEPTH = 8;

function redactPhones(value, depth = 0) {
  if (depth > MAX_REDACT_DEPTH) return value;
  if (typeof value === 'string') return maskPhoneDigits(value);
  if (Array.isArray(value)) return value.map((v) => redactPhones(v, depth + 1));
  if (value && typeof value === 'object' && !(value instanceof Error)) {
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = redactPhones(v, depth + 1);
    return out;
  }
  return value;
}

export const logger = pino({
  level: process.env.LOG_LEVEL || 'silent',
  formatters: {
    // pino calls this with the merged log object right before serialization -
    // the one place that sees every field regardless of which call site
    // logged it, so this is where H5's masking is enforced, once, for the
    // whole process.
    log(mergedObject) {
      return redactPhones(mergedObject);
    },
  },
});

/**
 * H12 ("كل منظومة فرعية ... لها ... سجلات JSON منظّمة بحقول ثابتة"):
 * structured event log with the fixed field set every subsystem's log line
 * must carry - event, tenant_id, session_id, msg_id?, duration_ms, outcome.
 * Extra fields may be added (spread after the fixed set so a caller can never
 * accidentally clobber one of them), but these six are always present
 * (nullable ones default to null, never omitted, so every H12 log line has
 * the same shape for downstream parsing/alerting).
 *
 * @param {import('pino').Logger} log       Logger instance (usually the module-level `logger`, or a `.child(...)`).
 * @param {object} fields
 * @param {string} fields.event             Event name (e.g. 'session.reconnect', 'outbound.send').
 * @param {string|null} [fields.tenant_id]
 * @param {string|null} [fields.session_id]
 * @param {string|null} [fields.msg_id]
 * @param {number|null} [fields.duration_ms]
 * @param {string|null} [fields.outcome]     e.g. 'ok' | 'error' | 'blocked' | 'duplicate'.
 * @param {string} [message]                Human-readable message; defaults to `event`.
 */
export function logEvent(log, fields, message) {
  const { event, tenant_id = null, session_id = null, msg_id = null, duration_ms = null, outcome = null, ...extra } = fields;
  log.info({ event, tenant_id, session_id, msg_id, duration_ms, outcome, ...extra }, message ?? event);
}
