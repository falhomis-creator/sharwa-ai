#!/usr/bin/env bash
set -euo pipefail
cd /home/sharwa/sharwa_ai

echo "=== 1) gateway/Dockerfile — own /app/auth_sessions as `app` BEFORE the VOLUME instruction ==="
python3 - <<'PYEOF'
path = "gateway/Dockerfile"
with open(path, "r", encoding="utf-8") as f:
    src = f.read()

old = """# Persistent WhatsApp credentials (auth_sessions/) survive container restarts.
VOLUME ["/app/auth_sessions"]"""
assert old in src, "VOLUME block not found verbatim - aborting"

new = """# Persistent WhatsApp credentials (auth_sessions/) survive container restarts.
# The directory must exist and be owned by the non-root `app` user BEFORE the
# VOLUME instruction: Docker seeds a fresh named volume by copying this
# directory's content AND ownership from the image the first time a container
# uses it. Without this, a fresh volume comes back root:root and `app` (USER
# app, below) gets EACCES on its very first mkdir under it (observed on real
# Docker: "EACCES: permission denied, mkdir '/app/auth_sessions/<id>'").
RUN mkdir -p /app/auth_sessions && chown -R app:app /app/auth_sessions
VOLUME ["/app/auth_sessions"]"""

src = src.replace(old, new)
with open(path, "w", encoding="utf-8") as f:
    f.write(src)
print("Dockerfile patched OK")
PYEOF

echo "=== 2) rebuild the image (bakes the corrected ownership for any FUTURE fresh volume) ==="
docker compose build gateway

echo "=== 3) fix the ALREADY-EXISTING gateway_auth_sessions volume's ownership (the rebuild above does not touch it - a named volume's content persists across image rebuilds) ==="
docker compose run --rm --user root --entrypoint sh gateway -c "chown -R app:app /app/auth_sessions && ls -la /app/auth_sessions"

echo "=== 4) restart gateway (and forwarder, same image) with the fixed image ==="
docker compose up -d gateway gateway-forwarder
sleep 3
docker compose ps gateway gateway-forwarder

echo "=== 5) retry creating a real session ==="
source .env
curl -s -X POST http://127.0.0.1:4001/sessions \
  -H "X-API-Key: $SHARWA_AI_GATEWAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"p02test1"}'
echo ""
echo "--- gateway logs (should show no EACCES this time) ---"
docker compose logs --tail=20 gateway

echo "=== 6) hunt_gate + git status (Dockerfile is a tracked file, review before commit) ==="
set +e
node scripts/hunt_gate.mjs
HUNT_EXIT=$?
set -e
echo "hunt_gate exit: $HUNT_EXIT"
git status
git diff --stat
