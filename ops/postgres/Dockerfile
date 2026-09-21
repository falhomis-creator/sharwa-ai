# ops/postgres/Dockerfile
# PostgreSQL 16 + PostGIS + pg_trgm (from postgis/postgis:16-3.4) + pgvector.
#
# Why a custom image (PROMPT_P0 §6 P0.1 step 1): no single upstream image ships
# all three extensions together - postgis/postgis:16-3.4 has PostGIS + pg_trgm
# (contrib) but lacks pgvector, while pgvector/pgvector:pg16 has pgvector but
# lacks PostGIS. We add the PGDG pgvector package to the PostGIS image.
#
# Image tag is explicit (16-3.4), not `latest`. Note: the apt package below is
# NOT version-pinned (it follows the PGDG repo at build time); recorded as a
# deviation in docs/P0_DEVIATIONS.md.

FROM postgis/postgis:16-3.4

# The official postgres image already configures the PGDG apt repository, so the
# matching extension package installs directly.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/*

# Enforce UTF-8 at first initdb (PROMPT_P0 §6 P0.1 step 2).
ENV POSTGRES_INITDB_ARGS="--encoding=UTF8 --locale=C.UTF-8"

# Role bootstrap: creates LOGIN roles from .env; base NOLOGIN roles (sharwa_app,
# sharwa_system) mirror docs/reference/schema.sql.
COPY init/ /docker-entrypoint-initdb.d/
