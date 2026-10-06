-- 0019_p3_cart_tombstones.sql - P3 (F-P3-24, owner decision OQ-P3-19 = option A).
-- Additive + idempotent (H14); 0001-0018 untouched.
--
-- Problem: a terminal event (cart.recovered / cart.cleared) that arrives BEFORE
-- the first cart.updated of the same cart finds no row (carts.customer_id is
-- NOT NULL, so no row can be created without a customer) and was dropped
-- ('already_final', nothing stored). A late cart.updated then opened the cart
-- and scheduled a reminder - a customer who already bought got "you left
-- items in your cart". The store's publisher retries each envelope
-- independently (Celery autoretry, backoff <= 300s + jitter), so the order is
-- NOT guaranteed.
--
-- Fix: remember the terminal outcome of an unseen cart. Two guards read it:
--   1. app/workers/carts.py - a cart.updated for a tombstoned cart never opens it;
--   2. app/workers/cart_reminder.py - the reminder re-checks the tombstone under
--      the cart row lock, closing the concurrent-commit window (H89).
-- The only writer is app/db/repos_carts.py.

CREATE TABLE IF NOT EXISTS cart_tombstones (
  tenant_id         uuid NOT NULL REFERENCES tenants(id),
  platform_cart_id  text NOT NULL,
  status            text NOT NULL CHECK (status IN ('recovered','cleared')),
  occurred_at       timestamptz NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, platform_cart_id)
);
CREATE INDEX IF NOT EXISTS cart_tombstones_created_idx ON cart_tombstones (created_at);

-- RLS is EXPLICIT (the F-P2-06 lesson: new tables opt in themselves).
ALTER TABLE cart_tombstones ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname = 'public' AND tablename = 'cart_tombstones'
                     AND policyname = 'tenant_isolation') THEN
    CREATE POLICY tenant_isolation ON cart_tombstones
      USING (tenant_id = app.current_tenant())
      WITH CHECK (tenant_id = app.current_tenant());
  END IF;
END $$;
-- Append-only for the app role: a tombstone is a fact, never edited.
GRANT SELECT, INSERT ON cart_tombstones TO sharwa_app;

-- Retention rides the sanctioned purge (H91): same window as final carts.
-- The return value keeps its 0015 meaning (carts deleted); tombstones are
-- purged in the same call.
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
  DELETE FROM cart_tombstones
   WHERE created_at < now() - p_retention;
  RETURN v_deleted;
END $$;
REVOKE ALL ON FUNCTION app.purge_old_carts(interval) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.purge_old_carts(interval) TO sharwa_system;
