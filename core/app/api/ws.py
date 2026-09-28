"""core/app/api/ws.py - P1.3b live employee stream (WebSocket + ticket + resync).

   POST /v1/ws/ticket   - issue a single-use, short-lived connection ticket (H31)
   WS   /v1/ws?ticket=  - the live stream: ticket auth, server-chosen subscription
                          (H32), heartbeat, limits, resync by last_seq (§4.5).

The two correctness guarantees live in _run_resync(): subscribe-before-read (no
gap) and drain-by-seq (no duplicate). Reverse the order and you get a silent gap
or a duplicate that no short test catches (PROMPT §4.5). Every database call in
the WebSocket path runs through run_in_threadpool (§4.2) - psycopg is sync, and a
direct call inside the event loop would freeze every connection, not just one.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import redis
from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from redis import asyncio as redis_asyncio
from starlette.concurrency import run_in_threadpool

from app import db as core_db
from app.api.errors import ApiError
from app.api.rate_limit import rate_limited
from app.api.ws_frames import to_frame
from app.db import repos_inbox
from app.obs import metrics
from app.security.permissions import StaffContext
from app.ws_publish import inbox_channel

router = APIRouter()

# --- written ceilings (H4): every limit has an explicit, documented default ----

WS_TICKET_TTL_S = 30
WS_HEARTBEAT_INTERVAL_S = 25.0
WS_PONG_TIMEOUT_S = 20.0
WS_MAX_INBOUND_FRAME_BYTES = 4096
WS_INBOUND_RATE_PER_S = 20
WS_REPLAY_BUFFER_MAX = 1000
WS_RESYNC_MAX = 500
WS_PER_STAFF_MAX = 5
WS_PER_TENANT_MAX = 200
# The global ceiling is an operator-set number (review ulimit -n); 1000 is the
# written default, recorded in P1_DEVIATIONS.md as a per-process guard, not a
# cross-process guarantee (running N api copies multiplies the effective cap).
WS_GLOBAL_MAX = 1000

_TICKET_PREFIX = "ws:ticket:"


def _ticket_key(ticket: str) -> str:
    return f"{_TICKET_PREFIX}{ticket}"


# --- ticket (W1) --------------------------------------------------------------


def issue_ticket(
    client: redis.Redis,
    *,
    tenant_id: str,
    staff_id: str | None,
    role: str | None,
    permissions: frozenset[str],
) -> tuple[str, int]:
    """Mint a 32-byte, single-use ticket (SETEX, TTL=WS_TICKET_TTL_S). The payload
    carries identity + permissions - never a JWT or any secret (H31): a lost
    ticket means the client re-requests one, nothing more."""
    ticket = secrets.token_urlsafe(32)
    payload = {
        "tenant_id": tenant_id,
        "staff_id": staff_id,
        "role": role,
        "permissions": sorted(permissions),
    }
    client.setex(_ticket_key(ticket), WS_TICKET_TTL_S, json.dumps(payload))
    return ticket, WS_TICKET_TTL_S


async def consume_ticket(client: redis_asyncio.Redis, ticket: str) -> dict[str, Any] | None:
    """Atomically consume a ticket via GETDEL (GET + DEL in one command): a ticket
    used twice => the second GETDEL returns None. This is the single-use guarantee
    (W1/Z3) - not a TTL check, an atomic consume."""
    raw = await client.getdel(_ticket_key(ticket))
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# --- the single frame send path (H34, S6) -------------------------------------


async def _send_frame(
    websocket: WebSocket, frame_type: str, payload: dict[str, Any] | None = None,
) -> None:
    """The ONLY place a WebSocket frame is built and sent. Every send_json payload
    passes through to_frame() (H34 whitelist) - static_gate S6 enforces that no
    other send_json in core/app builds a payload."""
    await websocket.send_json(to_frame(frame_type, payload))
    metrics.ws_frames_sent_total.labels(frame_type).inc()


# --- resync ordering (W6, the precise part) -----------------------------------


async def deliver_resync_events(
    *,
    send: Callable[[str, dict[str, Any] | None], Awaitable[None]],
    events: list[dict[str, Any]],
    buffer: asyncio.Queue,
    last_seq: int,
) -> int:
    """Send already-read events in ascending seq order, then drain the replay
    buffer dropping anything the read already covered (dedup by seq, not time).
    The subscribe happened before the read (the buffer is already attached to the
    tenant subscriber), so an event landing between read and subscribe appears in
    both `events` and `buffer` and is delivered exactly once (§4.5). Returns the
    highest seq delivered."""
    sent_seq = last_seq
    for ev in events:
        await send(ev["type"], {"seq": ev["seq"], **ev["payload"]})
        metrics.ws_resync_events_total.inc()
        sent_seq = ev["seq"]
    # Drain the replay buffer, dropping anything the read already covered
    # (dedup by seq, not by time - the seq is the monotonic truth).
    while True:
        try:
            item = buffer.get_nowait()
        except asyncio.QueueEmpty:
            break
        if item["seq"] <= sent_seq:
            continue
        await send(item["type"], {"seq": item["seq"], **item["payload"]})
        sent_seq = item["seq"]
    return sent_seq


# --- database readers (sync; called via run_in_threadpool only) ---------------


def _read_missed(tenant_id: str, last_seq: int) -> tuple[list[dict[str, Any]], int]:
    with core_db.tenant_tx(tenant_id) as conn:
        return repos_inbox.list_inbox_events(
            conn, tenant_id=uuid.UUID(tenant_id), since_seq=last_seq, limit=WS_RESYNC_MAX,
        )


def _read_oldest(tenant_id: str) -> int | None:
    with core_db.tenant_tx(tenant_id) as conn:
        return repos_inbox.fetch_oldest_inbox_seq(conn, tenant_id=uuid.UUID(tenant_id))


# --- connection tracking (W4 limits, W5 subscribers) --------------------------

class _Connection:
    __slots__ = ("websocket", "tenant_id", "staff_key", "buffer", "live", "resync_required")

    def __init__(
        self, websocket: WebSocket, tenant_id: str, staff_key: str, buffer: asyncio.Queue,
    ) -> None:
        self.websocket = websocket
        self.tenant_id = tenant_id
        self.staff_key = staff_key
        self.buffer = buffer
        self.live = False
        self.resync_required = False


class _TenantSubscriber:
    def __init__(self, channel: str) -> None:
        self.channel = channel
        self.connections: set[_Connection] = set()
        self.pubsub: Any = None
        self.task: asyncio.Task[None] | None = None


class WsHub:
    """In-process connection manager: per-staff/per-tenant/global ceilings, and
    one pub/sub subscriber per tenant (never per connection), torn down at the
    last disconnection. Counters are process-local resource guards, not truth
    (H18) - documented in P1_DEVIATIONS.md."""

    def __init__(self, redis_async: redis_asyncio.Redis) -> None:
        self._redis = redis_async
        self._connections: dict[WebSocket, _Connection] = {}
        self._staff_counts: dict[str, int] = {}
        self._tenant_counts: dict[str, int] = {}
        self._subscribers: dict[str, _TenantSubscriber] = {}
        self._total = 0
        self._lock = asyncio.Lock()

    async def attach(
        self, websocket: WebSocket, *, tenant_id: str, staff_key: str, buffer: asyncio.Queue,
    ) -> str | None:
        """Register a connection or return a rejection reason (W4). The subscriber
        is attached here - BEFORE any resync read - which closes the subscribe/read
        gap in §4.5."""
        async with self._lock:
            if self._total >= WS_GLOBAL_MAX:
                return "global"
            if self._staff_counts.get(staff_key, 0) >= WS_PER_STAFF_MAX:
                return "per_staff"
            if self._tenant_counts.get(tenant_id, 0) >= WS_PER_TENANT_MAX:
                return "per_tenant"
            conn = _Connection(websocket, tenant_id, staff_key, buffer)
            self._connections[websocket] = conn
            self._total += 1
            self._staff_counts[staff_key] = self._staff_counts.get(staff_key, 0) + 1
            self._tenant_counts[tenant_id] = self._tenant_counts.get(tenant_id, 0) + 1
            sub = self._subscribers.get(tenant_id)
            if sub is None:
                sub = _TenantSubscriber(inbox_channel(tenant_id))
                self._subscribers[tenant_id] = sub
                await self._start_subscriber(sub)
            sub.connections.add(conn)
            metrics.ws_connections.set(self._total)
            metrics.ws_connects_total.inc()
            return None

    async def detach(self, websocket: WebSocket, *, reason: str) -> None:
        async with self._lock:
            conn = self._connections.pop(websocket, None)
            if conn is None:
                return
            self._total -= 1
            self._staff_counts[conn.staff_key] -= 1
            self._tenant_counts[conn.tenant_id] -= 1
            sub = self._subscribers.get(conn.tenant_id)
            if sub is not None:
                sub.connections.discard(conn)
                if not sub.connections:
                    await self._stop_subscriber(conn.tenant_id, sub)
            metrics.ws_connections.set(self._total)
            metrics.ws_disconnects_total.labels(reason).inc()

    async def _start_subscriber(self, sub: _TenantSubscriber) -> None:
        sub.pubsub = self._redis.pubsub()
        await sub.pubsub.subscribe(sub.channel)
        sub.task = asyncio.create_task(self._listen(sub))

    async def _stop_subscriber(self, tenant_id: str, sub: _TenantSubscriber) -> None:
        if sub.task is not None:
            sub.task.cancel()
            sub.task = None
        if sub.pubsub is not None:
            await sub.pubsub.unsubscribe(sub.channel)
            await sub.pubsub.aclose()
            sub.pubsub = None
        self._subscribers.pop(tenant_id, None)

    async def _listen(self, sub: _TenantSubscriber) -> None:
        pubsub = sub.pubsub
        assert pubsub is not None
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                except (json.JSONDecodeError, KeyError):
                    continue
                self._dispatch(sub, data)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Best-effort pub/sub (H33): a transient listener error must not kill
            # the api process; the next resync recovers any missed broadcast.
            pass

    def _dispatch(self, sub: _TenantSubscriber, data: dict[str, Any]) -> None:
        event_type = data.get("type", "")
        seq = data.get("seq")
        payload = {k: v for k, v in data.items() if k not in ("type", "seq")}
        item = {"type": event_type, "seq": seq, "payload": payload}
        for conn in list(sub.connections):
            if conn.live:
                asyncio.create_task(
                    _send_frame(conn.websocket, item["type"], {"seq": item["seq"], **item["payload"]})
                )
            elif conn.buffer.qsize() >= WS_REPLAY_BUFFER_MAX:
                # Buffer overflow during resync: drop the broadcast and force a
                # full resync (§4.5).
                conn.resync_required = True
            else:
                conn.buffer.put_nowait(item)

    def connection(self, websocket: WebSocket) -> _Connection | None:
        return self._connections.get(websocket)


# --- endpoints ----------------------------------------------------------------

@router.post("/v1/ws/ticket")
def create_ws_ticket(
    request: Request,
    staff: StaffContext = Depends(rate_limited("ticket", "conversation.read")),
) -> dict[str, Any]:
    try:
        ticket, ttl = issue_ticket(
            request.app.state.redis_sync._client,
            tenant_id=str(staff.tenant_id),
            staff_id=str(staff.staff_id) if staff.staff_id is not None else None,
            role=staff.role,
            permissions=staff.permissions,
        )
    except redis.RedisError as exc:
        raise ApiError("TICKET_ISSUE_FAILED") from exc
    return {"ticket": ticket, "expires_in_s": ttl}


@router.websocket("/v1/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    ticket: str = Query(...),
    last_seq: int = Query(default=0),
) -> None:
    hub: WsHub = websocket.app.state.ws_hub
    redis_async: redis_asyncio.Redis = websocket.app.state.redis_async
    settings = websocket.app.state.settings

    # CORS (P1.8): the WebSocket handshake is NOT protected by CORSMiddleware, so
    # the Origin header is checked against the same explicit allowlist. A browser
    # Origin outside the list is rejected (1008); a missing Origin (non-browser
    # client) is not subject to CORS and is allowed.
    origin = websocket.headers.get("origin")
    if origin and origin not in settings.console_allowed_origins:
        metrics.ws_rejected_total.labels("origin_not_allowed").inc()
        await websocket.close(code=1008)
        return

    # H31: a JWT in the URL is forbidden - auth is ticket-only. Reject before
    # accepting any frame.
    if websocket.query_params.get("token") is not None:
        metrics.ws_rejected_total.labels("token_in_url").inc()
        await websocket.close(code=1008)
        return

    # Single-use ticket: GETDEL is atomic; an invalid/expired/reused ticket is
    # rejected before the handshake is accepted (W2).
    try:
        identity = await consume_ticket(redis_async, ticket)
    except redis.RedisError:
        identity = None
    if identity is None:
        metrics.ws_rejected_total.labels("ticket_invalid").inc()
        await websocket.close(code=1008)
        return

    tenant_id = identity["tenant_id"]
    staff_key = identity["staff_id"] or "platform_admin"

    buffer: asyncio.Queue = asyncio.Queue(maxsize=WS_REPLAY_BUFFER_MAX)
    reject = await hub.attach(websocket, tenant_id=tenant_id, staff_key=staff_key, buffer=buffer)
    if reject is not None:
        metrics.ws_rejected_total.labels(reject).inc()
        await websocket.close(code=1013)
        return

    reason = ["normal"]
    last_pong = asyncio.Event()
    last_pong.set()
    try:
        await websocket.accept()

        # Resync: subscribe already happened (attach); one db read returns both
        # the missed events and the latest seq, then hello goes first (§4.5).
        events, latest_seq = await run_in_threadpool(_read_missed, tenant_id, last_seq)
        await _send_frame(
            websocket, "hello",
            {"latest_seq": latest_seq, "retention_days": settings.inbox_events_retention_days},
        )

        oldest = await run_in_threadpool(_read_oldest, tenant_id)
        if oldest is not None and last_seq < oldest - 1:
            # Retention already deleted what the client is missing; never pretend
            # it was delivered (§4.5).
            await _send_frame(websocket, "resync_required")
        else:
            await deliver_resync_events(
                send=lambda ft, p=None: _send_frame(websocket, ft, p),
                events=events, buffer=buffer, last_seq=last_seq,
            )

        conn = hub.connection(websocket)
        if conn is not None:
            conn.live = True
            if conn.resync_required:
                await _send_frame(websocket, "resync_required")
                conn.resync_required = False

        heartbeat_task = asyncio.create_task(_heartbeat(websocket, last_pong))
        try:
            await _receive_loop(websocket, last_pong, reason)
        finally:
            heartbeat_task.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await hub.detach(websocket, reason=reason[0])
        except Exception:
            pass


async def _heartbeat(websocket: WebSocket, last_pong: asyncio.Event) -> None:
    """Server ping every 25s; a pong must arrive within 20s or the connection is
    closed and the counters cleaned up in the handler's finally (§4.2, disaster
    18: the leak here is what kills the server, not the connection itself)."""
    while True:
        await asyncio.sleep(WS_HEARTBEAT_INTERVAL_S)
        await _send_frame(websocket, "ping")
        try:
            await asyncio.wait_for(last_pong.wait(), timeout=WS_PONG_TIMEOUT_S)
        except asyncio.TimeoutError:
            metrics.ws_pong_timeouts_total.inc()
            await websocket.close(code=1000)
            return
        last_pong.clear()


async def _receive_loop(websocket: WebSocket, last_pong: asyncio.Event, reason: list[str]) -> None:
    """The client may only send pong (H32): any other incoming frame - including a
    subscribe request - is a policy violation and closes the connection with 1008.
    Enforces the 4KB frame ceiling (1009) and the 20/s rate limit (1008)."""
    window_start = time.monotonic()
    frames_in_window = 0
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        raw = message.get("text") or message.get("bytes") or b""
        if len(raw) > WS_MAX_INBOUND_FRAME_BYTES:
            reason[0] = "frame_too_large"
            await websocket.close(code=1009)
            return

        now = time.monotonic()
        if now - window_start >= 1.0:
            window_start = now
            frames_in_window = 0
        frames_in_window += 1
        if frames_in_window > WS_INBOUND_RATE_PER_S:
            reason[0] = "rate_limit"
            await websocket.close(code=1008)
            return

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            reason[0] = "policy"
            await websocket.close(code=1008)
            return
        if isinstance(data, dict) and data.get("type") == "pong":
            last_pong.set()
        else:
            # H32: the client never chooses a subscription; only pong is valid.
            reason[0] = "policy"
            metrics.ws_rejected_total.labels("subscribe_attempt").inc()
            await websocket.close(code=1008)
            return
