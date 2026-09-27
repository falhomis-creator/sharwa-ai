-- =============================================================================
-- 0005_p1_inbox.sql - P1.3 additive migration (forward-only, H14)
--
-- Every P1.3 table already exists in 0001_baseline.sql (conversations, messages,
-- internal_notes, staff_members, inbox_events, tenant_counters, outbox) and
-- 0002_p0_api.sql (api_idempotency). app.next_inbox_seq(), app.set_bot_status()
-- and the app.trg_messages_seq() staff-reply pause trigger all already exist in
-- 0001_baseline.sql. This migration does NOT create any existing table - it only
-- adds the two indexes the P1.3 cursor-paginated reads need (no OFFSET anywhere).
--
-- Never edit 0001_baseline.sql / 0002_p0_api.sql / 0003 / 0004 / docs/reference.
-- =============================================================================

-- GET /v1/conversations?status=&assigned=&cursor=&limit= walks
-- (last_message_at DESC, id DESC) with a keyset cursor and no OFFSET.
CREATE INDEX IF NOT EXISTS conversations_inbox_idx
    ON conversations (tenant_id, bot_status, last_message_at DESC, id DESC);

-- GET /v1/conversations/{id}/notes lists a conversation's internal notes in
-- created_at order (internal_notes is read ONLY by the note routes, never by a
-- send path - enforced structurally by static_gate S5).
CREATE INDEX IF NOT EXISTS internal_notes_conversation_idx
    ON internal_notes (conversation_id, created_at);
