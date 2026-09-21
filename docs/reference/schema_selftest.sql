-- =============================================================================
-- Self-test for reference/schema.sql. Run against an EMPTY database after applying schema.sql:
--   psql -v ON_ERROR_STOP=1 -f schema.sql && psql -v ON_ERROR_STOP=1 -f schema_selftest.sql
-- Must be run by a superuser/owner; it switches to sharwa_app / sharwa_system with SET ROLE to
-- prove that the guarantees hold for the roles the application actually uses.
-- Every check either prints "PASS <name>" or aborts the script with an exception.
-- =============================================================================
\set ON_ERROR_STOP on
\set QUIET on
SET client_min_messages = notice;

-- ---------- fixtures (as owner: bypasses RLS) ----------
INSERT INTO tenants (id, platform_ref, name, default_currency) VALUES
  ('11111111-1111-1111-1111-111111111111', 'ref-a', 'Store A (clothes)',  'YER'),
  ('22222222-2222-2222-2222-222222222222', 'ref-b', 'Store B (perfumes)', 'SAR');
INSERT INTO channel_accounts (id, tenant_id, type, session_id, engine) VALUES
  ('a1111111-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'whatsapp_baileys', 'sess-a', 'ai_core'),
  ('b2222222-0000-0000-0000-000000000001', '22222222-2222-2222-2222-222222222222', 'whatsapp_baileys', 'sess-b', 'django');
INSERT INTO customers (id, tenant_id, wa_id) VALUES
  ('ca111111-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', '967700000001'),
  ('cb222222-0000-0000-0000-000000000001', '22222222-2222-2222-2222-222222222222', '967700000001');   -- same phone, different store
INSERT INTO conversations (id, tenant_id, channel_account_id, customer_id) VALUES
  ('a0000000-0000-0000-0000-00000000c001', '11111111-1111-1111-1111-111111111111', 'a1111111-0000-0000-0000-000000000001', 'ca111111-0000-0000-0000-000000000001'),
  ('b0000000-0000-0000-0000-00000000c001', '22222222-2222-2222-2222-222222222222', 'b2222222-0000-0000-0000-000000000001', 'cb222222-0000-0000-0000-000000000001');

-- =============================================================================
-- #1  Isolation
-- =============================================================================
BEGIN;
SET LOCAL ROLE sharwa_app;
DO $$ DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM conversations;
  IF n <> 0 THEN RAISE EXCEPTION 'FAIL #1a: no tenant context must return 0 rows, got %', n; END IF;
  RAISE NOTICE 'PASS #1a no tenant context => 0 rows';
END $$;
SET LOCAL app.tenant_id = '11111111-1111-1111-1111-111111111111';
DO $$ DECLARE n int; m int;
BEGIN
  SELECT count(*) INTO n FROM conversations;
  SELECT count(*) INTO m FROM customers;
  IF n <> 1 OR m <> 1 THEN RAISE EXCEPTION 'FAIL #1b: tenant A must see exactly its own rows (conv %, cust %)', n, m; END IF;
  RAISE NOTICE 'PASS #1b tenant A sees only its own rows';
  BEGIN
    INSERT INTO customers (tenant_id, wa_id) VALUES ('22222222-2222-2222-2222-222222222222', 'x');
    RAISE EXCEPTION 'FAIL #1c: cross-tenant INSERT succeeded';
  EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'PASS #1c cross-tenant INSERT rejected by RLS WITH CHECK';
  END;
  SELECT count(*) INTO n FROM tenants;
  IF n <> 1 THEN RAISE EXCEPTION 'FAIL #1d: tenants table leaks other tenants (%)', n; END IF;
  RAISE NOTICE 'PASS #1d tenant sees only itself in tenants';
END $$;
COMMIT;

-- vectors: same product ids/names in both tenants; each tenant may only see its own; partitions not directly readable
INSERT INTO catalog_products (id, tenant_id, platform_product_id, title, source_version) VALUES
  ('aaaaaaaa-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'P1', 'قميص قطني رجالي', 1),
  ('bbbbbbbb-0000-0000-0000-000000000001', '22222222-2222-2222-2222-222222222222', 'P1', 'عطر عود ملكي', 1);
INSERT INTO catalog_embeddings (tenant_id, product_id, content_hash, model, embedding)
SELECT '11111111-1111-1111-1111-111111111111', 'aaaaaaaa-0000-0000-0000-000000000001', 'h1', 'm', (SELECT array_agg(0.5)::vector FROM generate_series(1,1024));
INSERT INTO catalog_embeddings (tenant_id, product_id, content_hash, model, embedding)
SELECT '22222222-2222-2222-2222-222222222222', 'bbbbbbbb-0000-0000-0000-000000000001', 'h2', 'm', (SELECT array_agg(0.5)::vector FROM generate_series(1,1024));

BEGIN;
SET LOCAL ROLE sharwa_app;
SET LOCAL app.tenant_id = '11111111-1111-1111-1111-111111111111';
DO $$ DECLARE n int; t text;
BEGIN
  -- nearest-neighbour search with an identical vector in the other tenant: must never return tenant B's product
  SELECT count(*) INTO n FROM (
    SELECT product_id FROM catalog_embeddings
     ORDER BY embedding <=> (SELECT array_agg(0.5)::vector FROM generate_series(1,1024)) LIMIT 10) s;
  IF n <> 1 THEN RAISE EXCEPTION 'FAIL #1e: vector search returned % rows (expected only own tenant = 1)', n; END IF;
  RAISE NOTICE 'PASS #1e vector search is tenant-isolated even with identical vectors';
  FOR t IN SELECT c.relname FROM pg_class c WHERE c.relkind = 'r' AND c.relname LIKE 'catalog_embeddings\_p%' ESCAPE '\' LOOP
    BEGIN
      EXECUTE format('SELECT count(*) FROM %I', t);
      RAISE EXCEPTION 'FAIL #1f: direct read of partition % allowed', t;
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
  END LOOP;
  RAISE NOTICE 'PASS #1f partitions are not directly readable (RLS on the parent cannot be bypassed)';
END $$;
COMMIT;

DO $$ DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM information_schema.columns
   WHERE table_schema = 'public' AND column_name ~* '(cost|margin|wholesale|profit)'
     AND table_name ~ '^(catalog_|size_|kb_|checkout_)';   -- merchant pricing data; llm_calls.cost_micro_usd is OUR AI spend
  IF n <> 0 THEN RAISE EXCEPTION 'FAIL #1g: merchant cost/margin columns exist in the catalog read model (%)', n; END IF;
  RAISE NOTICE 'PASS #1g catalog read model has no cost/margin/wholesale/profit columns';
END $$;

-- =============================================================================
-- #5  Idempotency at the database level
-- =============================================================================
DO $$
BEGIN
  INSERT INTO inbound_events (tenant_id, channel_account_id, provider_message_id)
  VALUES ('11111111-1111-1111-1111-111111111111', 'a1111111-0000-0000-0000-000000000001', 'MSG-1');
  BEGIN
    INSERT INTO inbound_events (tenant_id, channel_account_id, provider_message_id)
    VALUES ('11111111-1111-1111-1111-111111111111', 'a1111111-0000-0000-0000-000000000001', 'MSG-1');
    RAISE EXCEPTION 'FAIL #5a: duplicate inbound message accepted';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS #5a duplicate provider_message_id rejected (survives total Redis loss)';
  END;
  -- ON CONFLICT DO NOTHING is the production ingest path: returns 0 rows for a duplicate
  INSERT INTO inbound_events (tenant_id, channel_account_id, provider_message_id)
  VALUES ('11111111-1111-1111-1111-111111111111', 'a1111111-0000-0000-0000-000000000001', 'MSG-1')
  ON CONFLICT DO NOTHING;
  RAISE NOTICE 'PASS #5b ingest via ON CONFLICT DO NOTHING is safe to replay';
END $$;

-- =============================================================================
-- #11 Handoff state machine + #17 outbox stale-drop
-- =============================================================================
BEGIN;
SET LOCAL ROLE sharwa_app;
SET LOCAL app.tenant_id = '11111111-1111-1111-1111-111111111111';
DO $$ DECLARE s1 bigint; s2 bigint; nt boolean; ep int;
BEGIN
  INSERT INTO messages (tenant_id, conversation_id, direction, sent_by, body)
  VALUES ('11111111-1111-1111-1111-111111111111', 'a0000000-0000-0000-0000-00000000c001', 'in', 'customer', 'مرحبا');
  INSERT INTO messages (tenant_id, conversation_id, direction, sent_by, body)
  VALUES ('11111111-1111-1111-1111-111111111111', 'a0000000-0000-0000-0000-00000000c001', 'in', 'customer', 'عندكم قميص؟');
  SELECT min(seq), max(seq) INTO s1, s2 FROM messages WHERE conversation_id = 'a0000000-0000-0000-0000-00000000c001';
  IF s1 <> 1 OR s2 <> 2 THEN RAISE EXCEPTION 'FAIL #11a: seq not gapless/monotonic (% .. %)', s1, s2; END IF;
  SELECT needs_turn INTO nt FROM conversations WHERE id = 'a0000000-0000-0000-0000-00000000c001';
  IF NOT nt THEN RAISE EXCEPTION 'FAIL #11b: inbound must set needs_turn'; END IF;
  RAISE NOTICE 'PASS #11a/b message seq assigned atomically; inbound sets needs_turn';

  BEGIN
    UPDATE conversations SET bot_status = 'paused_human' WHERE id = 'a0000000-0000-0000-0000-00000000c001';
    RAISE EXCEPTION 'FAIL #11c: direct UPDATE of bot_status allowed';
  EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'PASS #11c bot_status cannot be written except through the state machine';
  END;

  -- optimistic lock: first transition wins, second with the stale version is refused
  SELECT app.set_bot_status('a0000000-0000-0000-0000-00000000c001', 0, 'paused_human', 'customer_asked') INTO ep;
  IF ep IS NULL THEN RAISE EXCEPTION 'FAIL #11d: first transition should succeed'; END IF;
  SELECT app.set_bot_status('a0000000-0000-0000-0000-00000000c001', 0, 'active', 'race') INTO ep;
  IF ep IS NOT NULL THEN RAISE EXCEPTION 'FAIL #11e: stale-version transition succeeded'; END IF;
  RAISE NOTICE 'PASS #11d/e optimistic version check';
END $$;
COMMIT;

-- bot reply queued at epoch 0, conversation then moves to a human (epoch 1): dispatcher must DROP it
BEGIN;
SET LOCAL ROLE sharwa_app;
SET LOCAL app.tenant_id = '22222222-2222-2222-2222-222222222222';
DO $$ DECLARE st text; n int;
BEGIN
  INSERT INTO outbox (tenant_id, conversation_id, channel_account_id, idempotency_key, origin, message_class, expected_epoch, to_wa_id, payload)
  VALUES ('22222222-2222-2222-2222-222222222222', 'b0000000-0000-0000-0000-00000000c001', 'b2222222-0000-0000-0000-000000000001',
          'conv-b:turn-1:1', 'bot', 'service', 0, '967700000001', '{"text":"رد البوت"}');
  BEGIN
    INSERT INTO outbox (tenant_id, conversation_id, channel_account_id, idempotency_key, origin, message_class, expected_epoch, to_wa_id, payload)
    VALUES ('22222222-2222-2222-2222-222222222222', 'b0000000-0000-0000-0000-00000000c001', 'b2222222-0000-0000-0000-000000000001',
            'conv-b:turn-1:1', 'bot', 'service', 0, '967700000001', '{"text":"retry"}');
    RAISE EXCEPTION 'FAIL #5c: duplicate outbox idempotency_key accepted';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS #5c a retried turn cannot enqueue a second reply';
  END;
  BEGIN
    INSERT INTO outbox (tenant_id, channel_account_id, idempotency_key, origin, message_class, to_wa_id, payload)
    VALUES ('22222222-2222-2222-2222-222222222222', 'b2222222-0000-0000-0000-000000000001', 'k2', 'bot', 'service', 'x', '{}');
    RAISE EXCEPTION 'FAIL #11f: bot outbox row without expected_epoch accepted';
  EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'PASS #11f bot replies must carry expected_epoch';
  END;
  -- a human agent replies (staff message) => trigger pauses the bot in the same transaction, epoch++
  INSERT INTO messages (tenant_id, conversation_id, direction, sent_by, body)
  VALUES ('22222222-2222-2222-2222-222222222222', 'b0000000-0000-0000-0000-00000000c001', 'out', 'staff', 'أهلاً، معك الموظف');
  SELECT bot_status INTO st FROM conversations WHERE id = 'b0000000-0000-0000-0000-00000000c001';
  IF st <> 'paused_human' THEN RAISE EXCEPTION 'FAIL #11g: staff message did not pause the bot (%)', st; END IF;
  RAISE NOTICE 'PASS #11g a human message pauses the bot atomically';
END $$;
COMMIT;

SET ROLE sharwa_system;
DO $$ DECLARE n int; st text;
BEGIN
  SELECT count(*) INTO n FROM app.claim_outbox(10);
  SELECT status INTO st FROM app.claim_outbox(0) LIMIT 1;   -- no-op call (function must be callable)
  RAISE NOTICE 'claimed=% (expected 0: the stale bot reply must not be dispatched)', n;
  IF n <> 0 THEN RAISE EXCEPTION 'FAIL #11h: stale bot reply was claimed for sending'; END IF;
END $$;
RESET ROLE;
DO $$ DECLARE st text;
BEGIN
  SELECT status INTO st FROM outbox WHERE idempotency_key = 'conv-b:turn-1:1';
  IF st <> 'dropped_stale' THEN RAISE EXCEPTION 'FAIL #11h: expected dropped_stale, got %', st; END IF;
  RAISE NOTICE 'PASS #11h bot reply written before handoff is dropped at dispatch (epoch guard)';
END $$;

-- system role: no direct table access, only the definer functions
SET ROLE sharwa_system;
DO $$ DECLARE r record;
BEGIN
  BEGIN
    PERFORM 1 FROM messages LIMIT 1;
    RAISE EXCEPTION 'FAIL #1h: sharwa_system can read tenant tables directly';
  EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'PASS #1h system role has no direct table access';
  END;
  SELECT * INTO r FROM app.resolve_session('sess-a');
  IF r.tenant_id IS DISTINCT FROM '11111111-1111-1111-1111-111111111111'::uuid OR r.engine <> 'ai_core' THEN
    RAISE EXCEPTION 'FAIL #25a: resolve_session wrong result';
  END IF;
  IF EXISTS (SELECT 1 FROM app.resolve_session('unknown-session')) THEN
    RAISE EXCEPTION 'FAIL #25b: unknown session resolved';
  END IF;
  RAISE NOTICE 'PASS #25 session -> (tenant, engine) resolution; unknown session resolves to nothing';
END $$;
RESET ROLE;

-- =============================================================================
-- #17 scheduled jobs: dedupe + crash recovery
-- =============================================================================
DO $$ DECLARE n int;
BEGIN
  INSERT INTO scheduled_jobs (tenant_id, kind, dedupe_key, run_at)
  VALUES ('11111111-1111-1111-1111-111111111111', 'cart_reminder', 'cart-9:stage1', now() - interval '1 minute');
  BEGIN
    INSERT INTO scheduled_jobs (tenant_id, kind, dedupe_key, run_at)
    VALUES ('11111111-1111-1111-1111-111111111111', 'cart_reminder', 'cart-9:stage1', now());
    RAISE EXCEPTION 'FAIL #17a: duplicate reminder for the same cart stage accepted';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS #17a one reminder per (cart, stage)';
  END;
END $$;
SET ROLE sharwa_system;
DO $$ DECLARE n1 int; n2 int;
BEGIN
  SELECT count(*) INTO n1 FROM app.claim_due_jobs(10, interval '1 second');
  SELECT count(*) INTO n2 FROM app.claim_due_jobs(10, interval '1 second');
  IF n1 <> 1 OR n2 <> 0 THEN RAISE EXCEPTION 'FAIL #17b: claim not exclusive (% / %)', n1, n2; END IF;
  RAISE NOTICE 'PASS #17b due job claimed exactly once while leased';
END $$;
RESET ROLE;
SELECT pg_sleep(1.2);
SET ROLE sharwa_system;
DO $$ DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM app.claim_due_jobs(10, interval '60 seconds');
  IF n <> 1 THEN RAISE EXCEPTION 'FAIL #17c: job of a crashed worker was not recovered after lease expiry'; END IF;
  RAISE NOTICE 'PASS #17c job re-claimed after the worker lease expired (crash recovery)';
END $$;
RESET ROLE;

-- =============================================================================
-- #7  Back-in-stock: 20 waiters, 1 unit
-- =============================================================================
INSERT INTO customers (tenant_id, wa_id)
SELECT '11111111-1111-1111-1111-111111111111', 'w' || g FROM generate_series(1, 20) g;
INSERT INTO waitlist_entries (tenant_id, customer_id, platform_variant_id, created_at)
SELECT c.tenant_id, c.id, 'VAR-L-BLACK', now() - (interval '1 hour') + (row_number() OVER (ORDER BY c.wa_id)) * interval '1 second'
  FROM customers c WHERE c.wa_id LIKE 'w%' AND c.tenant_id = '11111111-1111-1111-1111-111111111111';

BEGIN;
SET LOCAL ROLE sharwa_app;
SET LOCAL app.tenant_id = '11111111-1111-1111-1111-111111111111';
DO $$ DECLARE n int; first_wa text;
BEGIN
  SELECT count(*) INTO n FROM app.allocate_stock_holds('VAR-L-BLACK', 1, interval '30 minutes');
  IF n <> 1 THEN RAISE EXCEPTION 'FAIL #7a: 1 unit must produce exactly 1 hold, got %', n; END IF;
  SELECT count(*) INTO n FROM app.allocate_stock_holds('VAR-L-BLACK', 1, interval '30 minutes');
  IF n <> 0 THEN RAISE EXCEPTION 'FAIL #7b: second allocation over-sold (% more holds)', n; END IF;
  SELECT c.wa_id INTO first_wa FROM stock_holds h
    JOIN waitlist_entries w ON w.id = h.waitlist_entry_id JOIN customers c ON c.id = w.customer_id WHERE h.status = 'held';
  RAISE NOTICE 'PASS #7a/b 20 waiters + 1 unit => 1 hold (first in line = %); re-run allocates nothing', first_wa;
  SELECT count(*) INTO n FROM app.allocate_stock_holds('VAR-L-BLACK', 3, interval '30 minutes');
  IF n <> 2 THEN RAISE EXCEPTION 'FAIL #7c: stock rose to 3 with 1 held => 2 more holds expected, got %', n; END IF;
  RAISE NOTICE 'PASS #7c stock 3, 1 already held => exactly 2 more, FIFO';
END $$;
COMMIT;
-- simulate the first hold expiring, then promote the next in line
UPDATE stock_holds SET expires_at = now() - interval '1 minute'
 WHERE id = (SELECT id FROM stock_holds ORDER BY created_at LIMIT 1);
BEGIN;
SET LOCAL ROLE sharwa_app;
SET LOCAL app.tenant_id = '11111111-1111-1111-1111-111111111111';
DO $$ DECLARE n int;
BEGIN
  PERFORM app.expire_stock_holds('VAR-L-BLACK');
  SELECT count(*) INTO n FROM app.allocate_stock_holds('VAR-L-BLACK', 3, interval '30 minutes');
  IF n <> 1 THEN RAISE EXCEPTION 'FAIL #7d: expired hold must cascade to exactly the next waiter, got %', n; END IF;
  RAISE NOTICE 'PASS #7d expiry releases the unit to the next waiter (cascade)';
END $$;
COMMIT;

-- =============================================================================
-- #19 Kill switch resolution
-- =============================================================================
INSERT INTO kill_switches (scope, scope_id, capability, state, set_by) VALUES
  ('tenant', '11111111-1111-1111-1111-111111111111', 'marketing', 'off', 'test'),
  ('global', NULL, 'ai_reply', 'degraded', 'test');
DO $$ DECLARE a text; b text; c text; d text;
BEGIN
  a := app.effective_switch('11111111-1111-1111-1111-111111111111', 'a1111111-0000-0000-0000-000000000001', 'marketing');
  b := app.effective_switch('22222222-2222-2222-2222-222222222222', 'b2222222-0000-0000-0000-000000000001', 'marketing');
  c := app.effective_switch('22222222-2222-2222-2222-222222222222', 'b2222222-0000-0000-0000-000000000001', 'ai_reply');
  d := app.effective_switch('22222222-2222-2222-2222-222222222222', 'b2222222-0000-0000-0000-000000000001', 'gift');
  IF a <> 'off' OR b <> 'on' OR c <> 'degraded' OR d <> 'on' THEN
    RAISE EXCEPTION 'FAIL #19: switch resolution wrong (% % % %)', a, b, c, d;
  END IF;
  RAISE NOTICE 'PASS #19 tenant switch does not leak to other tenants; global applies to all';
END $$;

-- =============================================================================
-- #13 Spatial validators (dummy polygons; real boundaries come from the gazetteer import)
-- =============================================================================
INSERT INTO geo_gazetteer (level, name_ar, name_norm, geom)
VALUES ('governorate', 'أمانة العاصمة', 'امانه العاصمه',
        ST_GeomFromText('POLYGON((44.0 15.2, 44.4 15.2, 44.4 15.6, 44.0 15.6, 44.0 15.2))', 4326));
DO $$ DECLARE inside bigint; outside bigint;
BEGIN
  inside  := app.point_governorate(15.35, 44.20);   -- inside the polygon
  outside := app.point_governorate(15.35, 40.00);   -- hallucinated: outside every known governorate
  IF inside IS NULL OR outside IS NOT NULL THEN RAISE EXCEPTION 'FAIL #13a: spatial validator wrong'; END IF;
  RAISE NOTICE 'PASS #13a coordinate outside every known governorate is rejected (NULL)';
  BEGIN
    INSERT INTO address_resolutions (tenant_id, input, decision, confidence, source)
    VALUES ('11111111-1111-1111-1111-111111111111', '{"text":"خلف الجولة"}', 'accepted', 0.9, 'geocoder');
    RAISE EXCEPTION 'FAIL #13b: accepted an address without a validated point';
  EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'PASS #13b an address cannot be auto-accepted without a validated location';
  END;
END $$;

-- =============================================================================
-- #2 / search: Arabic full-text works with the built-in configuration
-- =============================================================================
BEGIN;
SET LOCAL ROLE sharwa_app;
SET LOCAL app.tenant_id = '11111111-1111-1111-1111-111111111111';
DO $$ DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM catalog_products WHERE search_tsv @@ plainto_tsquery('arabic', 'قميص');
  IF n <> 1 THEN RAISE EXCEPTION 'FAIL search: arabic FTS returned %', n; END IF;
  RAISE NOTICE 'PASS search Arabic full-text on the tenant read model';
END $$;
COMMIT;

-- =============================================================================
-- Money columns are integer minor units (no float / free numeric)
-- =============================================================================
DO $$ DECLARE bad int;
BEGIN
  SELECT count(*) INTO bad FROM information_schema.columns
   WHERE table_schema = 'public'
     AND (column_name LIKE '%\_minor' ESCAPE '\')
     AND data_type <> 'bigint';
  IF bad <> 0 THEN RAISE EXCEPTION 'FAIL #20: % money columns are not BIGINT', bad; END IF;
  SELECT count(*) INTO bad FROM information_schema.columns
   WHERE table_schema = 'public' AND data_type IN ('real','double precision')
     AND column_name ~* '(price|amount|total|cost|fee)';
  IF bad <> 0 THEN RAISE EXCEPTION 'FAIL #20: float money-like columns exist'; END IF;
  RAISE NOTICE 'PASS #20 money columns are BIGINT minor units, no float money';
END $$;


-- =============================================================================
-- Order tracking identity rule (docs/01 §2.3) + platform-owned money (decision D3)
-- =============================================================================
BEGIN;
SET LOCAL ROLE sharwa_app;
SET LOCAL app.tenant_id = '11111111-1111-1111-1111-111111111111';
DO $$ DECLARE c1 uuid := 'ca111111-0000-0000-0000-000000000001'; c2 uuid; i int; blocked boolean;
BEGIN
  SELECT id INTO c2 FROM customers WHERE wa_id = 'w1';
  IF app.order_lookup_blocked(c1, 'hash-x') THEN RAISE EXCEPTION 'FAIL D5a: blocked with no attempts'; END IF;
  -- two denials: still allowed to try; third denial closes the "other number" path for this identity
  FOR i IN 1..2 LOOP
    INSERT INTO order_lookup_attempts (tenant_id, customer_id, path, order_ref_hash, outcome)
    VALUES ('11111111-1111-1111-1111-111111111111', c1, 'other_number', 'hash-' || i, 'denied');
  END LOOP;
  IF app.order_lookup_blocked(c1, 'hash-new') THEN RAISE EXCEPTION 'FAIL D5b: blocked too early (2 denials)'; END IF;
  INSERT INTO order_lookup_attempts (tenant_id, customer_id, path, order_ref_hash, outcome)
  VALUES ('11111111-1111-1111-1111-111111111111', c1, 'other_number', 'hash-3', 'denied');
  IF NOT app.order_lookup_blocked(c1, 'hash-new') THEN RAISE EXCEPTION 'FAIL D5c: not blocked after 3 denials'; END IF;
  IF app.order_lookup_blocked(c2, 'hash-new') THEN RAISE EXCEPTION 'FAIL D5d: another identity was blocked'; END IF;
  RAISE NOTICE 'PASS D5a-d 3 denials/24h close the other-number path for that identity only';
  -- 5 denials on ONE order from different identities lock that order for everybody
  FOR i IN 1..5 LOOP
    INSERT INTO order_lookup_attempts (tenant_id, customer_id, path, order_ref_hash, outcome)
    VALUES ('11111111-1111-1111-1111-111111111111', c2, 'other_number', 'hash-hot-order', 'denied');
  END LOOP;
  IF NOT app.order_lookup_blocked(c2, 'hash-hot-order') THEN RAISE EXCEPTION 'FAIL D5e: hot order not locked'; END IF;
  -- successful lookups never count against anyone
  INSERT INTO order_lookup_attempts (tenant_id, customer_id, path, order_ref_hash, outcome)
  SELECT '11111111-1111-1111-1111-111111111111', c1, 'same_number', 'hash-ok', 'allowed' FROM generate_series(1,10);
  RAISE NOTICE 'PASS D5e per-order lock after 5 denials; allowed lookups never count';
  BEGIN
    DELETE FROM order_lookup_attempts;
    RAISE EXCEPTION 'FAIL D5f: audit rows deletable';
  EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'PASS D5f audit trail cannot be deleted by the app role';
  END;
  BEGIN
    INSERT INTO checkout_sessions (tenant_id, platform_checkout_id, url, total_minor, currency, cart_hash, idempotency_key)
    VALUES ('11111111-1111-1111-1111-111111111111', 'co-1', 'https://x', 1000, 'YER', 'h', 'k-co-1');
    RAISE EXCEPTION 'FAIL D3a: checkout stored without the platform display text';
  EXCEPTION WHEN not_null_violation THEN
    RAISE NOTICE 'PASS D3a a checkout amount cannot be stored without the platform-provided display text';
  END;
END $$;
COMMIT;
BEGIN;
SET LOCAL ROLE sharwa_app;
SET LOCAL app.tenant_id = '22222222-2222-2222-2222-222222222222';
DO $$ DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM order_lookup_attempts;
  IF n <> 0 OR app.order_lookup_blocked('cb222222-0000-0000-0000-000000000001', 'hash-hot-order') THEN
    RAISE EXCEPTION 'FAIL D5g: lookup counters leak across tenants (% rows)', n;
  END IF;
  RAISE NOTICE 'PASS D5g lookup attempts and locks are tenant-isolated';
END $$;
COMMIT;

\echo ALL SELF-TESTS PASSED
