-- =============================================================================
-- 0021_p4_gift_carts.sql - P4 Task 14: gift baskets the customer can pay for
-- (forward-only, H14).
--
-- The gift curator picks WHICH items (never a price or a total - the platform
-- prices at checkout). Each proposed basket is stored here and the customer
-- gets a link https://<platform>/checkout/gift/<id> (owner decision for
-- OQ-P4-06, 2026-10-09). The platform resolves <id> through the signed
-- POST /webhooks/platform/gift-cart and builds the real checkout itself.
--
-- items: [{"platform_product_id": "...", "platform_variant_id": "...", "qty": 1}]
-- A link is valid until expires_at (7 days by default); an expired cart is
-- answered as not found.
-- =============================================================================

CREATE TABLE IF NOT EXISTS gift_carts (
  id               uuid NOT NULL DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id),
  conversation_id  uuid REFERENCES conversations(id) ON DELETE CASCADE,
  items            jsonb NOT NULL CHECK (jsonb_typeof(items) = 'array' AND jsonb_array_length(items) BETWEEN 1 AND 4),
  currency         char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  budget_minor     bigint NOT NULL CHECK (budget_minor > 0),
  created_at       timestamptz NOT NULL DEFAULT now(),
  expires_at       timestamptz NOT NULL DEFAULT now() + interval '7 days',
  PRIMARY KEY (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS gift_carts_created_idx ON gift_carts (created_at);

-- RLS is EXPLICIT (the F-P2-06 lesson: new tables opt in themselves).
ALTER TABLE gift_carts ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname = 'public' AND tablename = 'gift_carts'
                     AND policyname = 'tenant_isolation') THEN
    CREATE POLICY tenant_isolation ON gift_carts
      USING (tenant_id = app.current_tenant())
      WITH CHECK (tenant_id = app.current_tenant());
  END IF;
END $$;
-- Append-only for the app role: a proposed basket is never edited.
GRANT SELECT, INSERT ON gift_carts TO sharwa_app;
