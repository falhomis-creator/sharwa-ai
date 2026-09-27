-- =============================================================================
-- 0010_p1_summary.sql - P1.5b rolling-summary candidate fetch (forward-only, H14).
--
-- conversations.summary and conversations.slots already exist in 0001_baseline.sql
-- and sharwa_app is already GRANTed UPDATE on (summary, slots). No schema change
-- is needed for P1.5b. The ONLY thing missing is the cross-tenant reader the
-- summary worker needs: a SECURITY DEFINER function returning POINTERS only
-- (conversation_id, tenant_id) for conversations that (a) are active, (b) exceed
-- the trigger message count, (c) have at least SUMMARY_MIN_MESSAGES_BETWEEN new
-- inbound messages since the last summary (recorded in slots.summary_seq), and
-- (d) are under the daily cap (counted from llm_calls - the state-of-record, no
-- new counter column).
-- =============================================================================

CREATE OR REPLACE FUNCTION app.list_conversations_needing_summary(
    p_limit integer,
    p_trigger integer,
    p_min_between integer,
    p_max_per_day integer
)
RETURNS TABLE (conversation_id uuid, tenant_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT c.id, c.tenant_id
    FROM conversations c
   WHERE c.bot_status = 'active'
     AND c.message_seq > p_trigger
     AND (
       c.slots->>'summary_seq' IS NULL
       OR c.last_inbound_seq - (c.slots->>'summary_seq')::bigint >= p_min_between
     )
     AND (
       SELECT count(*)
         FROM llm_calls l
        WHERE l.conversation_id = c.id AND l.purpose = 'summary'
          AND l.created_at > now() - interval '24 hours'
     ) < p_max_per_day
   ORDER BY c.last_message_at
   LIMIT p_limit
$$;

REVOKE ALL ON FUNCTION app.list_conversations_needing_summary(integer, integer, integer, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.list_conversations_needing_summary(integer, integer, integer, integer) TO sharwa_system;
