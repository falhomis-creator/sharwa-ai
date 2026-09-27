"""core/tests/test_ws.py - P1.3b WebSocket pure tests (doubles, no DB/redis/containers).

Covers the acceptance criteria that do not need a live connection (PROMPT §5):
Z3 (ticket single-use), Z4 (?token= rejected), Z5 (client subscribe attempt => 1008),
Z6 (resync ordering + gap delivered exactly once), Z7 (frame whitelist), Z8 (limits),
Z9 (heartbeat pong timeout), Z10 (publish failure swallowed + after-commit flush).
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import redis

from app import ws_publish
from app.api import ws
from app.api.ws_frames import to_frame
from app.obs import metrics


class FakeTicketStore:
    """One object satisfying both the sync issue_ticket (setex) and async
    consume_ticket (getdel) surfaces - mirrors the single redis-cache keyspace."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = value

    async def getdel(self, key: str) -> str | None:
        return self.store.pop(key, None)


class FakePubSub:
    def __init__(self) -> None:
        self.channels: list[str] = []

    async def subscribe(self, channel: str) -> None:
        self.channels.append(channel)

    async def unsubscribe(self, channel: str) -> None:
        if channel in self.channels:
            self.channels.remove(channel)

    async def aclose(self) -> None:
        pass

    def listen(self):
        async def gen():
            yield {"type": "subscribe", "channel": b"", "data": 1}

        return gen()


class FakeAsyncRedis:
    def __init__(self) -> None:
        self.pubsub_obj = FakePubSub()

    def pubsub(self) -> FakePubSub:
        return self.pubsub_obj


class FakeWebSocket:
    def __init__(self, messages=None, query_params=None) -> None:
        self.messages = list(messages or [])
        self.query_params = query_params or {}
        self.app = SimpleNamespace(
            state=SimpleNamespace(ws_hub=None, redis_async=None, settings=None),
        )
        self.closed: int | None = None
        self.sent: list[dict] = []
        self.accepted = False

    async def close(self, code: int) -> None:
        self.closed = code

    async def accept(self) -> None:
        self.accepted = True

    async def receive(self) -> dict:
        if self.messages:
            return self.messages.pop(0)
        return {"type": "websocket.disconnect"}

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


class RecordingSend:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict | None]] = []

    async def send(self, frame_type: str, payload: dict | None = None) -> None:
        self.sent.append((frame_type, payload))


# --- Z3: ticket single-use ---------------------------------------------------


def test_issue_ticket_mints_and_consume_is_single_use():
    store = FakeTicketStore()
    ticket, ttl = ws.issue_ticket(
        store, tenant_id="t1", staff_id="s1", role="owner",
        permissions=frozenset({"conversation.read"}),
    )
    assert ticket
    assert ttl == ws.WS_TICKET_TTL_S

    first = asyncio.run(ws.consume_ticket(store, ticket))
    assert first == {
        "tenant_id": "t1", "staff_id": "s1", "role": "owner",
        "permissions": ["conversation.read"],
    }

    second = asyncio.run(ws.consume_ticket(store, ticket))
    assert second is None  # single-use: GETDEL returns None on reuse


def test_consume_ticket_returns_none_when_missing():
    store = FakeTicketStore()
    assert asyncio.run(ws.consume_ticket(store, "never-issued")) is None


# --- Z4: ?token= rejected (H31) ----------------------------------------------


def test_ws_handler_rejects_token_in_url():
    sock = FakeWebSocket(query_params={"token": "jwt", "ticket": "t"})
    asyncio.run(ws.websocket_endpoint(sock, ticket="t", last_seq=0))
    assert sock.closed == 1008


# --- Z5: client subscribe attempt => 1008 (H32) -------------------------------


def test_receive_loop_rejects_subscribe_attempt():
    sock = FakeWebSocket(
        messages=[{"type": "websocket.receive", "text": '{"type":"subscribe","tenant_id":"x"}'}],
    )
    reason = ["normal"]
    asyncio.run(ws._receive_loop(sock, asyncio.Event(), reason))
    assert sock.closed == 1008
    assert reason[0] == "policy"


def test_receive_loop_rejects_oversized_frame():
    sock = FakeWebSocket(
        messages=[{"type": "websocket.receive", "text": "x" * (ws.WS_MAX_INBOUND_FRAME_BYTES + 1)}],
    )
    reason = ["normal"]
    asyncio.run(ws._receive_loop(sock, asyncio.Event(), reason))
    assert sock.closed == 1009
    assert reason[0] == "frame_too_large"


def test_receive_loop_enforces_rate_limit():
    messages = [
        {"type": "websocket.receive", "text": '{"type":"pong"}'}
        for _ in range(ws.WS_INBOUND_RATE_PER_S + 1)
    ]
    sock = FakeWebSocket(messages=messages)
    reason = ["normal"]
    asyncio.run(ws._receive_loop(sock, asyncio.Event(), reason))
    assert sock.closed == 1008
    assert reason[0] == "rate_limit"


def test_receive_loop_accepts_pong():
    sock = FakeWebSocket(messages=[{"type": "websocket.receive", "text": '{"type":"pong"}'}])
    last_pong = asyncio.Event()
    reason = ["normal"]
    asyncio.run(ws._receive_loop(sock, last_pong, reason))
    assert sock.closed is None  # pong is valid; loop exited on the following disconnect
    assert last_pong.is_set()


# --- Z6: resync ordering -----------------------------------------------------


def _ev(seq: int, event_type: str = "message.new") -> dict:
    return {"seq": seq, "type": event_type, "payload": {"conversation_id": "c1"}}


def _buf_item(seq: int, event_type: str = "message.new") -> dict:
    return {"seq": seq, "type": event_type, "payload": {"conversation_id": "c1"}}


