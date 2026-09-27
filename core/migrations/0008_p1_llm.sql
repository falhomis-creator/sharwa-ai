-- =============================================================================
-- 0008_p1_llm.sql - P1.5 LLM governance delta (PROMPT §5.7, L9).
--
-- llm_calls and tenant_budgets already exist in 0001_baseline.sql with their
-- columns (and their PK/indexes). The budget is per-tenant, checked inside a
-- tenant_tx, so sharwa_app needs no new grant. The ONLY thing missing is the
-- cross-tenant aggregation the budget_state{state} gauge needs: a SECURITY
-- DEFINER function so sharwa_system (which has no direct table SELECT) can count
-- tenants per budget state for the current month.
--
-- No existing table/column/index is created (the (tenant_id, month) PK already
-- covers the budget lookup).
-- =============================================================================

CREATE OR REPLACE FUNCTION app.budget_state_counts()
RETURNS TABLE (state text, count bigint)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT b.state, count(*)
    FROM tenant_budgets b
   WHERE b.month = date_trunc('month', now())::date
   GROUP BY b.state
$$;

REVOKE ALL ON FUNCTION app.budget_state_counts() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.budget_state_counts() TO sharwa_system;
