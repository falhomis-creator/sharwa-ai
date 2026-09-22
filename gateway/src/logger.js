// gateway/src/logger.js
// Single structured logger for the gateway (H12: no console.log in app code).
// Redacts the gateway API key defensively even though it never appears in a
// logged object today - cheap insurance if a future log call adds req/headers.

import pino from 'pino';

export const logger = pino({
  level: process.env.LOG_LEVEL || 'info',
  redact: {
    paths: ['req.headers["x-api-key"]', 'headers["x-api-key"]'],
    remove: true,
  },
});
