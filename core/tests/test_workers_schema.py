"""WalEntry validation + message-type mapping tests (P1.1.2, §6.1 contract).

Pure pydantic tests: extra='ignore' tolerance is measured (unknown fields are
reported), and a wholly-unknown type maps to 'unsupported' rather than being
dropped. No database/Redis needed.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.workers import schema


def _text_entry(**extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "v": 1,
        "session_id": "sess-1",
        "provider_message_id": "abc123",
        "type": "text",
        "ts": 1758900000000,
        "identity": {"jid_raw": "9677@s.whatsapp.net", "addressing": "pn",
                     "wa_id": "9677", "phone_e164": "+9677"},
        "text": "hello",
    }
    base.update(extra)
    return base


def test_parse_text_entry_roundtrips():
    entry, unknown = schema.parse_entry(_text_entry())
    assert entry.type == "text"
    assert entry.session_id == "sess-1"
    assert entry.identity is not None and entry.identity.wa_id == "9677"
    assert unknown == frozenset()


def test_unknown_top_level_fields_are_ignored_but_reported():
    entry, unknown = schema.parse_entry(_text_entry(future_field="x", another_new=1))
    assert entry.type == "text"
    assert unknown == frozenset({"future_field", "another_new"})


def test_unknown_nested_fields_do_not_raise():
    raw = _text_entry()
    raw["identity"]["phone_hint"] = "hi"  # type: ignore[index]
    entry, unknown = schema.parse_entry(raw)
    assert entry.identity is not None
    assert unknown == frozenset()


def test_missing_required_session_id_raises():
    with pytest.raises(ValidationError):
        schema.parse_entry({"v": 1, "type": "text"})


def test_identity_update_shape_parses():
    entry, _ = schema.parse_entry({
        "v": 1, "session_id": "sess-1", "type": "identity_update",
        "provider_message_id": "identity:999@lid:+20111", "ts": 1,
        "lid": "999@lid", "phone_e164": "+20111",
    })
    assert entry.lid == "999@lid"
    assert entry.phone_e164 == "+20111"


def test_human_takeover_signal_shape_parses():
    entry, _ = schema.parse_entry({
        "v": 1, "session_id": "sess-1", "type": "human_takeover_signal",
        "provider_message_id": "wa-id", "ts": 1, "direction": "outbound_human",
        "identity": {"jid_raw": "x", "addressing": "pn", "wa_id": "9", "phone_e164": None},
        "text": "رد الموظف",
    })
    assert entry.direction == "outbound_human"
    assert entry.identity is not None and entry.identity.wa_id == "9"


def test_message_type_for_known_and_unknown():
    assert schema.message_type_for("text") == "text"
    assert schema.message_type_for("location") == "location"
    assert schema.message_type_for("brand_new_kind") == "unsupported"
