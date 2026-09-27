-- =============================================================================
-- 0003_p1_outbound.sql - P1.2 additive migration (forward-only, H14)
--
-- T7: a partial unique index on messages (conversation_id, provider_message_id)
-- WHERE provider_message_id IS NOT NULL. This is the SECOND idempotency layer
-- for the outbound path (disaster #5/H22): the evt:{shard} `sent` handler
-- updates outbox with a state-transition guard AND inserts the outbound message
-- row, but a crash between those two leaves a window; this index makes a
-- re-delivered `sent` event collapse to a unique violation instead of a
-- duplicate outbound message row (see §5.2 / §6.4, A12).
--
-- Never edit 0001_baseline.sql / 0002_p0_api.sql / docs/reference/schema.sql.
-- =============================================================================

CREATE UNIQUE INDEX IF NOT EXISTS messages_provider_message_uniq_idx
    ON messages (conversation_id, provider_message_id)
    WHERE provider_message_id IS NOT NULL;
