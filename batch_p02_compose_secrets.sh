#!/usr/bin/env bash
set -euo pipefail
cd /home/sharwa/sharwa_ai

echo "=== 1) scripts/gen_secrets.mjs — add the two gateway secrets to TEMPLATE (future re-generation only) ==="
python3 - <<'PYEOF'
path = "scripts/gen_secrets.mjs"
with open(path, "r", encoding="utf-8") as f:
    src = f.read()

old = "  ['REDIS_DURABLE_PASSWORD', randomSecret],\n  ['REDIS_CACHE_PASSWORD', randomSecret],\n];"
assert old in src, "TEMPLATE tail not found verbatim - aborting"
new = ("  ['REDIS_DURABLE_PASSWORD', randomSecret],\n"
       "  ['REDIS_CACHE_PASSWORD', randomSecret],\n"
       "  // P0.2 (R3_DIRECTIVE): gateway app tier (docker-compose.yml gateway/gateway-forwarder).\n"
       "  ['SHARWA_AI_GATEWAY_API_KEY', randomSecret],\n"
       "  ['SHARWA_AI_GATEWAY_WEBHOOK_SECRET', randomSecret],\n"
       "];")
src = src.replace(old, new)
with open(path, "w", encoding="utf-8") as f:
    f.write(src)
print("gen_secrets.mjs patched OK")
PYEOF

echo "=== 2) append SHARWA_AI_GATEWAY_API_KEY / SHARWA_AI_GATEWAY_WEBHOOK_SECRET to the REAL .env (idempotent, never overwrites existing keys) ==="
node - <<'NODEEOF'
import crypto from 'node:crypto';
import fs from 'node:fs';

const ENV_PATH = '.env';
const randomSecret = () => crypto.randomBytes(24).toString('base64url');

let content = fs.existsSync(ENV_PATH) ? fs.readFileSync(ENV_PATH, 'utf8') : '';
const hasKey = (name) => new RegExp(`^${name}=`, 'm').test(content);

const toAdd = [];
for (const name of ['SHARWA_AI_GATEWAY_API_KEY', 'SHARWA_AI_GATEWAY_WEBHOOK_SECRET']) {
  if (hasKey(name)) {
    console.log(`.env: ${name} already present - leaving it untouched`);
  } else {
    toAdd.push(`${name}=${randomSecret()}`);
  }
}

if (toAdd.length > 0) {
  const sep = content.endsWith('\n') || content === '' ? '' : '\n';
  fs.appendFileSync(ENV_PATH, sep + toAdd.map((l) => l).join('\n') + '\n');
  console.log(`.env: appended ${toAdd.length} new key(s)`);
} else {
  console.log('.env: nothing to append');
}
NODEEOF

echo "=== 3) docker-compose.yml — MinIO creds become OPTIONAL (empty default), not hard-required ==="
echo "    (owner decision, this session: MinIO deployment status unknown - sessions.js already"
echo "     degrades a media upload gracefully (object_key: null, fallback text) when MinIO is"
echo "     unreachable/misconfigured, so the gateway should still be able to START and handle"
echo "     text messages without an unrelated external dependency blocking it at compose-parse time.)"
python3 - <<'PYEOF'
path = "docker-compose.yml"
with open(path, "r", encoding="utf-8") as f:
    src = f.read()

old = """      MINIO_ACCESS_KEY: ${MINIO_ACCESS_KEY:?MINIO_ACCESS_KEY is required (see .env)}
      MINIO_SECRET_KEY: ${MINIO_SECRET_KEY:?MINIO_SECRET_KEY is required (see .env)}"""
assert old in src, "MinIO required-vars block not found verbatim - aborting"
new = """      # Optional (owner decision, P0.2): MinIO deployment status was unknown when
      # this was written. Left EMPTY here rather than :?required so `gateway` can
      # still start and handle text messages - sessions.js already degrades a
      # media upload gracefully (object_key: null, Arabic fallback text) when
      # these are unset/wrong. Set real values in .env once MinIO is confirmed.
      MINIO_ACCESS_KEY: ${MINIO_ACCESS_KEY:-}
      MINIO_SECRET_KEY: ${MINIO_SECRET_KEY:-}"""
src = src.replace(old, new)
with open(path, "w", encoding="utf-8") as f:
    f.write(src)
print("docker-compose.yml patched OK (MinIO creds now optional)")
PYEOF

echo "=== 4) .env.example — reflect that MinIO creds are now optional ==="
python3 - <<'PYEOF'
path = ".env.example"
with open(path, "r", encoding="utf-8") as f:
    src = f.read()

old = """# --- Gateway app tier (docker-compose.yml gateway / gateway-forwarder, P0.2) ---
SHARWA_AI_GATEWAY_API_KEY=change-me
SHARWA_AI_GATEWAY_WEBHOOK_SECRET=change-me
MINIO_ACCESS_KEY=change-me
MINIO_SECRET_KEY=change-me
# Optional - docker-compose.yml already defaults these for the common case
# (Django/MinIO running on this same VPS host, outside compose). Uncomment
# to override:
# DJANGO_BASE_URL=http://host.docker.internal:8000
# MINIO_ENDPOINT_URL=http://host.docker.internal:9000
# MINIO_BUCKET_NAME=sharwa-ai
# MINIO_USE_SSL=false"""
assert old in src, ".env.example gateway section not found verbatim - aborting"
new = """# --- Gateway app tier (docker-compose.yml gateway / gateway-forwarder, P0.2) ---
SHARWA_AI_GATEWAY_API_KEY=change-me
SHARWA_AI_GATEWAY_WEBHOOK_SECRET=change-me
# MinIO - OPTIONAL. Unset/wrong values are fine for now: gateway still starts
# and handles text messages; media uploads degrade gracefully (object_key:
# null, fallback text) until MinIO is confirmed and these are set for real.
MINIO_ACCESS_KEY=
MINIO_SECRET_KEY=
# Optional - docker-compose.yml already defaults these for the common case
# (Django/MinIO running on this same VPS host, outside compose). Uncomment
# to override:
# DJANGO_BASE_URL=http://host.docker.internal:8000
# MINIO_ENDPOINT_URL=http://host.docker.internal:9000
# MINIO_BUCKET_NAME=sharwa-ai
# MINIO_USE_SSL=false"""
src = src.replace(old, new)
with open(path, "w", encoding="utf-8") as f:
    f.write(src)
print(".env.example patched OK")
PYEOF

echo "=== 5) validate: docker compose config must now parse cleanly (SHARWA_AI_GATEWAY_* present in .env; MinIO no longer required) ==="
set +e
docker compose config >/tmp/compose_config_resolved.yml 2>&1
CONFIG_EXIT=$?
set -e
echo "docker compose config exit: $CONFIG_EXIT"
if [ "$CONFIG_EXIT" -ne 0 ]; then
  echo "--- docker compose config output (error) ---"
  cat /tmp/compose_config_resolved.yml
else
  echo "docker compose config: OK"
  grep -A 30 "^  gateway:" /tmp/compose_config_resolved.yml | head -40
fi

echo "=== 6) git status / diff ==="
git status
git diff --stat
