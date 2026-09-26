#!/usr/bin/env bash
set -euo pipefail
cd /home/sharwa/sharwa_ai

echo "=== 1) docker-compose.yml — add gateway/gateway-forwarder app tier ==="
python3 - <<'PYEOF'
path = "docker-compose.yml"
with open(path, "r", encoding="utf-8") as f:
    src = f.read()

# 1a. Header comment: this file is no longer a "not run" draft, and app code is
# no longer categorically absent - gateway/gateway-forwarder are added below.
old_header = """# Derived from docs/reference/docker-compose.reference.yml, scoped to the P0.1
# data layer only (PROMPT_P0 §6 P0.1 step 1). worker-*/scheduler and all
# application code (gateway/forwarder/api/prometheus) are intentionally ABSENT:
# they are app code and are forbidden this round."""
assert old_header in src, "header block not found verbatim - aborting"
new_header = """# Derived from docs/reference/docker-compose.reference.yml. Originally scoped to
# the P0.1 data layer only (PROMPT_P0 §6 P0.1 step 1); P0.1 was verified on real
# Docker at C1 (docs/P0_C1_REPORT.md) - this file is no longer a "not run" draft.
# P0.2 (R3_DIRECTIVE) adds the `gateway`/`gateway-forwarder` app tier below.
# worker-*/scheduler/api/prometheus remain ABSENT - still out of scope."""
src = src.replace(old_header, new_header)

# 1b. Memory budget comment.
old_budget = """# Memory budget (data tier only): postgres 1536 + pgbouncer 64 +
# redis-durable 320 + redis-cache 128 = 2048 MB, leaving ~6.0 GB of an 8 GB host
# for the OS, page cache, and the (later) gateway/forwarder/api/prometheus tiers."""
assert old_budget in src, "memory budget comment not found verbatim - aborting"
new_budget = """# Memory budget: data tier (postgres 1536 + pgbouncer 64 + redis-durable 320 +
# redis-cache 128 = 2048 MB) + app tier (gateway 512 + gateway-forwarder 128 =
# 640 MB) = 2688 MB, leaving ~5.3 GB of an 8 GB host for the OS, page cache, and
# the still-absent api/prometheus tiers. The gateway/gateway-forwarder limits
# are a FIRST-PASS ESTIMATE (Baileys per-session memory not yet load-tested
# under P0.2) - revisit once real WhatsApp sessions run under load."""
src = src.replace(old_budget, new_budget)

# 1c. New services, inserted right before the top-level `networks:` key (i.e.
# right after redis-cache, the last data-tier service).
anchor = "\nnetworks:\n  data:\n    driver: bridge\n    internal: true                       # databases/Redis cannot be reached from outside\n"
assert anchor in src, "networks: anchor not found verbatim - aborting"

new_services = """
  # ---------------------------------------------------------------- app tier
  # P0.2 (R3_DIRECTIVE, disaster #4/G1 + #5/#17): the WA socket (gateway) and
  # the Django delivery path (gateway-forwarder) are separate containers/
  # processes so a slow/down Django never blocks inbound message receipt.
  gateway:
    build:
      context: ./gateway
    image: sharwa-ai-gateway:p0.2          # tag shared with gateway-forwarder below (built once)
    environment:
      PORT: "4001"
      NODE_ENV: production
      SHARWA_AI_GATEWAY_API_KEY: ${SHARWA_AI_GATEWAY_API_KEY:?SHARWA_AI_GATEWAY_API_KEY is required (see .env)}
      SHARWA_AI_GATEWAY_WEBHOOK_SECRET: ${SHARWA_AI_GATEWAY_WEBHOOK_SECRET:?SHARWA_AI_GATEWAY_WEBHOOK_SECRET is required (see .env)}
      # host.docker.internal (below) is what makes these reachable when Django/
      # MinIO run on the VPS host itself rather than in this compose file -
      # "localhost" from INSIDE the container would mean the container itself.
      DJANGO_BASE_URL: ${DJANGO_BASE_URL:-http://host.docker.internal:8000}
      MINIO_ENDPOINT_URL: ${MINIO_ENDPOINT_URL:-http://host.docker.internal:9000}
      MINIO_ACCESS_KEY: ${MINIO_ACCESS_KEY:?MINIO_ACCESS_KEY is required (see .env)}
      MINIO_SECRET_KEY: ${MINIO_SECRET_KEY:?MINIO_SECRET_KEY is required (see .env)}
      MINIO_BUCKET_NAME: ${MINIO_BUCKET_NAME:-sharwa-ai}
      MINIO_USE_SSL: ${MINIO_USE_SSL:-false}
      AUTH_SESSIONS_DIR: /app/auth_sessions
      REDIS_DURABLE_HOST: redis-durable
      REDIS_DURABLE_PORT: "6379"
      REDIS_DURABLE_PASSWORD: ${REDIS_DURABLE_PASSWORD:?REDIS_DURABLE_PASSWORD is required (see .env)}
    volumes:
      - gateway_auth_sessions:/app/auth_sessions   # WhatsApp session credentials survive restarts
    networks: [data, app]                  # data: reach redis-durable. app: reach the internet (WhatsApp) + host
    extra_hosts:
      - "host.docker.internal:host-gateway"
    ports:
      - "127.0.0.1:4001:4001"              # loopback only (Dockerfile: never published to the public internet)
    mem_limit: 512m
    memswap_limit: 512m
    cpus: 1.0
    pids_limit: 200
    healthcheck:
      test: ["CMD", "wget", "-q", "-O", "-", "http://127.0.0.1:4001/healthz"]
      interval: 10s
      timeout: 5s
      retries: 6
      start_period: 15s
    depends_on:
      redis-durable: { condition: service_healthy }
    logging: *default-logging
    restart: unless-stopped

  gateway-forwarder:                       # same image as `gateway`, different command - reads in:{shard}
    image: sharwa-ai-gateway:p0.2          # via the legacy-forwarder consumer group and delivers to Django
    command: ["node", "src/forwarder.js"]
    environment:
      NODE_ENV: production
      SHARWA_AI_GATEWAY_WEBHOOK_SECRET: ${SHARWA_AI_GATEWAY_WEBHOOK_SECRET:?SHARWA_AI_GATEWAY_WEBHOOK_SECRET is required (see .env)}
      DJANGO_BASE_URL: ${DJANGO_BASE_URL:-http://host.docker.internal:8000}
      REDIS_DURABLE_HOST: redis-durable
      REDIS_DURABLE_PORT: "6379"
      REDIS_DURABLE_PASSWORD: ${REDIS_DURABLE_PASSWORD:?REDIS_DURABLE_PASSWORD is required (see .env)}
    networks: [data, app]
    extra_hosts:
      - "host.docker.internal:host-gateway"
    mem_limit: 128m
    memswap_limit: 128m
    cpus: 0.5
    pids_limit: 50
    # No process-level healthcheck: this image's alpine base has no verified
    # pgrep/procps, and a wrong CMD here would flap the container needlessly
    # (H3: don't guess a healthcheck we haven't proven). restart: unless-stopped
    # plus the forwarder's own structured logs (logger.info '[forwarder] started')
    # are the liveness signal for now - revisit if we add a tiny /healthz shim.
    logging: *default-logging
    restart: unless-stopped
"""

