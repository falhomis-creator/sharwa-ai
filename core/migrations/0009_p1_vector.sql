-- =============================================================================
-- 0009_p1_vector.sql - P1.5b embedding candidate fetch (forward-only, H14).
--
-- catalog_embeddings already exists in 0001_baseline.sql (hash-partitioned on
-- tenant_id into 8 partitions, UNIQUE (tenant_id, product_id), embedding
-- vector(1024)). Nothing in that schema needs to change for P1.5b. The ONLY
-- thing missing is the cross-tenant reader the embed worker needs: a SECURITY
-- DEFINER function that returns POINTERS only (tenant_id, product_id) for
-- products whose embedding is missing or whose content_hash no longer matches
-- `title + description` - never any tenant content.
--
-- content_hash is `md5(title || E'\n' || coalesce(description, ''))`, which MUST
-- stay byte-identical to repos_catalog.content_hash() in Python so the writer and
-- this lister agree on "needs re-embedding" and never loop. MD5 (not sha256)
-- because it is built into PostgreSQL; for a content fingerprint a collision only
-- causes one redundant re-embed, never a correctness bug.
--
-- No ANN/HNSW index in this batch (the architecture defers it until real data
-- can size it; the exact scan inside the tenant's hash partition is the adopted
-- method now).
-- =============================================================================

CREATE OR REPLACE FUNCTION app.list_products_needing_embedding(p_limit integer)
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
            AND ce.content_hash IS DISTINCT FROM
                md5(p.title || E'\n' || coalesce(p.description, ''))
       )
     )
   ORDER BY p.tenant_id, p.id
   LIMIT p_limit
$$;

REVOKE ALL ON FUNCTION app.list_products_needing_embedding(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.list_products_needing_embedding(integer) TO sharwa_system;
