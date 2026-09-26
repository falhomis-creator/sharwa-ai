#!/usr/bin/env bash
# Read-only reconnaissance for finishing P0.3 properly (real MinIO E2E per spec).
# Makes NO changes to the repo or to running containers. Just gathers exactly
# what's needed to write the MinIO E2E test harness without guessing at any
# existing contract (FakeWaDriver API, compose service names/networks, current
# media.js/config.js, existing docs sections).
set -euo pipefail
cd /home/sharwa/sharwa_ai

echo "=== A) repo root listing (top level + docs/) ==="
ls -la
echo "---"
ls -la docs/

echo "=== B) docker-compose files present ==="
ls -la docker-compose*.yml 2>/dev/null || echo "(none matched docker-compose*.yml at root)"

echo "=== C) docker-compose.yml (full) ==="
cat docker-compose.yml 2>/dev/null || echo "(no docker-compose.yml)"

echo "=== D) docker-compose.test.yml (full, if it exists) ==="
cat docker-compose.test.yml 2>/dev/null || echo "(no docker-compose.test.yml yet)"

echo "=== E) gateway/test-support/ (FakeWaDriver location per spec) ==="
ls -la gateway/test-support/ 2>/dev/null || echo "(gateway/test-support/ does not exist yet)"
echo "--- grep for FakeWaDriver / WA_DRIVER wiring across gateway/src ---"
grep -rn "FakeWaDriver\|WA_DRIVER\|ALLOW_FAKE_WA" gateway/src gateway/test-support 2>/dev/null || echo "(no matches - FakeWaDriver not wired yet)"

echo "=== F) gateway/src/config.js (env-driven limits module, if it exists) ==="
cat gateway/src/config.js 2>/dev/null || echo "(no gateway/src/config.js yet)"

echo "=== G) gateway/src directory tree (current module structure) ==="
find gateway/src -type f -name "*.js" | sort

echo "=== H) MinIO / S3 env vars currently referenced ==="
grep -rn "MINIO_\|S3_" .env.example gateway/src docker-compose.yml 2>/dev/null | grep -v node_modules || echo "(no MINIO_/S3_ references found)"

echo "=== I) versions ==="
node --version
docker --version
docker compose version
echo "---"
free -h
echo "---"
df -h /home/sharwa/sharwa_ai

echo "=== J) tail of progress/open-questions docs (to append correctly, not guess format) ==="
echo "--- docs/P0_PROGRESS.md (last 40 lines) ---"
tail -n 40 docs/P0_PROGRESS.md 2>/dev/null || echo "(no docs/P0_PROGRESS.md yet)"
echo "--- docs/P0_OPEN_QUESTIONS.md (last 40 lines) ---"
tail -n 40 docs/P0_OPEN_QUESTIONS.md 2>/dev/null || echo "(no docs/P0_OPEN_QUESTIONS.md yet)"
echo "--- docs/P0_FINDINGS.md (last 40 lines) ---"
tail -n 40 docs/P0_FINDINGS.md 2>/dev/null || echo "(no docs/P0_FINDINGS.md yet)"

echo "=== K) current gateway/src/media.js and sessions.js hashes (sanity: matches the commit we just made) ==="
git log -1 --format="HEAD: %H %s"
sha256sum gateway/src/media.js gateway/src/sessions.js
