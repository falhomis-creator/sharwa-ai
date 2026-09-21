import crypto from 'node:crypto';

// ---------------------------------------------------------------------------
// Signed outbound webhooks (gateway -> Django).
//
// Constitution notes:
//  - The signing secret SHARWA_AI_GATEWAY_WEBHOOK_SECRET is completely
//    independent from any other secret used by any other Sharwa project.
//  - Signature scheme: HMAC-SHA256("{timestamp}." + rawBody), sent via the
//    X-Timestamp and X-Signature headers (exact names).
//  - Delivery never fails silently: retries use exponential backoff and the
//    final give-up is always logged loudly.
// ---------------------------------------------------------------------------

const MAX_ATTEMPTS = 5;
const BASE_DELAY_MS = 500;
const TIMEOUT_MS = 10_000;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Sign a raw JSON body with the shared webhook secret.
 * Deterministic: same (body, timestamp, secret) always yields the same HMAC.
 *
 * @param {string} rawBody    The exact JSON string that will be sent.
 * @param {string} timestamp  Unix epoch seconds, as a string.
 * @returns {string}          Lowercase hex HMAC-SHA256 digest.
 */
export function sign(rawBody, timestamp) {
  const secret = process.env.SHARWA_AI_GATEWAY_WEBHOOK_SECRET;
  if (!secret) {
    throw new Error('SHARWA_AI_GATEWAY_WEBHOOK_SECRET is not set');
  }
  const payload = `${timestamp}.${rawBody}`;
  return crypto.createHmac('sha256', secret).update(payload).digest('hex');
}

/**
 * Resolve and validate the Django base URL.
 * There is intentionally no public default: if missing we log loudly and
 * refuse to attempt delivery (fail loud, never fail silent).
 *
 * @returns {string|null}
 */
function getBaseUrl() {
  const base = process.env.DJANGO_BASE_URL;
  if (!base) {
    console.error('[webhook] DJANGO_BASE_URL is not set; cannot deliver webhook events. Skipping delivery.');
    return null;
  }
  return base.replace(/\/+$/, '');
}

/**
 * POST a signed JSON payload with exponential backoff (up to MAX_ATTEMPTS).
 * Every final failure is logged clearly - no event is ever dropped silently.
 *
 * @param {string} path  e.g. '/webhooks/sharwa-ai/session-status/'
 * @param {object} body  JSON-serializable payload
 * @returns {Promise<Response|null>}
 */
async function post(path, body) {
  const baseUrl = getBaseUrl();
  if (!baseUrl) return null;

  let rawBody;
  let timestamp;
  let signature;
  try {
    rawBody = JSON.stringify(body);
    timestamp = Math.floor(Date.now() / 1000).toString();
    signature = sign(rawBody, timestamp);
  } catch (err) {
    console.error(`[webhook] cannot sign request: ${err.message}`);
    return null;
  }

  let lastError;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
    try {
      const response = await fetch(`${baseUrl}${path}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Timestamp': timestamp,
          'X-Signature': signature,
        },
        body: rawBody,
        signal: AbortSignal.timeout(TIMEOUT_MS),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status} ${response.statusText}`);
      }
      return response;
    } catch (err) {
      lastError = err;
      if (attempt < MAX_ATTEMPTS) {
        const delay = BASE_DELAY_MS * 2 ** (attempt - 1); // 500, 1000, 2000, 4000
        console.error(`[webhook] POST ${path} attempt ${attempt}/${MAX_ATTEMPTS} failed (${err.message}); retrying in ${delay}ms`);
        await sleep(delay);
      }
    }
  }

  console.error(`[webhook] POST ${path} gave up after ${MAX_ATTEMPTS} attempts (last error: ${lastError.message})`);
  return null;
}

// [مطابقة العقد — مسار الـwebhook]: Django يسجّل هذا المسار في config/urls_public.py
// على الشكل `/webhooks/sharwa-ai/session-status/` (بلا `/api/`، ومع شرطة مائلة ختامية).
//
// [ملاحظة مقصودة — اسم الحقل `phone_number`]: هذا الـwebhook يرسل `phone_number`
// (لا `connected_phone_number`) عمداً. دالة `sharwa_ai_session_status_webhook` من
// جانب Django لا تقرأ `phone_number` من جسم هذا الـwebhook إطلاقاً — رقم الهاتف
// المتصل يصل إلى Django حصراً عبر `GET /sessions/:id/status` (انظر sessions.js)،
// لا عبر هذا الـwebhook. لا تغيّر هذا الحقل.
export function postSessionStatus({ session_id, status, phone_number, detail }) {
  return post('/webhooks/sharwa-ai/session-status/', {
    session_id,
    status,
    phone_number: phone_number ?? null,
    detail: detail ?? null,
  });
}

export function postInboundMessage({
  session_id,
  from,
  text,
  message_id,
  media_object_key = null,
  media_type = null,
}) {
  // [إصلاح تطابق العقد text/message_text]: Django يقرأ الحقل باسم `message_text`
  // (tenants/webhooks_ai.py::sharwa_ai_inbound_message_webhook)، بينما كانت البوابة
  // ترسله باسم `text` — فكان نص أي رسالة عميل حقيقية يضيع قبل وصوله لمحرك الرد
  // الآلي منذ المرحلة 3أ. نرسله الآن بالاسم الصحيح.
  // [المرحلة 4 — الوسائط]: media_object_key/media_type يُمرَّران كما هما من
  // sessions.js (قد يكونان null لرسالة نصية عادية). Django يقرؤهما ويُوسّع بهما
  // عقد incoming_whatsapp_message.
  return post('/webhooks/sharwa-ai/inbound-message/', {
    session_id,
    from,
    message_text: text,
    message_id,
    media_object_key,
    media_type,
  });
}
