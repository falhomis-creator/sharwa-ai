-- 0013_p3_send_policy.sql - P3.1 send-policy data layer (Stage B).
--
-- Idempotent (H14): ADD COLUMN IF NOT EXISTS for columns; DO blocks for CHECK
-- constraints (ADD CONSTRAINT has no IF NOT EXISTS). 0001-0012 left untouched.
--
-- H76-H86 (see docs/CONSTITUTION.md): one gate at send time; class derived from
-- the template and enforced by a DB constraint; the drip is a SQL slot on
-- number_health (FOR UPDATE) - never an in-memory or Redis counter; the daily
-- cap is a COLUMN written by the sweeper and enforced by SQL alone.

-- ---------------------------------------------------------------------------
-- 1. number_health: warm-up + dual (marketing/utility) drip windows.
-- ---------------------------------------------------------------------------
ALTER TABLE number_health ADD COLUMN IF NOT EXISTS warmup_started_at timestamptz;
ALTER TABLE number_health ADD COLUMN IF NOT EXISTS next_marketing_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE number_health ADD COLUMN IF NOT EXISTS next_utility_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE number_health ADD COLUMN IF NOT EXISTS utility_daily_cap integer NOT NULL DEFAULT 100 CHECK (utility_daily_cap >= 0);
ALTER TABLE number_health ADD COLUMN IF NOT EXISTS utility_sent_today integer NOT NULL DEFAULT 0;
ALTER TABLE number_health ADD COLUMN IF NOT EXISTS state_reason text;
ALTER TABLE number_health ADD COLUMN IF NOT EXISTS state_changed_at timestamptz NOT NULL DEFAULT now();

