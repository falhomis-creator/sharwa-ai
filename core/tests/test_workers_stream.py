"""Pure helpers of app/workers/stream.py (P1.1.1) - no live Redis needed.

Only the field-block normalizer and the JSON parser are under test here; the
stream commands themselves run against real redis-durable in the Docker-based
integration suite (P1.1.8), never mocked (H7).
"""
from __future__ import annotations

import pytest

from app.workers.stream import PermanentError, _fields_to_dict, parse_data_field


def test_fields_to_dict_from_flat_list():
    assert _fields_to_dict(["data", '{"x":1}', "k", "v"]) == {"data": '{"x":1}', "k": "v"}


def test_fields_to_dict_passes_dict_through():
    assert _fields_to_dict({"data": "{}"}) == {"data": "{}"}


def test_parse_data_field_roundtrips():
    obj, raw = parse_data_field({"data": '{"type": "text", "session_id": "s"}'})
    assert obj == {"type": "text", "session_id": "s"}
    assert raw == '{"type": "text", "session_id": "s"}'


def test_parse_data_field_missing_data_is_permanent():
    with pytest.raises(PermanentError):
        parse_data_field({})


def test_parse_data_field_corrupt_json_is_permanent():
    with pytest.raises(PermanentError):
        parse_data_field({"data": "{not json"})
