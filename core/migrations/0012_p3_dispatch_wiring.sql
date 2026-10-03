-- 0012_p3_dispatch_wiring.sql
-- F-P1-12: the dispatcher never ran against a real database. Three wiring defects
-- (outbox_stats without a system-role SELECT grant, claim_outbox called with
-- int/int against (integer, interval), and _outbox_from_row reading columns by
-- position) left EVERY outbound row dead on real infrastructure. This migration
-- fixes the SQL side; the Python side (typed params + by-name columns) is fixed
-- in app/db/repos_outbox.py and app/workers/dispatch.py in the same change.
--
-- Idempotent (H14): CREATE OR REPLACE for functions; DROP FUNCTION IF EXISTS for
-- the superseded claim_outbox signature. 0001-0011 are left untouched.

-- ---------------------------------------------------------------------------
-- 1. app.outbox_stats(): cross-tenant read for the dispatcher's gauge. The system
--    role has EXECUTE on SECURITY DEFINER functions only (no direct table SELECT),
--    so this function is the single read path - and it now tags each row by
--    message_class (H82: the service outbox must be watched separately).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.outbox_stats()
RETURNS TABLE (message_class text, depth bigint, oldest_pending_seconds double precision)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT message_class,
         count(*),
         COALESCE(EXTRACT(EPOCH FROM (now() - min(next_attempt_at))), 0)
    FROM outbox
   WHERE status IN ('pending', 'sending')
   GROUP BY message_class
$$;
REVOKE ALL ON FUNCTION app.outbox_stats() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.outbox_stats() TO sharwa_system;

-- ---------------------------------------------------------------------------
-- 2. app.claim_outbox: ONE claiming function (H74 in spirit), replacing the
--    P1.2 two-arg version. F-P3-04: non-marketing rows first (service never
--    starved behind a blast - H82), then marketing rows capped at
--    p_marketing_limit per cycle and one row per channel_account_id.
--    F-P1-09 preserved: stale bot rows (epoch mismatch / closed) are dropped.
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS app.claim_outbox(integer, interval);

CREATE OR REPLACE FUNCTION app.claim_outbox(
  p_limit integer,
  p_lease interval,
  p_marketing_limit integer DEFAULT 4
)
RETURNS SETOF outbox LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  -- F-P1-09: a bot row whose conversation epoch no longer matches, or whose
  -- conversation is closed, is stale - dropped, never sent.
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
    SELECT * FROM due WHERE message_class <> 'marketing'
    UNION ALL
    SELECT * FROM marketing
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