-- ---------------------------------------------------------------------------
-- 2. outbox: forensic policy_reason + two structural constraints.
--    H77: (origin = 'automation') IFF (message_class IN utility|marketing).
--    F-P3-02 becomes impossible: automation rows MUST carry a conversation.
-- ---------------------------------------------------------------------------
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS policy_reason text;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'outbox_automation_class_ck') THEN
    ALTER TABLE outbox ADD CONSTRAINT outbox_automation_class_ck
      CHECK ((origin = 'automation') = (message_class IN ('utility','marketing')));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'outbox_automation_conversation_ck') THEN
    ALTER TABLE outbox ADD CONSTRAINT outbox_automation_conversation_ck
      CHECK (origin <> 'automation' OR conversation_id IS NOT NULL);
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 3. proactive_ledger: the idempotent reservation record (H85).
--    UNIQUE(outbox_id) => reprocessing the same row never takes a second slot.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS proactive_ledger (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid NOT NULL REFERENCES tenants(id),
  channel_account_id  uuid NOT NULL REFERENCES channel_accounts(id),
  customer_id         uuid NOT NULL REFERENCES customers(id),
  outbox_id           uuid NOT NULL REFERENCES outbox(id) UNIQUE,
  message_class       text NOT NULL CHECK (message_class IN ('utility','marketing')),
  template_id         text NOT NULL,
  status              text NOT NULL DEFAULT 'reserved' CHECK (status IN ('reserved','handed_off','released')),
  reserved_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS proactive_ledger_customer_idx
  ON proactive_ledger (tenant_id, customer_id, message_class, reserved_at DESC);

-- RLS: the 0001 loop ran over the tables that existed THEN only - a new table
-- must declare its own policy explicitly (the F-P2-06 lesson, applied forward).
ALTER TABLE proactive_ledger ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON proactive_ledger;
CREATE POLICY tenant_isolation ON proactive_ledger
  USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
GRANT SELECT, INSERT, UPDATE ON proactive_ledger TO sharwa_app;

-- ---------------------------------------------------------------------------
-- 4. app.reserve_send_slot - the single atomic drip slot (H79). SECURITY INVOKER
--    so it runs inside tenant_tx under RLS. p_gap_s comes from Python (injectable
--    jitter), never random() in SQL (deterministic tests).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.reserve_send_slot(
  p_channel uuid, p_class text, p_gap_s integer, p_now timestamptz DEFAULT now()
)
RETURNS TABLE (verdict text, defer_until timestamptz, sent_today integer, daily_cap integer)
LANGUAGE plpgsql SECURITY INVOKER SET search_path = public, pg_temp AS $$
DECLARE
  v_tenant uuid;
  v_tz text;
  v_local_day date;
  v_prev_day date;
  v_sent integer;
  v_cap integer;
  v_next timestamptz;
  v_state text;
BEGIN
  IF p_class NOT IN ('marketing','utility') THEN
    RAISE EXCEPTION 'unknown class %', p_class;
  END IF;

  SELECT tenant_id INTO v_tenant FROM number_health WHERE channel_account_id = p_channel;
  IF v_tenant IS NULL THEN
    RETURN QUERY SELECT 'no_health_row'::text, NULL::timestamptz, 0, 0;
    RETURN;
  END IF;

  -- H79: the per-number serialization point.
  PERFORM 1 FROM number_health WHERE channel_account_id = p_channel FOR UPDATE;

  SELECT timezone INTO v_tz FROM tenants WHERE id = v_tenant;
  IF v_tz IS NULL OR v_tz = '' THEN
    RAISE EXCEPTION 'tenant has no timezone';
  END IF;

  v_local_day := (p_now AT TIME ZONE v_tz)::date;
  SELECT day INTO v_prev_day FROM number_health WHERE channel_account_id = p_channel;

  IF v_prev_day IS DISTINCT FROM v_local_day THEN
    UPDATE number_health SET day = v_local_day, sent_today = 0, utility_sent_today = 0, updated_at = now()
     WHERE channel_account_id = p_channel;
  END IF;

  IF p_class = 'marketing' THEN
    SELECT sent_today, daily_cap, next_marketing_at, state
      INTO v_sent, v_cap, v_next, v_state
      FROM number_health WHERE channel_account_id = p_channel;

    IF v_state = 'paused' THEN
      RETURN QUERY SELECT 'paused'::text, NULL::timestamptz, v_sent, v_cap; RETURN;
    END IF;
    IF v_sent >= v_cap THEN
      RETURN QUERY SELECT 'cap_reached'::text,
        ((v_local_day + 1)::timestamp AT TIME ZONE v_tz), v_sent, v_cap; RETURN;
    END IF;
    IF v_next > p_now THEN
      RETURN QUERY SELECT 'spacing'::text, v_next, v_sent, v_cap; RETURN;
    END IF;

    UPDATE number_health
       SET sent_today = sent_today + 1,
           next_marketing_at = p_now + make_interval(secs => p_gap_s),
           warmup_started_at = COALESCE(warmup_started_at, p_now),
           updated_at = now()
     WHERE channel_account_id = p_channel;
    SELECT sent_today, daily_cap INTO v_sent, v_cap FROM number_health WHERE channel_account_id = p_channel;
    RETURN QUERY SELECT 'reserved'::text, NULL::timestamptz, v_sent, v_cap;
  ELSE
    SELECT utility_sent_today, utility_daily_cap, next_utility_at
      INTO v_sent, v_cap, v_next
      FROM number_health WHERE channel_account_id = p_channel;

    IF v_sent >= v_cap THEN
      RETURN QUERY SELECT 'cap_reached'::text,
        ((v_local_day + 1)::timestamp AT TIME ZONE v_tz), v_sent, v_cap; RETURN;
    END IF;
    IF v_next > p_now THEN
      RETURN QUERY SELECT 'spacing'::text, v_next, v_sent, v_cap; RETURN;
    END IF;

    UPDATE number_health
       SET utility_sent_today = utility_sent_today + 1,
           next_utility_at = p_now + make_interval(secs => p_gap_s),
           updated_at = now()
     WHERE channel_account_id = p_channel;
    SELECT utility_sent_today, utility_daily_cap INTO v_sent, v_cap FROM number_health WHERE channel_account_id = p_channel;
    RETURN QUERY SELECT 'reserved'::text, NULL::timestamptz, v_sent, v_cap;
  END IF;
END $$;
REVOKE ALL ON FUNCTION app.reserve_send_slot(uuid, text, integer, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.reserve_send_slot(uuid, text, integer, timestamptz) TO sharwa_app;

-- ---------------------------------------------------------------------------
-- 5. app.release_send_slot - decrement the right counter (never below 0); the
--    spacing window stays (H85: a failure before delivery gives the slot back,
--    but the gap is not re-opened - fail toward fewer sends).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.release_send_slot(p_channel uuid, p_class text)
RETURNS void LANGUAGE plpgsql SECURITY INVOKER SET search_path = public, pg_temp AS $$
BEGIN
  IF p_class = 'marketing' THEN
    UPDATE number_health SET sent_today = GREATEST(sent_today - 1, 0), updated_at = now()
     WHERE channel_account_id = p_channel;
  ELSIF p_class = 'utility' THEN
    UPDATE number_health SET utility_sent_today = GREATEST(utility_sent_today - 1, 0), updated_at = now()
     WHERE channel_account_id = p_channel;
  ELSE
    RAISE EXCEPTION 'unknown class %', p_class;
  END IF;
END $$;
REVOKE ALL ON FUNCTION app.release_send_slot(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.release_send_slot(uuid, text) TO sharwa_app;

-- ---------------------------------------------------------------------------
-- 6. app.policy_sweep_targets - cross-tenant pointers for the sweeper (the same
--    shape as claim_due_turns): every ai_core WhatsApp channel.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.policy_sweep_targets()
RETURNS TABLE (tenant_id uuid, channel_account_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT tenant_id, id FROM channel_accounts
   WHERE engine = 'ai_core' AND type = 'whatsapp_baileys'
   ORDER BY tenant_id, id
$$;
REVOKE ALL ON FUNCTION app.policy_sweep_targets() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.policy_sweep_targets() TO sharwa_system;

