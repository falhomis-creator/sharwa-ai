-- 0016_p3_consent.sql - P3.3 consent capture & management (H94-H99).
--
-- Idempotent (H14): REVOKE/GRANT and CREATE INDEX IF NOT EXISTS are naturally
-- idempotent; the CHECK constraint goes through a DO block + pg_constraint so
-- re-running never duplicates it. 0001-0015 left untouched.
--
-- H94: consents is APPEND-ONLY for the app role - the current state is the
-- latest row (latest-wins) and that is enforced by database privileges
-- (UPDATE/DELETE revoked from sharwa_app), never by discipline.
-- H95: `source` is a closed list; checkout_optin/import are allowed by the
-- constraint but blocked from marketing eligibility in
-- repos_policy.read_latest_consent until the owner's legal decision (OQ-P3-14).

-- 1. Append-only (H94): the app role keeps SELECT + INSERT only.
REVOKE UPDATE, DELETE ON consents FROM sharwa_app;
GRANT SELECT, INSERT ON consents TO sharwa_app;

-- 2. Closed source list (H95). A legacy row outside the list is REPORTED (a
--    WARNING, visible in the migration log) and NEVER deleted; the constraint
--    then stays unenforced until the owner resolves the data. The S27-c static
--    gate compares this list to CONSENT_SOURCES in app/db/repos_consent.py
--    verbatim - change both together or not at all.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'consents_source_closed_list') THEN
    IF EXISTS (SELECT 1 FROM consents WHERE source NOT IN (
        'customer_message_optin', 'customer_message_optout', 'waitlist_join',
        'checkout_optin', 'import', 'operator')) THEN
      RAISE WARNING 'consents_source_closed_list NOT added: legacy consents rows carry sources outside the closed list (reported, not deleted)';
    ELSE
      ALTER TABLE consents ADD CONSTRAINT consents_source_closed_list CHECK (source IN (
          'customer_message_optin', 'customer_message_optout', 'waitlist_join',
          'checkout_optin', 'import', 'operator'));
    END IF;
  END IF;
END $$;

-- 3. The latest-wins reader orders by (created_at DESC, id DESC) - give it the
--    full key. The 0001 index (same prefix, no id tiebreaker) stays; this one
--    is the deterministic-ordering index.
CREATE INDEX IF NOT EXISTS consents_latest_wins_idx
  ON consents (tenant_id, customer_id, scope, created_at DESC, id DESC);
