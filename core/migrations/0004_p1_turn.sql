-- =============================================================================
-- 0004_p1_turn.sql - P1.2 turn-engine candidate fetch (forward-only, H14)
--
-- The turn engine must find conversations with needs_turn=true ACROSS tenants.
-- A plain SELECT under sharwa_app is RLS-restricted to one tenant, and
-- sharwa_system has no SELECT grant on conversations - so this SECURITY
-- DEFINER function is the single cross-tenant surface (it returns only
-- pointers: conversation_id + tenant_id, never any tenant data), and the real
-- serialization happens later per-conversation via FOR UPDATE SKIP LOCKED in
-- repos_outbox.lock_conversation.
-- =============================================================================

CREATE OR REPLACE FUNCTION app.claim_due_turns(p_limit integer)
RETURNS TABLE (conversation_id uuid, tenant_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT id, tenant_id
    FROM conversations
   WHERE needs_turn
   ORDER BY last_message_at
   LIMIT p_limit
$$;

REVOKE ALL ON FUNCTION app.claim_due_turns(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.claim_due_turns(integer) TO sharwa_system;
