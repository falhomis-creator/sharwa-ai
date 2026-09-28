"""Pure track_order tool tests (P1.7 §10, H50/H52/H53) with a recording FakeCommerce."""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from app.tools import track_order
from app.tools.registry import ToolContext
from tests.fake_commerce import FakeCommerce


class _Settings:
    order_ref_pattern = re.compile(r"\b\d{4,8}\b")
    order_lookup_max_phone_candidates = 3
    default_country_code = "967"


class _Recording(FakeCommerce):
    def __init__(self, data):
        super().__init__(data)
        self.calls: list[dict] = []

    def lookup_order(self, **kw):
        self.calls.append(kw)
        return super().lookup_order(**kw)


_ORDERS = {
    "orders": {
        "t1": {
            "12345": {"ref": "12345", "status": "shipped", "updated_at": "2026-01-01", "phone": "+9675555555"},
        },
    },
}


def _ctx(recording, **kw):
    base = dict(
        tenant_id=uuid.uuid4(), conversation_id=uuid.uuid4(), tenant_ref="t1",
        channel_phone_e164="+9675555555", message_texts=("طلب 12345",),
        commerce=recording, settings=_Settings(),
    )
    base.update(kw)
    return ToolContext(**base)


def test_same_number_channel_matches_order_phone():
    rec = _Recording(_ORDERS)
    result = track_order.run(_ctx(rec, channel_phone_e164="+9675555555", message_texts=("طلب 12345",)))
    assert result.kind == "card"
    assert rec.calls[0]["path"] == "same_number"


def test_channel_not_normalizable_order_ref_alone_needs_phone():
    rec = _Recording(_ORDERS)
    result = track_order.run(_ctx(rec, channel_phone_e164=None, message_texts=("طلب 12345",)))
    assert result.kind == "need_phone"
    assert rec.calls == []  # no platform call


def test_other_number_with_correct_phone():
    rec = _Recording(_ORDERS)
    result = track_order.run(_ctx(
        rec, channel_phone_e164=None, message_texts=("طلب 12345، هاتفي 9675555555",),
    ))
    assert result.kind == "card"
    assert rec.calls[0]["path"] == "other_number"


def test_other_number_with_wrong_phone_unverified():
    rec = _Recording(_ORDERS)
    result = track_order.run(_ctx(
        rec, channel_phone_e164=None, message_texts=("طلب 12345، هاتفي 9671111111",),
    ))
    assert result.kind == "unverified"


def test_order_not_found_unverified():
    rec = _Recording(_ORDERS)
    result = track_order.run(_ctx(
        rec, channel_phone_e164=None, message_texts=("طلب 99999، هاتفي 9675555555",),
    ))
    assert result.kind == "unverified"


def test_oracle_result_identical():
    rec = _Recording(_ORDERS)
    r_wrong_phone = track_order.run(_ctx(
        rec, channel_phone_e164=None, message_texts=("طلب 12345، هاتفي 9671111111",),
    ))
    r_not_found = track_order.run(_ctx(
        rec, channel_phone_e164=None, message_texts=("طلب 99999، هاتفي 9675555555",),
    ))
    assert r_wrong_phone == r_not_found  # identical OrderLookup, no distinguishing field


def test_channel_none_degrades_to_other_number():
    rec = _Recording(_ORDERS)
    track_order.run(_ctx(
        rec, channel_phone_e164=None, message_texts=("طلب 12345، هاتفي 9675555555",),
    ))
    assert rec.calls[0]["path"] == "other_number"


def test_another_tenants_order_unverified():
    rec = _Recording(_ORDERS)
    # tenant_ref "t2" has no orders -> the order is effectively "another tenant's"
    result = track_order.run(_ctx(
        rec, tenant_ref="t2", channel_phone_e164=None, message_texts=("طلب 12345، هاتفي 9675555555",),
    ))
    assert result.kind == "unverified"


def test_h50_tool_never_touches_db():
    import ast
    src = Path(track_order.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden = ("app.db", "psycopg", "httpx", "redis", "app.llm", "app.channels", "app.ws_publish")
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    for imp in imports:
        assert not any(imp == f or imp.startswith(f + ".") for f in forbidden), imp
