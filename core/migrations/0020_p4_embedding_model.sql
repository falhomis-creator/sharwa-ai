-- =============================================================================
-- 0020_p4_embedding_model.sql - P4 Task 15 / F-P4-13 (forward-only, H14).
--
-- 0009's candidate lister re-embeds a product only when its content_hash
-- changes. With a second embedding provider (P4 Task 15) that is not enough: a
-- vector written by the previous model would never be replaced, and comparing
-- it with a query vector from the new model is meaningless (two unrelated
-- vector spaces). The lister now also returns products whose stored vector was
-- written by a DIFFERENT model than the one the worker runs (p_model).
--
-- Same contract as 0009 otherwise: SECURITY DEFINER, pointers only
-- (tenant_id, product_id), never tenant content; EXECUTE for sharwa_system only.
-- The one-argument version is dropped: its only caller (app.workers.embed via
-- repos_catalog) passes the model from this release on.
-- =============================================================================

DROP FUNCTION IF EXISTS app.list_products_needing_embedding(integer);

CREATE OR REPLACE FUNCTION app.list_products_needing_embedding(p_limit integer, p_model text)
RETURNS TABLE (tenant_id uuid, product_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT p.tenant_id, p.id
    FROM catalog_products p
   WHERE p.active = true
     AND (
       NOT EXISTS (
         SELECT 1 FROM catalog_embeddings ce
          WHERE ce.tenant_id = p.tenant_id AND ce.product_id = p.id
       )
       OR EXISTS (
         SELECT 1 FROM catalog_embeddings ce
          WHERE ce.tenant_id = p.tenant_id AND ce.product_id = p.id
            AND (
              ce.content_hash IS DISTINCT FROM
                md5(p.title || E'\n' || coalesce(p.description, ''))
              OR ce.model IS DISTINCT FROM p_model
            )
       )
     )
   ORDER BY p.tenant_id, p.id
   LIMIT p_limit
$$;

REVOKE ALL ON FUNCTION app.list_products_needing_embedding(integer, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.list_products_needing_embedding(integer, text) TO sharwa_system;
