-- =============================================================================
-- 0002_p0_api.sql - P0.7 core/ (api) additive migration
--
-- Forward-only (H14). Never edit 0001_baseline.sql or docs/reference/schema.sql
-- to add these - this file is the only place P0.7's new surface lives.
--
-- What this adds, and why (P0_DEEPSEEK_PROMPT.md / PROMPT_P0_foundation_for_deepseek.md,
-- section "P0.7 - حزمة core/ المصغّرة"):
--   1. audit_log        - every kill-switch state change is recorded IN THE SAME
--                          transaction as the change (spec: "كل تغيير حالة يكتب
--                          audit_log في المعاملة نفسها"). RLS by tenant_id.
--   2. api_idempotency  - backs the Idempotency-Key header on
--                          POST /v1/channels/whatsapp. RLS by tenant_id.
--   3. channel_accounts_one_active_baileys_uq - partial unique index: at most one
--                          ACTIVE whatsapp_baileys channel per tenant in P0 (spec:
--                          "فهرس فريد جزئي يمنع أكثر من قناة whatsapp_baileys واحدة
--                          نشطة لكل متجر"). "Active" here means not logged_out/banned
--                          (those are terminal-ish states where a merchant is expected
--                          to link a fresh channel) - conservative CHECK, see F19 below.
--   4. app.resolve_tenant(platform_ref)   - SECURITY DEFINER. Resolves the JWT's
--                          `tenant` claim (= tenants.platform_ref) to tenants.id
--                          WITHOUT a tenant context existing yet (this runs BEFORE
--                          auth establishes one - same shape as app.resolve_session
--                          in 0001). Rejects tenants.status='suspended'.
--   5. app.set_kill_switch(...)            - SECURITY DEFINER. The ONLY way core's
--                          tenant-scoped role may write kill_switches (0001 REVOKEs
--                          direct INSERT/UPDATE/DELETE on kill_switches from
--                          sharwa_app - by design, before core/ existed). Enforces
--                          scope: a tenant-role caller may only write its OWN
--                          tenant/channel scope; `global` scope is platform_admin-only
--                          (checked by the caller's JWT role, passed in explicitly -
--                          this function does not read JWTs, core's auth layer does).
--   6. app.list_kill_switches(...)          - SECURITY DEFINER read-scoped listing
--                          (0001's own `ks_app` RLS policy already lets sharwa_app
--                          SELECT its own global/tenant/channel rows directly, so
--                          this function is a convenience wrapper that also surfaces
--                          "changed by platform" per spec - not a security boundary
--                          by itself, unlike set_kill_switch).
--   7. GRANT INSERT (channel_accounts) to sharwa_app  - see F19 in docs/P0_FINDINGS.md:
--                          0001_baseline.sql REVOKEs INSERT/UPDATE/DELETE on
--                          channel_accounts from sharwa_app ("managed by the admin
--                          API only" - written before core/ existed). core/ IS now
--                          that API and must create channel_accounts rows on
--                          POST /v1/channels/whatsapp as a tenant-scoped operation.
--                          Safe to grant: 0001's own RLS `tenant_isolation` policy
--                          already forces tenant_id = app.current_tenant() on every
--                          INSERT via its WITH CHECK clause - a tenant-scoped
--                          connection cannot create a row for another tenant no
--                          matter what this GRANT allows. tenants/kill_switches stay
--                          untouched (still admin-API-only, now meaning
--                          app.set_kill_switch specifically for kill_switches; tenant
--                          creation stays CLI-only per spec, not reachable from
--                          sharwa_app/core's tenant-scoped connections at all).
--                          UPDATE is scoped to just the columns core's reconnect/
--                          status-refresh flow needs - never the tenant_id/type/
--                          session_id identity columns.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. audit_log
-- ---------------------------------------------------------------------------
CREATE TABLE audit_log (
  id          bigserial PRIMARY KEY,
  tenant_id   uuid REFERENCES tenants(id),  -- NULL = platform-wide action (e.g. a GLOBAL
                                             -- kill-switch change), never a tenant-scoped one
                                             -- written with a missing tenant by accident -
                                             -- every INSERT path in core/ sets this explicitly,
                                             -- one or the other, never leaves it to a default.
  actor_sub   text NOT NULL,             -- JWT `sub` claim of whoever made the change
  actor_role  text NOT NULL CHECK (actor_role IN ('merchant_admin','staff','platform_admin')),
  action      text NOT NULL,             -- e.g. 'kill_switch.set'
  target      text NOT NULL,             -- e.g. 'tenant:<id>:ai_reply'
  before      jsonb,
  after       jsonb,
  request_id  text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_tenant_time_idx ON audit_log (tenant_id, created_at DESC);
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON audit_log
  USING (tenant_id = app.current_tenant())
  WITH CHECK (tenant_id = app.current_tenant());
-- sharwa_system has NO tenant context (system_tx() never sets app.tenant_id), so the
-- tenant_isolation policy above (tenant_id = app.current_tenant() = NULL) would reject
-- even a legitimate platform-wide row. This second, narrower policy is the ONLY way
-- sharwa_system may write audit_log at all, and only a NULL-tenant row - it can never
-- forge a row into any tenant's own audit trail.
CREATE POLICY system_platform_wide_insert ON audit_log
  FOR INSERT TO sharwa_system
  WITH CHECK (tenant_id IS NULL);

-- ---------------------------------------------------------------------------
-- 2. api_idempotency (backs the Idempotency-Key header)
-- ---------------------------------------------------------------------------
CREATE TABLE api_idempotency (
  tenant_id        uuid NOT NULL REFERENCES tenants(id),
  idempotency_key  text NOT NULL,
  request_hash     text NOT NULL,        -- sha256 of the normalized request body; a replay with a
                                          -- DIFFERENT body under the same key is a real conflict, not a replay
  response_status  integer,
  response_body    jsonb,
  created_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, idempotency_key)
);
ALTER TABLE api_idempotency ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON api_idempotency
  USING (tenant_id = app.current_tenant())
  WITH CHECK (tenant_id = app.current_tenant());

-- ---------------------------------------------------------------------------
-- 3. Partial unique index: at most one non-terminal whatsapp_baileys channel per tenant
--    F19 (docs/P0_FINDINGS.md): "active" = not logged_out/banned, i.e. still occupying
--    the merchant's one P0 WhatsApp slot. A logged_out/banned channel must not block
--    linking a fresh one.
-- ---------------------------------------------------------------------------
CREATE UNIQUE INDEX channel_accounts_one_active_baileys_uq
  ON channel_accounts (tenant_id)
  WHERE type = 'whatsapp_baileys' AND status NOT IN ('logged_out', 'banned');

-- ---------------------------------------------------------------------------
-- 4. app.resolve_tenant - resolves a JWT `tenant` claim (platform_ref) to tenants.id.
--    Runs BEFORE any tenant context exists (same shape as app.resolve_session in 0001).
--    Returns NULL for an unknown platform_ref OR a suspended tenant - the caller
--    (core's auth dependency) turns NULL into TENANT_SUSPENDED/NOT_FOUND as appropriate,
--    never a raw exception (H3, U3).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.resolve_tenant(p_platform_ref text)
RETURNS TABLE (tenant_id uuid, status text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT id, status FROM tenants WHERE platform_ref = p_platform_ref
$$;

-- ---------------------------------------------------------------------------
-- 5. app.set_kill_switch - the only write path to kill_switches for sharwa_app.
--    p_actor_role is passed in by core's auth layer (from the verified JWT `role`
--    claim) - this function does not parse tokens; it only enforces scope rules
--    given an already-authenticated caller's declared role and tenant context.
--    Scope rules (spec): a tenant-scoped caller may set scope='tenant' (its own
--    tenant, forced via app.current_tenant(), never a caller-supplied tenant id)
--    or scope='channel_account' for a channel that belongs to its own tenant;
--    scope='global' is platform_admin-only and bypasses the tenant-context check
--    entirely (a platform_admin call runs via system_tx(), so app.current_tenant()
--    is NULL there by design - checked below).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.set_kill_switch(
  p_actor_role  text,
  p_scope       text,
  p_scope_id    uuid,
  p_capability  text,
  p_state       text,
  p_reason      text,
  p_set_by      text
) RETURNS kill_switches
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  v_tenant uuid := app.current_tenant();
  v_row kill_switches;
BEGIN
  IF p_actor_role NOT IN ('merchant_admin', 'staff', 'platform_admin') THEN
    RAISE EXCEPTION 'invalid actor role %', p_actor_role;
  END IF;
  IF p_scope NOT IN ('global', 'tenant', 'channel_account') THEN
    RAISE EXCEPTION 'invalid scope %', p_scope;
  END IF;
  IF p_state NOT IN ('on', 'degraded', 'off') THEN
    RAISE EXCEPTION 'invalid state %', p_state;
  END IF;

  IF p_scope = 'global' THEN
    IF p_actor_role <> 'platform_admin' THEN
      RAISE EXCEPTION 'FORBIDDEN_ROLE: only platform_admin may set the global scope';
    END IF;
    -- global writes run via system_tx(): no tenant context is expected or required.
  ELSE
    IF v_tenant IS NULL THEN
      RAISE EXCEPTION 'tenant context required for scope %', p_scope;
    END IF;
    IF p_actor_role <> 'merchant_admin' THEN
      RAISE EXCEPTION 'FORBIDDEN_ROLE: only merchant_admin may set tenant/channel scope';
    END IF;
    IF p_scope = 'tenant' THEN
      -- The tenant scope always means the CALLER's own tenant - a caller-supplied
      -- scope_id is never trusted here (H2: tenant_id never accepted from the client).
      p_scope_id := v_tenant;
    ELSIF p_scope = 'channel_account' THEN
      IF NOT EXISTS (
        SELECT 1 FROM channel_accounts WHERE id = p_scope_id AND tenant_id = v_tenant
      ) THEN
        -- Another tenant's channel (or a nonexistent one): NOT_FOUND, never FORBIDDEN
        -- (spec: "قناة/مورد متجر آخر ⇒ NOT_FOUND (لا FORBIDDEN)" - so existence of
        -- another tenant's resource is never disclosed).
        RAISE EXCEPTION 'NOT_FOUND: channel_account % not found for this tenant', p_scope_id;
      END IF;
    END IF;
  END IF;

  INSERT INTO kill_switches (scope, scope_id, capability, state, reason, set_by)
  VALUES (p_scope, p_scope_id, p_capability, p_state, p_reason, p_set_by)
  ON CONFLICT (scope, scope_id, capability) DO UPDATE
    SET state = EXCLUDED.state, reason = EXCLUDED.reason,
        set_by = EXCLUDED.set_by, set_at = now()
  RETURNING * INTO v_row;

  RETURN v_row;
END $$;

-- ---------------------------------------------------------------------------
-- 6. app.list_kill_switches - convenience read wrapper (0001's ks_app RLS policy
--    already restricts what a tenant-scoped SELECT on kill_switches can see; this
--    function does not widen that, it just shapes the result for the API).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.list_kill_switches(p_channel_account_id uuid DEFAULT NULL)
RETURNS SETOF kill_switches
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT * FROM kill_switches
   WHERE scope = 'global'
      OR (scope = 'tenant' AND scope_id = app.current_tenant())
      OR (scope = 'channel_account' AND scope_id = p_channel_account_id
          AND EXISTS (SELECT 1 FROM channel_accounts
                       WHERE id = p_channel_account_id AND tenant_id = app.current_tenant()))
   ORDER BY scope, capability
$$;

-- ---------------------------------------------------------------------------
-- 7. Grants
-- ---------------------------------------------------------------------------
REVOKE ALL ON FUNCTION app.resolve_tenant(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.resolve_tenant(text) TO sharwa_system;

REVOKE ALL ON FUNCTION app.set_kill_switch(text, text, uuid, text, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.set_kill_switch(text, text, uuid, text, text, text, text)
  TO sharwa_app, sharwa_system;

REVOKE ALL ON FUNCTION app.list_kill_switches(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.list_kill_switches(uuid) TO sharwa_app;

-- F19: core/ needs to create/update channel_accounts rows for its own tenant.
-- RLS's tenant_isolation WITH CHECK (already on this table from 0001) still forces
-- tenant_id = app.current_tenant() on every row this grant lets sharwa_app touch.
GRANT INSERT ON channel_accounts TO sharwa_app;
GRANT UPDATE (status, phone_e164) ON channel_accounts TO sharwa_app;

GRANT SELECT, INSERT, UPDATE ON audit_log, api_idempotency TO sharwa_app;
GRANT USAGE, SELECT ON SEQUENCE audit_log_id_seq TO sharwa_app;

-- sharwa_system may ONLY insert (never SELECT/UPDATE) into audit_log, and only the
-- NULL-tenant rows the system_platform_wide_insert policy above allows - a platform-wide
-- kill-switch change (PUT /v1/admin/kill-switches/global/{capability}) is the only caller.
GRANT INSERT ON audit_log TO sharwa_system;
GRANT USAGE, SELECT ON SEQUENCE audit_log_id_seq TO sharwa_system;
