"""Pure order coordinator tests (P1.7 §10, H40/H53/H54) with recorded doubles.

After N1/N2/N3 (P1.7 audit): the coordinator extracts ONCE and passes frozen
order_ref/phone_candidates/path to the tool; the per-conversation-per-day ceiling
is enforced; the order card is a closed projection.
"""
from __future__ import annotations

import contextlib
import re
import uuid

import pytest

from app.channels.commerce_client import CommerceUnavailableError
from app.tools import track_order
from app.workers import orders


class Settings:
    order_ref_pattern = re.compile(r"\b\d{4,8}\b")
    default_country_code = "967"
    order_lookup_max_phone_candidates = 3
    order_lookup_max_per_conversation_per_day = 10
    national_number_len = 9
    mobile_prefixes = ("7",)
    order_ref_hash_key = "test-key"


class Log:
    def __init__(self):
        self.events: list[tuple] = []

    def add(self, *parts):
        self.events.append(parts)


def _named(log, name):
    return [e for e in log.events if e[0] == name]


@contextlib.contextmanager
def _fake_tx(log, tenant_id):
    log.add("tx_open", tenant_id)
    try:
        yield object()
    finally:
        log.add("tx_close", tenant_id)


def _install(monkeypatch, log, *, blocked=False, attempts_today=0, wa_id="9677777777"):
    monkeypatch.setattr(orders.core_db, "tenant_tx", lambda tid: _fake_tx(log, tid))
    monkeypatch.setattr(orders.repos_outbox, "customer_id_for_conversation", lambda conn, cid: uuid.uuid4())
    monkeypatch.setattr(orders.repos_outbox, "wa_id_for_conversation", lambda conn, cid: wa_id)
    monkeypatch.setattr(orders.repos, "platform_ref_for_tenant", lambda conn, tid: "t1")
    monkeypatch.setattr(orders.repos_outbox, "order_lookup_blocked",
                        lambda conn, customer_id, order_ref_hash: blocked)
    monkeypatch.setattr(orders.repos_outbox, "count_order_lookup_attempts_for_conversation",
                        lambda conn, conversation_id: attempts_today)
    monkeypatch.setattr(orders.repos_outbox, "insert_order_lookup_attempt",
                        lambda conn, **kw: log.add("insert_order_lookup_attempt", kw))


def _run(monkeypatch, log, *, blocked=False, attempts_today=0, wa_id="9677777777", tool_result=None,
         bodies=("طلب 12345",)):
    _install(monkeypatch, log, blocked=blocked, attempts_today=attempts_today, wa_id=wa_id)
    if tool_result is None:
        monkeypatch.setattr(orders.track_order, "run",
                            lambda ctx: (log.add("platform_call") or track_order.OrderLookup(kind="card")))
    else:
        monkeypatch.setattr(orders.track_order, "run", lambda ctx: (log.add("platform_call") or tool_result))
    return orders.run_order_lookup(
        Settings(), tenant_id=uuid.uuid4(), conversation_id=uuid.uuid4(),
        commerce=object(), bodies=bodies,
    )


def test_blocked_logs_and_skips_platform(monkeypatch):
    log = Log()
    result = _run(monkeypatch, log, blocked=True)
    assert result.kind == "blocked"
    attempts = _named(log, "insert_order_lookup_attempt")
    assert len(attempts) == 1
    assert attempts[0][1]["outcome"] == "blocked"
    assert not _named(log, "platform_call")


def test_card_logs_allowed_once(monkeypatch):
    log = Log()
    result = _run(monkeypatch, log, tool_result=track_order.OrderLookup(kind="card"))
    assert result.kind == "card"
    attempts = _named(log, "insert_order_lookup_attempt")
    assert len(attempts) == 1
    assert attempts[0][1]["outcome"] == "allowed"


def test_unverified_logs_denied_once(monkeypatch):
    log = Log()
    result = _run(monkeypatch, log, tool_result=track_order.OrderLookup(kind="unverified"))
    assert result.kind == "unverified"
    attempts = _named(log, "insert_order_lookup_attempt")
    assert len(attempts) == 1
    assert attempts[0][1]["outcome"] == "denied"


@pytest.mark.parametrize("kind", ["need_order_ref", "need_phone", "unavailable"])
def test_non_attempt_kinds_log_nothing(monkeypatch, kind):
    log = Log()
    result = _run(monkeypatch, log, tool_result=track_order.OrderLookup(kind=kind))
    assert result.kind == kind
    assert not _named(log, "insert_order_lookup_attempt")


def test_platform_call_outside_transaction(monkeypatch):
    log = Log()
    _run(monkeypatch, log, tool_result=track_order.OrderLookup(kind="card"))
    names = [e[0] for e in log.events]
    i = names.index("platform_call")
    assert names[i - 1] == "tx_close"  # after phase-1 close
    assert names[i + 1] == "tx_open"   # before phase-3 open (H40)


def test_order_ref_hash_is_hmac_and_deterministic():
    h1 = orders.order_ref_hash("12345", "key1")
    assert h1 != "12345"
    assert h1 == orders.order_ref_hash("12345", "key1")
    assert h1 != orders.order_ref_hash("12345", "key2")