src = src.replace(anchor, new_services + anchor)

# 1d. networks: section - add a second, NON-internal network for gateway/forwarder.
old_networks = """networks:
  data:
    driver: bridge
    internal: true                       # databases/Redis cannot be reached from outside"""
assert old_networks in src, "networks block not found verbatim - aborting"
new_networks = """networks:
  data:
    driver: bridge
    internal: true                       # databases/Redis cannot be reached from outside
  app:
    driver: bridge                       # NOT internal: gateway needs outbound internet (WhatsApp)
                                          # and a route to Django/MinIO on the host (host.docker.internal)"""
src = src.replace(old_networks, new_networks)

# 1e. volumes: section - persistent WhatsApp auth credentials.
old_volumes = """volumes:
  pgdata:
  redis_durable:"""
assert old_volumes in src, "volumes block not found verbatim - aborting"
new_volumes = """volumes:
  pgdata:
  redis_durable:
  gateway_auth_sessions:"""
src = src.replace(old_volumes, new_volumes)

with open(path, "w", encoding="utf-8") as f:
    f.write(src)
print("docker-compose.yml patched OK")
PYEOF

echo "=== 2) .env.example — document the new required/optional gateway vars ==="
python3 - <<'PYEOF'
path = ".env.example"
with open(path, "r", encoding="utf-8") as f:
    src = f.read()

marker = "# --- Redis (docker-compose.yml) ---\nREDIS_DURABLE_PASSWORD=change-me\nREDIS_CACHE_PASSWORD=change-me\n"
assert marker in src, "Redis section not found verbatim in .env.example - aborting"

addition = """
# --- Gateway app tier (docker-compose.yml gateway / gateway-forwarder, P0.2) ---
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
# MINIO_USE_SSL=false
"""

src = src.replace(marker, marker + addition)
with open(path, "w", encoding="utf-8") as f:
    f.write(src)
print(".env.example patched OK")
PYEOF

echo "=== 3) validate: docker compose config must parse cleanly ==="
if command -v docker >/dev/null 2>&1; then
  docker compose config >/tmp/compose_config_resolved.yml 2>&1
  CONFIG_EXIT=$?
  echo "docker compose config exit: $CONFIG_EXIT"
  if [ "$CONFIG_EXIT" -ne 0 ]; then
    echo "--- docker compose config output (error) ---"
    cat /tmp/compose_config_resolved.yml
  else
    echo "docker compose config: OK (parsed cleanly - env vars from .env resolved, see /tmp/compose_config_resolved.yml)"
  fi
else
  echo "docker not found on PATH - skipping docker compose config validation (run it manually)"
fi

echo "=== 4) yaml sanity check (python) as a second, independent parse ==="
python3 - <<'PYEOF'
try:
    import yaml
except ImportError:
    print("PyYAML not installed - skipping python-side YAML parse (docker compose config above is the real check)")
else:
    with open("docker-compose.yml") as f:
        doc = yaml.safe_load(f)
    services = list(doc.get("services", {}).keys())
    print("services:", services)
    assert "gateway" in services and "gateway-forwarder" in services
    print("YAML parses OK; gateway + gateway-forwarder present.")
PYEOF

echo "=== 5) git diff summary ==="
git status
git diff --stat
