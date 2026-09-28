"""Pure order coordinator tests (P1.7 §10, H40/H53/H54) with recorded doubles."""
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


def _install(monkeypatch, log, *, blocked=False, wa_id="9677777777"):
    monkeypatch.setattr(orders.core_db, "tenant_tx", lambda tid: _fake_tx(log, tid))
    monkeypatch.setattr(orders.repos_outbox, "customer_id_for_conversation", lambda conn, cid: uuid.uuid4())
    monkeypatch.setattr(orders.repos_outbox, "wa_id_for_conversation", lambda conn, cid: wa_id)
    monkeypatch.setattr(orders.repos, "platform_ref_for_tenant", lambda conn, tid: "t1")
    monkeypatch.setattr(orders.repos_outbox, "order_lookup_blocked",
                        lambda conn, customer_id, order_ref_hash: blocked)
    monkeypatch.setattr(orders.repos_outbox, "insert_order_lookup_attempt",
                        lambda conn, **kw: log.add("insert_order_lookup_attempt", kw))


def _run(monkeypatch, log, *, blocked=False, wa_id="9677777777", tool_result=None,
         bodies=("طلب 12345",)):
    _install(monkeypatch, log, blocked=blocked, wa_id=wa_id)
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