def test_no_raw_order_or_phone_logged(monkeypatch):
    log = Log()
    _run(monkeypatch, log, tool_result=track_order.OrderLookup(kind="card"))
    attempt = _named(log, "insert_order_lookup_attempt")[0][1]
    assert "12345" not in attempt["order_ref_hash"]
    for e in log.events:
        assert "12345" not in repr(e)


def test_platform_unavailable_no_exception_leaks(monkeypatch):
    log = Log()
    _install(monkeypatch, log)

    def boom(ctx):
        log.add("platform_call")
        raise CommerceUnavailableError("timeout")

    monkeypatch.setattr(orders.track_order, "run", boom)
    result = orders.run_order_lookup(
        Settings(), tenant_id=uuid.uuid4(), conversation_id=uuid.uuid4(),
        commerce=object(), bodies=("طلب 12345",),
    )
    assert result.kind == "unavailable"
    assert not _named(log, "insert_order_lookup_attempt")


def test_unknown_status_returns_none():
    from app.workers import compose
    assert compose.compose_order_status(
        {"ref": "12345", "status": "weird", "updated_at": "2026-01-01"},
        labels=compose.ORDER_STATUS_LABELS,
    ) is None


def test_no_cost_margin_vocabulary_in_output():
    from app.workers import compose
    text = compose.compose_order_status(
        {"ref": "12345", "status": "shipped", "updated_at": "2026-01-01"},
        labels=compose.ORDER_STATUS_LABELS,
    )
    assert text is not None
    for token in ("تكلفة", "هامش", "جملة", "cost", "margin"):
        assert token not in text


# --- N1 (P1.7 audit): the per-conversation-per-day ceiling ----------------------


def test_conversation_daily_ceiling_blocks(monkeypatch):
    log = Log()
    result = _run(monkeypatch, log, attempts_today=10)  # == ceiling (default 10)
    assert result.kind == "blocked"
    assert not _named(log, "platform_call")
    attempts = _named(log, "insert_order_lookup_attempt")
    assert len(attempts) == 1
    assert attempts[0][1]["outcome"] == "blocked"


def test_conversation_daily_ceiling_under_limit_passes(monkeypatch):
    log = Log()
    result = _run(monkeypatch, log, attempts_today=9)
    assert result.kind == "card"
    assert _named(log, "platform_call")


# --- N2 (P1.7 audit): extraction happens ONCE and is frozen into the tool --------


def test_single_extraction_passed_to_tool(monkeypatch):
    log = Log()
    _install(monkeypatch, log, wa_id="9677777777")
    captured: dict = {}

    def fake_run(ctx):
        captured["ctx"] = ctx
        return track_order.OrderLookup(kind="card")

    monkeypatch.setattr(orders.track_order, "run", fake_run)
    orders.run_order_lookup(
        Settings(), tenant_id=uuid.uuid4(), conversation_id=uuid.uuid4(),
        commerce=object(), bodies=("طلب 12345، هاتفي 771234567",),
    )
    ctx = captured["ctx"]
    assert ctx.order_ref == "12345"
    # F-P1-10: the local Yemeni format (9 digits, leading 7) normalizes to E.164.
    assert ctx.phone_candidates == ("+967771234567",)
    assert ctx.path == "other_number"


def test_order_ref_not_also_read_as_phone(monkeypatch):
    # N5: an 8-digit order-ref starting with 967 must NOT become a phone candidate.
    log = Log()
    _install(monkeypatch, log, wa_id="9677777777")
    captured: dict = {}

    def fake_run(ctx):
        captured["ctx"] = ctx
        return track_order.OrderLookup(kind="card")

    monkeypatch.setattr(orders.track_order, "run", fake_run)
    orders.run_order_lookup(
        Settings(), tenant_id=uuid.uuid4(), conversation_id=uuid.uuid4(),
        commerce=object(), bodies=("طلب 96712345",),
    )
    ctx = captured["ctx"]
    assert ctx.order_ref == "96712345"
    assert ctx.phone_candidates == ()  # the order-ref was excluded, not a phone


# --- N3 (P1.7 audit): the order card is a closed projection ----------------------


def test_card_reaching_core_has_no_phone(monkeypatch):
    from tests.fake_commerce import FakeCommerce

    log = Log()
    _install(monkeypatch, log, wa_id="9675555555")
    commerce = FakeCommerce({
        "orders": {
            "t1": {
                "12345": {"ref": "12345", "status": "shipped", "updated_at": "2026-01-01", "phone": "+9675555555"},
            },
        },
    })
    # do NOT mock track_order.run - run it for real through the coordinator.
    result = orders.run_order_lookup(
        Settings(), tenant_id=uuid.uuid4(), conversation_id=uuid.uuid4(),
        commerce=commerce, bodies=("طلب 12345",),
    )
    assert result.kind == "card"
    assert result.card is not None
    assert "phone" not in result.card
    assert set(result.card) == {"ref", "status", "updated_at"}
