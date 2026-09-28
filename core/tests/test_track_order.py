"""Pure track_order tool tests (P1.7 §10, H50/H52/H53) with a recording FakeCommerce.

After N2 (P1.7 audit), the tool no longer extracts: order_ref / phone_candidates /
path are frozen into ToolContext by the coordinator (orders.py phase 1). These
tests cover resolve_path() and run() with explicit frozen inputs.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from app.tools import track_order
from app.tools.registry import ToolContext
from tests.fake_commerce import FakeCommerce


class _Settings:
    pass


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
        order_ref="12345", phone_candidates=(), path="same_number",
    )
    base.update(kw)
    return ToolContext(**base)


# --- resolve_path (pure) -------------------------------------------------------


def test_resolve_path_same_number_when_channel_matches_candidate():
    assert track_order.resolve_path("+9675555555", ("+9675555555",)) == "same_number"


def test_resolve_path_same_number_when_no_candidates():
    assert track_order.resolve_path("+9675555555", ()) == "same_number"


def test_resolve_path_other_number_when_candidate_differs():
    assert track_order.resolve_path("+9675555555", ("+9671111111",)) == "other_number"


def test_resolve_path_other_number_when_channel_unknown():
    assert track_order.resolve_path(None, ("+9675555555",)) == "other_number"


# --- run() with frozen inputs ---------------------------------------------------


def test_same_number_channel_matches_order_phone():
    rec = _Recording(_ORDERS)
    result = track_order.run(_ctx(rec, path="same_number", phone_candidates=()))
    assert result.kind == "card"
    assert rec.calls[0]["path"] == "same_number"


def test_other_number_without_phone_needs_phone():
    rec = _Recording(_ORDERS)
    result = track_order.run(_ctx(rec, path="other_number", phone_candidates=()))
    assert result.kind == "need_phone"
    assert rec.calls == []  # no platform call


def test_other_number_with_correct_phone():
    rec = _Recording(_ORDERS)
    result = track_order.run(_ctx(rec, path="other_number", phone_candidates=("+9675555555",)))
    assert result.kind == "card"
    assert rec.calls[0]["path"] == "other_number"


def test_other_number_with_wrong_phone_unverified():
    rec = _Recording(_ORDERS)
    result = track_order.run(_ctx(rec, path="other_number", phone_candidates=("+9671111111",)))
    assert result.kind == "unverified"


def test_order_not_found_unverified():
    rec = _Recording(_ORDERS)
    result = track_order.run(_ctx(rec, order_ref="99999", path="other_number", phone_candidates=("+9675555555",)))
    assert result.kind == "unverified"


def test_oracle_result_identical():
    rec = _Recording(_ORDERS)
    r_wrong_phone = track_order.run(_ctx(rec, path="other_number", phone_candidates=("+9671111111",)))
    r_not_found = track_order.run(_ctx(rec, order_ref="99999", path="other_number", phone_candidates=("+9675555555",)))
    assert r_wrong_phone == r_not_found  # identical OrderLookup, no distinguishing field


def test_channel_none_degrades_to_other_number():
    rec = _Recording(_ORDERS)
    track_order.run(_ctx(rec, channel_phone_e164=None, path="other_number", phone_candidates=("+9675555555",)))
    assert rec.calls[0]["path"] == "other_number"


def test_another_tenants_order_unverified():
    rec = _Recording(_ORDERS)
    # tenant_ref "t2" has no orders -> the order is effectively "another tenant's"
    result = track_order.run(_ctx(rec, tenant_ref="t2", path="other_number", phone_candidates=("+9675555555",)))
    assert result.kind == "unverified"


def test_lookup_order_card_has_no_phone():
    # N3 (P1.7 audit): the card reaching core is a closed projection - the
    # platform's `phone` (and any address/amount) never crosses the boundary.
    rec = _Recording(_ORDERS)
    result = track_order.run(_ctx(rec, path="same_number", phone_candidates=()))
    assert result.kind == "card"
    assert result.card is not None
    assert "phone" not in result.card
    assert set(result.card) == {"ref", "status", "updated_at"}


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
