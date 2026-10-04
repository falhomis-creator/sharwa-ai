-- 0018_p35_admin_tenant_list.sql - P3.5 (H104): the ONE cross-tenant read the
-- superadmin dashboard needs - "which tenants exist".
--
-- tenants is RLS'd (tenant_self), so neither sharwa_app nor sharwa_system can
-- list it. Rather than widen any policy, this SECURITY DEFINER function exposes
-- exactly five identifier/config columns (never a phone, never customer data)
-- and is executable by sharwa_system ONLY (the API's system_tx, behind a
-- platform_admin role check). Everything else the dashboard shows is read per
-- tenant, inside tenant_tx(target) - i.e. under RLS, never around it.
-- Idempotent (CREATE OR REPLACE) - H14.
CREATE OR REPLACE FUNCTION app.admin_list_tenants()
RETURNS TABLE (id uuid, platform_ref text, name text, status text, timezone text)
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT t.id, t.platform_ref, t.name, t.status, t.timezone
    FROM tenants t
   ORDER BY t.created_at, t.id
   LIMIT 1000
$$;
REVOKE ALL ON FUNCTION app.admin_list_tenants() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.admin_list_tenants() TO sharwa_system;
