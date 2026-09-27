-- =============================================================================
-- 0007_p1_catalog.sql - P1.4 catalog read-model delta (PROMPT_P1_04 §9 item 9).
--
-- The catalog tables already exist in 0001_baseline.sql (a byte-identical copy
-- of the reference schema). This forward-only migration adds ONLY what P1.4
-- needs that the reference did not already carry:
--
--   1. stock_levels.source_version  - variant.stock events carry their own
--      monotonic version; without it an older stock observation could overwrite
--      a newer one (H37). Added with backfill + DEFAULT 0 + NOT NULL (N1) so a
--      row can never carry a NULL that would make `X > NULL` silently skip an
--      update while still reporting "applied".
--   2. kb_chunks.source_version     - kb.upserted events carry a version; the
--      same H37 ordering guarantee (backfill + DEFAULT 0 + NOT NULL, N1).
--   3. kb_chunks UNIQUE (tenant_id, source) - the natural upsert key for a
--      kb.upserted event (source identifies the policy chunk; there is no
--      platform id for a knowledge-base row). Built as CREATE UNIQUE INDEX IF
--      NOT EXISTS (N2) so the migration stays idempotent (H14: ADD CONSTRAINT
--      has no IF NOT EXISTS).
--   4. app.list_catalog_sync_state() - a SECURITY DEFINER function so the
--      system role can enumerate tenants + their sync state for the reconcile
--      thread (sharwa_system has no direct table access by design).
--
-- No existing table is re-created, and no index that already exists is added
-- (the FTS GIN and trgm GIN indexes are present in 0001).
-- =============================================================================

ALTER TABLE stock_levels ADD COLUMN IF NOT EXISTS source_version bigint;
UPDATE stock_levels SET source_version = 0 WHERE source_version IS NULL;
ALTER TABLE stock_levels ALTER COLUMN source_version SET DEFAULT 0;
ALTER TABLE stock_levels ALTER COLUMN source_version SET NOT NULL;

ALTER TABLE kb_chunks ADD COLUMN IF NOT EXISTS source_version bigint;
UPDATE kb_chunks SET source_version = 0 WHERE source_version IS NULL;
ALTER TABLE kb_chunks ALTER COLUMN source_version SET DEFAULT 0;
ALTER TABLE kb_chunks ALTER COLUMN source_version SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS kb_chunks_tenant_source_uq ON kb_chunks (tenant_id, source);

-- Reconcile thread: list every active tenant and its sync state. SECURITY
-- DEFINER + owner context so sharwa_system (which has no direct table SELECT)
-- can enumerate tenants; RLS is bypassed only by this narrow, owned function.
CREATE OR REPLACE FUNCTION app.list_catalog_sync_state()
RETURNS TABLE (tenant_id uuid, platform_ref text, cursor text, last_reconcile_ok timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT t.id, t.platform_ref, c.cursor, c.last_reconcile_ok
    FROM tenants t
    LEFT JOIN catalog_sync_cursor c ON c.tenant_id = t.id
   WHERE t.status = 'active'
   ORDER BY t.id
$$;

REVOKE ALL ON FUNCTION app.list_catalog_sync_state() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.list_catalog_sync_state() TO sharwa_system;
