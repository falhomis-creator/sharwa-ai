-- 0017_p3_marketing_activation.sql - P3.4: marketing activation under
-- control (H100-H103). Additive + idempotent (H14); 0001-0016 untouched.
--
-- NOTHING here enables any tenant: the table starts EMPTY and absence means
-- DISABLED (H100). The only writer is app/db/repos_marketing.py (S29) and the
-- only enabler is the operator CLI with a literal confirmation phrase.

-- 1. marketing_activation: one row per tenant. Absence = no marketing.
CREATE TABLE IF NOT EXISTS marketing_activation (
  tenant_id          uuid PRIMARY KEY REFERENCES tenants(id),
  enabled            boolean NOT NULL DEFAULT false,
  canary_cap_per_day integer NOT NULL DEFAULT 5 CHECK (canary_cap_per_day BETWEEN 1 AND 500),
  enabled_by         text,
  enabled_at         timestamptz,
  disabled_at        timestamptz,
  updated_at         timestamptz NOT NULL DEFAULT now()
);
-- Partial index for the gate's hot read (the disabled majority is skipped).
CREATE INDEX IF NOT EXISTS marketing_activation_enabled_idx
  ON marketing_activation (tenant_id) WHERE enabled;

-- RLS is EXPLICIT (the F-P2-06 lesson: new tables opt in themselves).
ALTER TABLE marketing_activation ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname = 'public' AND tablename = 'marketing_activation'
                     AND policyname = 'tenant_isolation') THEN
    CREATE POLICY tenant_isolation ON marketing_activation
      USING (tenant_id = app.current_tenant())
      WITH CHECK (tenant_id = app.current_tenant());
  END IF;
END $$;
GRANT SELECT, INSERT, UPDATE ON marketing_activation TO sharwa_app;

-- 2. marketing_activation_log: append-only audit trail (like consents, H94) -
--    every enable/disable/cap_change is a logged human act (H100).
CREATE TABLE IF NOT EXISTS marketing_activation_log (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid NOT NULL REFERENCES tenants(id),
  action     text NOT NULL CHECK (action IN ('enable','disable','cap_change')),
  actor      text NOT NULL,
  reason     text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE marketing_activation_log ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname = 'public' AND tablename = 'marketing_activation_log'
                     AND policyname = 'tenant_isolation') THEN
    CREATE POLICY tenant_isolation ON marketing_activation_log
      USING (tenant_id = app.current_tenant())
      WITH CHECK (tenant_id = app.current_tenant());
  END IF;
END $$;
GRANT SELECT, INSERT ON marketing_activation_log TO sharwa_app;
REVOKE UPDATE, DELETE ON marketing_activation_log FROM sharwa_app;