def test_resync_sends_ascending_then_drains_dedup_by_seq():
    rec = RecordingSend()
    events = [_ev(3), _ev(4)]
    buffer: asyncio.Queue = asyncio.Queue(maxsize=100)
    for item in [_buf_item(2), _buf_item(4), _buf_item(5)]:
        buffer.put_nowait(item)

    sent_seq = asyncio.run(ws.deliver_resync_events(
        send=rec.send, events=events, buffer=buffer, last_seq=1,
    ))
    assert sent_seq == 5
    assert [p["seq"] for _t, p in rec.sent] == [3, 4, 5]


def test_resync_gap_event_delivered_exactly_once():
    rec = RecordingSend()
    # seq 5 is both in the read result AND in the replay buffer (it landed
    # between subscribe and read) - must be delivered exactly once.
    events = [_ev(5)]
    buffer: asyncio.Queue = asyncio.Queue(maxsize=100)
    buffer.put_nowait(_buf_item(5))

    asyncio.run(ws.deliver_resync_events(
        send=rec.send, events=events, buffer=buffer, last_seq=4,
    ))
    assert [p["seq"] for _t, p in rec.sent] == [5]


# --- Z7: frame whitelist (H34) ------------------------------------------------


def test_to_frame_rejects_non_whitelisted_key():
    with pytest.raises(ValueError):
        to_frame("message.new", {"body": "must never leak"})


def test_to_frame_rejects_phone_key():
    with pytest.raises(ValueError):
        to_frame("message.new", {"phone_e164": "+201234567890"})


def test_to_frame_allows_whitelisted_keys():
    frame = to_frame("message.new", {"conversation_id": "c1", "seq": 5})
    assert frame == {"type": "message.new", "conversation_id": "c1", "seq": 5}


# --- Z8: connection limits ---------------------------------------------------


def test_hub_rejects_at_global_ceiling():
    hub = ws.WsHub(FakeAsyncRedis())
    hub._total = ws.WS_GLOBAL_MAX
    buffer: asyncio.Queue = asyncio.Queue(maxsize=1)
    reason = asyncio.run(hub.attach(
        FakeWebSocket(), tenant_id="t1", staff_key="s1", buffer=buffer,
    ))
    assert reason == "global"


def test_hub_rejects_at_per_staff_ceiling():
    hub = ws.WsHub(FakeAsyncRedis())
    hub._staff_counts["s1"] = ws.WS_PER_STAFF_MAX
    buffer: asyncio.Queue = asyncio.Queue(maxsize=1)
    reason = asyncio.run(hub.attach(
        FakeWebSocket(), tenant_id="t1", staff_key="s1", buffer=buffer,
    ))
    assert reason == "per_staff"


def test_hub_rejects_at_per_tenant_ceiling():
    hub = ws.WsHub(FakeAsyncRedis())
    hub._tenant_counts["t1"] = ws.WS_PER_TENANT_MAX
    buffer: asyncio.Queue = asyncio.Queue(maxsize=1)
    reason = asyncio.run(hub.attach(
        FakeWebSocket(), tenant_id="t1", staff_key="s1", buffer=buffer,
    ))
    assert reason == "per_tenant"


def test_hub_attach_detach_restores_counters():
    hub = ws.WsHub(FakeAsyncRedis())
    sock = FakeWebSocket()
    buffer: asyncio.Queue = asyncio.Queue(maxsize=1)
    reason = asyncio.run(hub.attach(sock, tenant_id="t1", staff_key="s1", buffer=buffer))
    assert reason is None
    assert hub._total == 1
    asyncio.run(hub.detach(sock, reason="normal"))
    assert hub._total == 0
    assert hub._staff_counts.get("s1", 0) == 0
    assert hub._tenant_counts.get("t1", 0) == 0


# --- Z9: heartbeat pong timeout ----------------------------------------------


def test_heartbeat_closes_on_pong_timeout(monkeypatch):
    monkeypatch.setattr(ws, "WS_HEARTBEAT_INTERVAL_S", 0.01)
    monkeypatch.setattr(ws, "WS_PONG_TIMEOUT_S", 0.01)
    sock = FakeWebSocket()
    asyncio.run(ws._heartbeat(sock, asyncio.Event()))
    assert sock.closed == 1000
    assert any(f.get("type") == "ping" for f in sock.sent)


# --- Z10: publish failure is swallowed, not raised ----------------------------


def test_publish_failure_is_counted_and_swallowed(monkeypatch):
    calls: list[int] = []

    class FakeCounter:
        def inc(self) -> None:
            calls.append(1)

    monkeypatch.setattr(metrics, "ws_publish_failures_total", FakeCounter())

    class ThrowingPublisher:
        def publish(self, channel: str, message: str) -> None:
            raise redis.RedisError("boom")

    ws_publish.publish_inbox_event(
        ThrowingPublisher(), tenant_id="t1", seq=1, event_type="message.new", payload={},
    )
    assert calls == [1]  # counted, and no exception propagated


def test_queue_then_flush_publishes_after_commit():
    class FakeClient:
        def __init__(self) -> None:
            self.published: list[tuple[str, str]] = []

        def publish(self, channel: str, message: str) -> None:
            self.published.append((channel, message))

    fake = FakeClient()
    ws_publish.reset_pending()
    ws_publish.queue_publish(
        tenant_id="t1", seq=7, event_type="message.new",
        payload={"conversation_id": "c1"},
    )
    ws_publish.flush_publishes(fake)
    assert fake.published == [
        ("tenant:t1:inbox", json.dumps({"type": "message.new", "seq": 7, "conversation_id": "c1"})),
    ]

