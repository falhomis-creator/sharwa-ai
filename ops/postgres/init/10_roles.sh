#!/usr/bin/env bash
# ops/postgres/init/10_roles.sh
# Runs ONCE on first database initialization (docker-entrypoint-initdb.d), as
# the superuser POSTGRES_USER. Creates LOGIN roles from environment variables
# and grants membership in the NOLOGIN base roles sharwa_app / sharwa_system
# (which docs/reference/schema.sql also creates idempotently). A separate
# migration user owns the schema objects.
#
# Governed by: PROMPT_P0 §6 P0.1 step 2; docs/reference/schema.sql lines 13-17.
set -euo pipefail

: "${SHARWA_APP_USER:?SHARWA_APP_USER is required}"
: "${SHARWA_APP_PASSWORD:?SHARWA_APP_PASSWORD is required}"
: "${SHARWA_SYSTEM_USER:?SHARWA_SYSTEM_USER is required}"
: "${SHARWA_SYSTEM_PASSWORD:?SHARWA_SYSTEM_PASSWORD is required}"
: "${SHARWA_MIGRATION_USER:?SHARWA_MIGRATION_USER is required}"
: "${SHARWA_MIGRATION_PASSWORD:?SHARWA_MIGRATION_PASSWORD is required}"

psql -v ON_ERROR_STOP=1 \
  -v dbname="${POSTGRES_DB}" \
  -v app_user="${SHARWA_APP_USER}" \
  -v app_password="${SHARWA_APP_PASSWORD}" \
  -v system_user="${SHARWA_SYSTEM_USER}" \
  -v system_password="${SHARWA_SYSTEM_PASSWORD}" \
  -v migration_user="${SHARWA_MIGRATION_USER}" \
  -v migration_password="${SHARWA_MIGRATION_PASSWORD}" \
  --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" <<'SQL'
-- Extensions pre-created as superuser so schema.sql's `CREATE EXTENSION IF NOT
-- EXISTS` is a no-op and the migration user never needs SUPERUSER.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS postgis;

-- Base NOLOGIN roles (mirror schema.sql so membership works before migrations).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sharwa_app') THEN
    CREATE ROLE sharwa_app NOLOGIN NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sharwa_system') THEN
    CREATE ROLE sharwa_system NOLOGIN NOBYPASSRLS;
  END IF;
END $$;

-- LOGIN roles from .env, inheriting the base roles (least privilege).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') THEN
    CREATE ROLE :"app_user" LOGIN PASSWORD :'app_password' NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'system_user') THEN
    CREATE ROLE :"system_user" LOGIN PASSWORD :'system_password' NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'migration_user') THEN
    CREATE ROLE :"migration_user" LOGIN PASSWORD :'migration_password' NOBYPASSRLS;
  END IF;
END $$;

-- Membership: app user is subject to RLS as sharwa_app; system user may only run
-- SECURITY DEFINER functions as sharwa_system; the migration user owns objects.
GRANT sharwa_app TO :"app_user";
GRANT sharwa_system TO :"system_user";
GRANT sharwa_app TO :"migration_user";

-- The migration user creates schemas and tables when applying schema.sql.
GRANT CREATE ON DATABASE :"dbname" TO :"migration_user";
GRANT CREATE ON SCHEMA public TO :"migration_user";
SQL
