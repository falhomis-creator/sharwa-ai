"""core/app/ws_publish.py - best-effort inbox Pub/Sub publisher (W5, H33).

inbox_events (Postgres) is the source of truth; redis-cache Pub/Sub is only the
fast wake-up signal. A publish failure is logged + counted and NEVER raised: the
event is already committed, and a missed broadcast is recovered by the client's
last_seq resync (§4.5). Publish runs AFTER COMMIT, never inside the transaction
(H33): writers call queue_publish() during the transaction (in-memory only, no
redis I/O) and the transaction owner calls flush_publishes() once COMMIT landed.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from typing import Any

import redis

from app.obs import metrics

logger = logging.getLogger(__name__)

# One pending-publish list per thread: the api sync routes and each worker thread
# own exactly one transaction at a time, so thread-local is the right scope here.
_LOCAL = threading.local()

# The worker process configures a single redis-cache client at startup; the api
# sync routes pass their own per-request client explicitly to flush_publishes().
_configured_client: redis.Redis | None = None


def configure(client: redis.Redis | None) -> None:
    """Set the worker process's redis-cache client (called once at startup). The
    api never calls this - it passes a client to flush_publishes() directly."""
    global _configured_client
    _configured_client = client


def inbox_channel(tenant_id: uuid.UUID | str) -> str:
    """The redis-cache Pub/Sub channel for one tenant's live inbox feed."""
    return f"tenant:{tenant_id}:inbox"


def _pending() -> list[dict[str, Any]]:
    items = getattr(_LOCAL, "pending", None)
    if items is None:
        items = []
        _LOCAL.pending = items
    return items


def queue_publish(
    *, tenant_id: uuid.UUID | str, seq: int, event_type: str, payload: dict[str, Any],
) -> None:
    """Record one post-commit publish for the current thread's transaction. No
    redis I/O here - the actual PUBLISH happens in flush_publishes() after COMMIT
    (H33: the broadcast must never run inside the transaction)."""
    _pending().append({
        "tenant_id": str(tenant_id), "seq": seq, "event_type": event_type, "payload": payload,
    })


def publish_inbox_event(
    client: redis.Redis,
    *,
    tenant_id: str,
    seq: int,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Publish one committed event to redis-cache Pub/Sub. Best-effort (H33): a
    RedisError is logged + counted and swallowed - the event already exists in
    Postgres and the client recovers any missed broadcast via last_seq."""
    message = json.dumps({"type": event_type, "seq": seq, **payload})
    try:
        client.publish(inbox_channel(tenant_id), message)
    except redis.RedisError:
        logger.warning(
            "inbox pub/sub publish failed for tenant %s seq %s", tenant_id, seq,
        )
        metrics.ws_publish_failures_total.inc()


def reset_pending() -> None:
    """Discard any queued publishes (called when a transaction will roll back)."""
    _LOCAL.pending = []


def flush_publishes(client: redis.Redis | None = None) -> None:
    """Drain and publish every queued event for this thread, then clear the
    buffer. Called after the owning transaction has COMMITted. `client` may be
    None when the worker's configured client should be used."""
    if client is None:
        client = _configured_client
    items = getattr(_LOCAL, "pending", None)
    if not items:
        return
    _LOCAL.pending = []
    if client is None:
        return
    for item in items:
        publish_inbox_event(
            client, tenant_id=item["tenant_id"], seq=item["seq"],
            event_type=item["event_type"], payload=item["payload"],
        )
