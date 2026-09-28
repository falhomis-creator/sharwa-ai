# core/Dockerfile - P0.7 `api` service (FastAPI channel-lifecycle backend).
#
# Mirrors the pinned-version discipline of requirements.txt (self-verified:
# 265/265 real tests, ruff/mypy --strict/import-linter all clean - see
# docs/P0_FINDINGS.md F19-F21 / docs/P0_DEVIATIONS.md D-29-D-32). No `latest`
# base image tag, per project rule (see docker-compose.yml's own header).
#
# psycopg[binary] and cryptography both ship manylinux wheels for this base,
# so no compiler toolchain / libpq-dev is needed at build time - only `wget`
# is installed, purely so this container's healthcheck can use the exact same
# `wget -q -O -` convention every other service in docker-compose.yml already
# uses (python:3.12-slim's Debian base does not include it by default).
FROM python:3.12.8-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash wget \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system app && useradd --system --gid app --home-dir /app --no-create-home app

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --no-compile -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY entrypoint.sh /usr/local/bin/entrypoint

RUN chmod +x /usr/local/bin/entrypoint \
    && chown -R app:app /app
USER app

EXPOSE 8080

# Never runs migrations automatically (H5/H14 - the FIRST real run against
# this VPS's existing hand-applied schema needs the one-time, explicit
# `--adopt-existing-schema 0001_baseline` flag; baking a bare `migrate` into
# the image's own startup would silently attempt to re-run 0001_baseline's
# raw SQL against tables that already exist). Migrations are a separate,
# deliberate operator step: `docker compose run --rm api python -m app.cli
# migrate [--adopt-existing-schema ...]` - see the delivery script's own
# post-install instructions.
# The shared bootstrap (core/entrypoint.sh) waits for PG + redis, verifies the
# migrations are applied (never applies them - H62), then execs the role command.
# `CMD` supplies the role; worker-realtime overrides it to "worker" in compose.
ENTRYPOINT ["/usr/local/bin/entrypoint"]
CMD ["api"]
