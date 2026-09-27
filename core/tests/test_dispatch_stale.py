"""Pure F-P1-09 stale-drop tests (recorded doubles, no DB).

These five cases pin the corrected drop condition (PROMPT_P1_06 §3): a bot row is
stale iff its epoch no longer matches OR the conversation is CLOSED - paused_human
alone no longer drops the row. Case 1 is the test whose absence hid F-P1-09: every
handoff_notice/safe_ack was silently dropped before reaching the customer.
"""
from __future__ import annotations

import contextlib
import uuid

from app.db import repos_outbox
from app.workers import dispatch


def _row(**kw) -> repos_outbox.OutboxRow:
    defaults = dict(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), conversation_id=uuid.uuid4(),
        channel_account_id=uuid.uuid4(), idempotency_key="k", origin="bot",
        message_class="service", expected_epoch=5, to_wa_id="wa",
        payload={"template": "handoff_notice", "text": "أهلاً"}, attempts=0,
    )
    defaults.update(kw)
    return repos_outbox.OutboxRow(**defaults)


def _run(monkeypatch, row, epoch_status):
    mark: list[str] = []
    monkeypatch.setattr(repos_outbox, "read_conversation_epoch_status", lambda conn, cid: epoch_status)
    monkeypatch.setattr(repos_outbox, "mark_outbox_status", lambda conn, outbox_id, status: mark.append(status))
    monkeypatch.setattr(repos_outbox, "effective_switch", lambda conn, tenant_id, channel_account_id, capability: "on")
    monkeypatch.setattr(repos_outbox, "read_channel_session_id", lambda conn, caid: "session-123")
    monkeypatch.setattr(repos_outbox, "customer_id_for_conversation", lambda conn, cid: None)
    monkeypatch.setattr(repos_outbox, "has_suppression", lambda conn, tid, cid, mc: False)
    monkeypatch.setattr(dispatch, "tenant_tx", lambda tid: contextlib.nullcontext(object()))
    return dispatch._pre_send_checks(None, row), mark


def test_case1_paused_human_matching_epoch_not_dropped(monkeypatch):
    session, mark = _run(monkeypatch, _row(expected_epoch=5), (5, "paused_human"))
    assert session == "session-123"
    assert mark == []


def test_case2_paused_human_old_epoch_dropped(monkeypatch):
    session, mark = _run(monkeypatch, _row(expected_epoch=5), (7, "paused_human"))
    assert session is None
    assert mark == ["dropped_stale"]


def test_case3_closed_matching_epoch_dropped(monkeypatch):
    session, mark = _run(monkeypatch, _row(expected_epoch=5), (5, "closed"))
    assert session is None
    assert mark == ["dropped_stale"]


def test_case4_active_matching_epoch_not_dropped(monkeypatch):
    session, mark = _run(monkeypatch, _row(expected_epoch=5), (5, "active"))
    assert session == "session-123"
    assert mark == []


def test_case5_human_never_dropped(monkeypatch):
    row = _row(origin="human", expected_epoch=None)
    session, mark = _run(monkeypatch, row, None)
    assert session == "session-123"
    assert mark == []
