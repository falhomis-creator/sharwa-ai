# P0.2 Dependencies (H11)

> H11 — every new dependency is justified, version-pinned, and audited before it
> reaches the gateway. This is the running ledger for P0.2.

## `ioredis@^5.11.1` — NEW

- **Why**: the gateway's durable tier is Redis Streams. The ingest path needs
  `XADD`, `XGROUP CREATE`, `XACK`, `XAUTOCLAIM`, `XTRIM`, and an atomic
  dedupe+XADD fused into one fsync'd write via Lua `EVAL` (owner decision #1).
  `ioredis` exposes all of these with a Promise API, per-command retry caps, and
  an offline queue that can be disabled — the exact "fail fast and spool" shape
  the ingest contract requires (see `src/redis.js`).
- **Why not `redis` (node-redis)**: it is not installed in the lockfile, and its
  v4/v5 API split means choosing it would add a second surface to review. One
  client library, one place to reason about error classification (H3).
- **Pin**: `^5.11.1` (installed `5.11.1`). `engines.node >= 22` — compatible.
- **Audit**: `npm audit` → `found 0 vulnerabilities` (2026-09-22).

## Pre-existing (unchanged by P0.2)

| Package | Version | Role |
| --- | --- | --- |
| `@whiskeysockets/baileys` | `6.7.24` (pinned) | WhatsApp protocol |
| `express` | `^4.21.2` | HTTP control plane |
| `@aws-sdk/client-s3` | `^3.1136.0` | MinIO object store (media; P0.3) |
| `qrcode` | `^1.5.4` | session QR rendering |

No dependency was upgraded or removed in P0.2; the only change is the addition of
`ioredis` to `gateway/package.json` and `gateway/package-lock.json`.
