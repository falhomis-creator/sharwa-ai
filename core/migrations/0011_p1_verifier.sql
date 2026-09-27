-- 0011_p1_verifier.sql
-- F-P1-09 (PROMPT_P1_06 §0.2/§3): fix the stale-drop condition in
-- app.claim_outbox. The old condition dropped any bot row whose conversation was
-- NOT 'active' - including the row created by the very transition that paused
-- the bot (handoff_notice / safe_ack), so every handoff notice and safe_ack was
-- dropped before reaching the customer.
--
-- Correct rule (H26): a bot row is stale iff its epoch no longer matches (a
-- reply composed before a human intervened) OR the conversation is CLOSED.
-- 'paused_human' alone is no longer a reason to drop.
--
-- CREATE OR REPLACE is idempotent (H14): it yields the same function on an
-- existing database (where 0001 already applied) and on a fresh one, and it
-- appears in the migration ledger. 0001_baseline.sql is left UNTOUCHED on
-- purpose (editing an already-applied migration would fork the schema between
-- existing and fresh databases - H14 forward-only).

CREATE OR REPLACE FUNCTION app.claim_outbox(p_limit integer, p_lease interval DEFAULT interval '60 seconds')
RETURNS SETOF outbox LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  UPDATE outbox o
     SET status = 'dropped_stale'
    FROM conversations c
   WHERE o.conversation_id = c.id
     AND o.status = 'pending' AND o.origin = 'bot'
     AND (c.epoch <> o.expected_epoch OR c.bot_status = 'closed');

  RETURN QUERY
  WITH picked AS (
    SELECT id FROM outbox
     WHERE (status = 'pending' AND next_attempt_at <= now())
        OR (status = 'sending' AND locked_until < now())     -- recover after a crashed dispatcher
     ORDER BY next_attempt_at
     FOR UPDATE SKIP LOCKED
     LIMIT p_limit
  )
  UPDATE outbox o
     SET status = 'sending', attempts = o.attempts + 1, locked_until = now() + p_lease
    FROM picked WHERE o.id = picked.id
  RETURNING o.*;
END $$;
