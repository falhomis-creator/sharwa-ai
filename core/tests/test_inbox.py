"""Pure P1.3 inbox tests (PROMPT §6, G2/G9/G10) - no DB/Redis.

The RBAC permission matrix, the inbox_events payload whitelist (no text/phone),
and the closed error-code catalog are all pure and verified here. The employee
reply's DB-trigger + outbox ordering (G3) is a DB-transaction guarantee that
needs Docker and is recorded as debt in the report.
"""
from __future__ import annotations

import uuid

import pytest

from app.api.errors import ERROR_CODES, _STATUS_BY_CODE, ApiError
from app.db.repos_inbox import INBOX_EVENT_ALLOWED_KEYS, write_inbox_event
from app.security.permissions import PERMISSIONS

_ALL_PERMISSIONS = (
    "conversation.read", "conversation.reply", "conversation.handoff",
    "conversation.assign", "conversation.assign_others", "note",
)

# PROMPT §5.1 matrix (6 permissions x 4 roles = 24 cases).
_EXPECTED = {
    "owner": set(_ALL_PERMISSIONS),
    "manager": set(_ALL_PERMISSIONS),
    "agent": {"conversation.read", "conversation.reply", "conversation.handoff",
              "conversation.assign", "note"},
    "viewer": {"conversation.read"},
}


def test_permission_matrix_24_cases():
    assert set(PERMISSIONS) == {"owner", "manager", "agent", "viewer"}
    assert len(_ALL_PERMISSIONS) == 6
    for role in _EXPECTED:
        for permission in _ALL_PERMISSIONS:
            assert (permission in PERMISSIONS[role]) == (permission in _EXPECTED[role]), (
                f"matrix mismatch: {role} / {permission}"
            )


def test_inbox_event_payload_whitelist_has_no_text_or_phone():
    # H20/G9: message text and phone must never appear in an inbox_events payload.
    assert "text" not in INBOX_EVENT_ALLOWED_KEYS
    assert "body" not in INBOX_EVENT_ALLOWED_KEYS
    assert "phone" not in INBOX_EVENT_ALLOWED_KEYS
    assert "phone_e164" not in INBOX_EVENT_ALLOWED_KEYS
    assert "wa_id" not in INBOX_EVENT_ALLOWED_KEYS


def test_write_inbox_event_rejects_non_whitelisted_key():
    with pytest.raises(ValueError):
        write_inbox_event(
            None, tenant_id=uuid.uuid4(), event_type="message.new",
            payload={"conversation_id": "x", "text": "must never leak"},
        )


def test_inbox_event_payload_shapes_are_whitelisted():
    # The four documented payloads (PROMPT §5.5) must stay within the whitelist.
    message_new = {"conversation_id", "message_id", "seq", "direction", "sent_by", "type"}
    message_status = {"conversation_id", "message_id", "status", "provider_message_id"}
    conversation_updated = {"conversation_id", "bot_status", "epoch", "version", "handoff_reason"}
    handoff_requested = {"conversation_id", "reason"}
    for payload in (message_new, message_status, conversation_updated, handoff_requested):
        assert payload <= INBOX_EVENT_ALLOWED_KEYS, payload


def test_new_error_codes_are_declared():
    for code in (
        "STAFF_NOT_PROVISIONED", "FORBIDDEN_PERMISSION", "CONVERSATION_CLOSED",
        "CONFLICT_VERSION", "IDEMPOTENCY_KEY_REQUIRED", "IDEMPOTENCY_KEY_REUSED",
    ):
        assert code in ERROR_CODES
        assert code in _STATUS_BY_CODE


def test_api_error_rejects_undeclared_code():
    with pytest.raises(ValueError):
        ApiError("NOT_A_REAL_CODE")
