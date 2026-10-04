-- 0015_p3_scheduler.sql - P3.2 scheduler engine + cart events (Stage B).
--
-- Idempotent (H14): ADD COLUMN IF NOT EXISTS for columns; DO blocks for anything
-- that has no IF NOT EXISTS. 0001-0014 left untouched (app.claim_due_jobs is
-- frozen by the architect - NOT modified here).
--
-- H87 (single writer in repos_scheduler.py), H88 (defer != attempt), H89
-- (idempotent handlers), H91 (cart snapshot is minimal + private), H92
-- (max lateness + finality beats execution).

-- ---------------------------------------------------------------------------
-- 1. carts (new table). RLS is EXPLICIT - the 0001 loop only ran over the
--    tables that existed then, and a new table must opt in itself (F-P2-06).
--    snapshot is the H91 minimal liveness snapshot (item_count, total_minor,
--    currency, <=3 truncated titles) - NEVER printed in logs, purged after
--    CARTS_RETENTION_D.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS carts (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id),
  customer_id       uuid NOT NULL REFERENCES customers(id),
  platform_cart_id  text NOT NULL,
  status            text NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open','recovered','cleared','reminded','expired')),
  last_activity_at  timestamptz NOT NULL,
  snapshot          jsonb NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, platform_cart_id)
);
CREATE INDEX IF NOT EXISTS carts_tenant_status_activity_idx
  ON carts (tenant_id, status, last_activity_at);

ALTER TABLE carts ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies
                  WHERE schemaname = 'public' AND tablename = 'carts'
                    AND policyname = 'tenant_isolation') THEN
    CREATE POLICY tenant_isolation ON carts
      USING (tenant_id = app.current_tenant())
      WITH CHECK (tenant_id = app.current_tenant());
  END IF;
END $$;
GRANT SELECT, INSERT, UPDATE, DELETE ON carts TO sharwa_app;

-- ---------------------------------------------------------------------------
-- 2. scheduled_jobs (ALTER): engine forensics + per-job max lateness (H92).
--    max_lateness_s = 0 means "no lateness limit" (the 0001 default behavior).
-- ---------------------------------------------------------------------------
ALTER TABLE scheduled_jobs ADD COLUMN IF NOT EXISTS last_error text;
ALTER TABLE scheduled_jobs ADD COLUMN IF NOT EXISTS finished_at timestamptz;
ALTER TABLE scheduled_jobs ADD COLUMN IF NOT EXISTS cancel_reason text;
ALTER TABLE scheduled_jobs ADD COLUMN IF NOT EXISTS max_lateness_s integer NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS scheduled_jobs_kind_status_idx
  ON scheduled_jobs (tenant_id, kind, status);

-- ---------------------------------------------------------------------------
-- 3. app.purge_old_carts(p_retention) - the sanctioned cross-tenant delete for
--    FINAL carts older than the retention (H91). RLS blocks a multi-tenant
--    DELETE from the app role; this SECURITY DEFINER function is the one
--    legitimate path, granted to sharwa_system ONLY (never sharwa_app).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.purge_old_carts(p_retention interval)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_deleted integer;
BEGIN
  IF p_retention IS NULL OR p_retention <= interval '0 seconds' THEN
    RAISE EXCEPTION 'p_retention must be a positive interval';
  END IF;
  DELETE FROM carts
   WHERE status IN ('recovered','cleared','reminded','expired')
     AND last_activity_at < now() - p_retention;
  GET DIAGNOSTICS v_deleted = ROW_COUNT;
  RETURN v_deleted;
END $$;
REVOKE ALL ON FUNCTION app.purge_old_carts(interval) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.purge_old_carts(interval) TO sharwa_system;

-- ---------------------------------------------------------------------------
-- 4. app.scheduler_job_stats() - counts + oldest overdue seconds per
--    (kind, status) for the metrics/CLI. SECURITY DEFINER because the system
--    role cannot read scheduled_jobs directly; NO payloads are returned (H48).
--    Output names are v_-prefixed and every column reference is table-qualified
--    so RETURNS TABLE names can never collide with column names (F-P3-10).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.scheduler_job_stats()
RETURNS TABLE (v_kind text, v_status text, v_jobs bigint, v_oldest_due_s double precision)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT j.kind, j.status, count(*)::bigint,
         COALESCE(
           min(EXTRACT(EPOCH FROM (now() - j.run_at)))
             FILTER (WHERE j.status = 'pending' AND j.run_at <= now()),
           0)::double precision
  FROM scheduled_jobs j
  GROUP BY j.kind, j.status
$$;
REVOKE ALL ON FUNCTION app.scheduler_job_stats() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.scheduler_job_stats() TO sharwa_system;
