-- =============================================================================
-- 0006_p1_ws.sql - P1.3b additive migration (forward-only, H14)
--
-- Adds the ONE database object the P1.3b WebSocket retention sweep (W7) needs:
-- a SECURITY DEFINER function that deletes inbox_events rows older than a
-- cutoff in bounded batches. Deletion is cross-tenant (it must prune every
-- tenant's old rows, not just the caller's), so it is granted to sharwa_system
-- ALONE - the worker runs it through system_tx(), the same role that runs
-- app.claim_due_turns.
--
-- Why SECURITY DEFINER and not a plain DELETE: a tenant-scoped role cannot (and
-- must not) delete another tenant's rows; sharwa_system has no tenant context
-- but also no direct DELETE grant on inbox_events. Wrapping the cross-tenant
-- delete in a DEFINER function is the same pattern 0002_p0_api.sql uses for
-- app.resolve_tenant / app.set_kill_switch.
--
-- tenant_counters.inbox_seq is NEVER touched here - the seq is monotonic forever
-- (otherwise WebSocket resync-by-last_seq collapses). This migration is the only
-- new file touched; never edit 0001..0005 or docs/reference/*.
-- =============================================================================

CREATE OR REPLACE FUNCTION app.delete_old_inbox_events(
  p_cutoff timestamptz,
  p_batch integer,
  p_max_batches integer
)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_total bigint := 0;
  v_deleted bigint;
  v_loops integer := 0;
BEGIN
  -- Bounded (H4): delete in batches of p_batch via ctid, with a hard cap of
  -- p_max_batches per invocation - never one giant DELETE that locks the table.
  LOOP
    EXIT WHEN v_loops >= p_max_batches;
    DELETE FROM inbox_events
    WHERE ctid IN (
      SELECT ctid FROM inbox_events
      WHERE created_at < p_cutoff
      LIMIT p_batch
    );
    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    v_total := v_total + v_deleted;
    v_loops := v_loops + 1;
    EXIT WHEN v_deleted = 0;
  END LOOP;
  RETURN v_total;
END $$;

REVOKE ALL ON FUNCTION app.delete_old_inbox_events(timestamptz, integer, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.delete_old_inbox_events(timestamptz, integer, integer)
  TO sharwa_system;
