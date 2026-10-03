-- 0014_p3_fix_reserve.sql - P3.1 repair round 0' (F-P3-10, F-P3-12).
--
-- Two SQL fixes, both CREATE OR REPLACE with the SAME signature and the SAME
-- output column names as 0012/0013 (Python reads results by name - H86).
-- 0013 itself is left untouched (it is already applied wherever it ran).

-- ---------------------------------------------------------------------------
-- F-P3-10: app.reserve_send_slot crashed with AmbiguousColumn on the first real
-- reservation. RETURNS TABLE declares output variables named `sent_today` and
-- `daily_cap` which shadow the number_health columns of the same name. The
-- `#variable_conflict use_column` directive (first thing after AS $$) makes the
-- body's unqualified names resolve to the TABLE COLUMNS, not the output vars.
-- Signature + output names unchanged (Python reads `verdict`, `defer_until`,
-- `sent_today`, `daily_cap` by name).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.reserve_send_slot(
  p_channel uuid, p_class text, p_gap_s integer, p_now timestamptz DEFAULT now()
)
RETURNS TABLE (verdict text, defer_until timestamptz, sent_today integer, daily_cap integer)
LANGUAGE plpgsql SECURITY INVOKER SET search_path = public, pg_temp AS $$
#variable_conflict use_column
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
-- F-P3-12: app.claim_outbox's `ORDER BY (message_class = 'marketing')` after
-- UNION ALL is invalid SQL (FeatureNotSupported). Wrap the union in a subquery,
-- then order. Signature unchanged (integer, interval, integer).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.claim_outbox(
  p_limit integer,
  p_lease interval,
  p_marketing_limit integer DEFAULT 4
)
RETURNS SETOF outbox LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  UPDATE outbox o
     SET status = 'dropped_stale'
    FROM conversations c
   WHERE o.conversation_id = c.id
     AND o.status = 'pending' AND o.origin = 'bot'
     AND (c.epoch <> o.expected_epoch OR c.bot_status = 'closed');

  RETURN QUERY
  WITH due AS (
    SELECT id, tenant_id, conversation_id, channel_account_id, idempotency_key,
           origin, message_class, expected_epoch, to_wa_id, payload, attempts, status,
           next_attempt_at
      FROM outbox
     WHERE (status = 'pending' AND next_attempt_at <= now())
        OR (status = 'sending' AND locked_until < now())
     FOR UPDATE SKIP LOCKED
  ),
  marketing AS (
    SELECT DISTINCT ON (channel_account_id) *
      FROM due
     WHERE message_class = 'marketing'
     ORDER BY channel_account_id, next_attempt_at
     LIMIT p_marketing_limit
  ),
  picked AS (
    SELECT * FROM (
      SELECT * FROM due WHERE message_class <> 'marketing'
      UNION ALL
      SELECT * FROM marketing
    ) u
    ORDER BY (message_class = 'marketing'), next_attempt_at
    LIMIT p_limit
  )
  UPDATE outbox o
     SET status = 'sending', attempts = o.attempts + 1, locked_until = now() + p_lease
    FROM picked p WHERE o.id = p.id
  RETURNING o.*;
END $$;
REVOKE ALL ON FUNCTION app.claim_outbox(integer, interval, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.claim_outbox(integer, interval, integer) TO sharwa_system;
