"""core/app/workers/stream.py - the ONLY place in core/ that runs Redis stream
commands (P1.1.1, H17, hunt_gate rule 9).

XREADGROUP/XAUTOCLAIM/XACK/XADD-on-DLQ all live here; realtime.py never issues
a stream command itself. XAUTOCLAIM runs first (reclaim a dead consumer's
abandoned entries), then XREADGROUP BLOCK for new ones; per-entry attempts live
in Redis (`core:fwd:attempts:{stream}:{id}`, 7-day TTL), and past the cap the
entry is parked on CORE_INGEST_DLQ_STREAM + ACKed.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import time
from typing import Any

import redis
from redis.exceptions import RedisError

from app.obs import logging as obs_logging
from app.obs import metrics
from app.workers.config import WorkerSettings

_log = obs_logging.get_logger("stream")


class TransientError(RuntimeError):
    """Retryable Redis/PG/network failure - do NOT XACK; back off + the entry
    stays in the PEL (H3)."""


class PermanentError(RuntimeError):
    """Non-retryable failure (corrupt JSON / schema mismatch) - park on DLQ and
    XACK to avoid a poison-pill loop."""


def create_redis_client(settings: WorkerSettings) -> redis.Redis:
    """redis-durable client with an explicit socket timeout and no offline
    buffering (redis-py fails fast on ConnectionError - H3)."""
    timeout_s = settings.redis_durable_timeout_ms / 1000.0
    return redis.Redis(
        host=settings.redis_durable_host,
        port=settings.redis_durable_port,
        password=settings.redis_durable_password or None,
        socket_timeout=timeout_s,
        socket_connect_timeout=timeout_s,
        decode_responses=True,
    )


def wait_for_ready(client: redis.Redis, *, timeout_ms: int) -> None:
    """Block until the client answers PING (TCP+AUTH handshake complete) or the
    timeout elapses. Prevents the forwarder's crash-loop where XGROUP CREATE
    raced the handshake on every startup."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        try:
            client.ping()
            return
        except RedisError:
            time.sleep(0.05)
    raise TransientError("redis-durable did not become ready before timeout")


def _fields_to_dict(fields: Any) -> dict[str, str]:
    """Normalize a stream entry's field block (list [k,v,...] or dict) to a dict."""
    if isinstance(fields, dict):
        return fields
    items = list(fields)
    return dict(zip(items[0::2], items[1::2]))


class StreamReader:
    """Per-worker-group stream reader: consumer name + every stream op.

    `group`/`dlq_stream` default to the ingest group/dlq; the evt:{shard}
    consumer constructs a second reader with the ai-core-evt group and
    dlq:evt:core (rule 9 stays intact: all stream commands still live here)."""

    def __init__(
        self,
        client: redis.Redis,
        settings: WorkerSettings,
        *,
        group: str | None = None,
        dlq_stream: str | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._group = group or settings.core_ingest_group
        self._dlq_stream = dlq_stream or settings.core_ingest_dlq_stream
        self._consumer = f"core-ingest-{socket.gethostname()}-{os.getpid()}"

    @property
    def consumer(self) -> str:
        return self._consumer

    def ensure_group(self, stream: str) -> None:
        try:
            self._client.xgroup_create(stream, self._group, id="$", mkstream=True)
        except RedisError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def read_batch(self, stream: str) -> list[tuple[str, dict[str, str]]]:
        group = self._group
        batch = self._settings.core_ingest_batch
        block_ms = self._settings.core_ingest_block_ms
        claim_idle_ms = self._settings.core_ingest_claim_idle_ms

        entries: list[tuple[str, dict[str, str]]] = []

        claimed = self._client.xautoclaim(
            stream, group, self._consumer, min_idle_time=claim_idle_ms,
            start_id="0-0", count=batch,
        )
        for item in (claimed[1] if len(claimed) > 1 else []):
            parsed = _fields_to_dict(item[1])
            if parsed:
                entries.append((item[0], parsed))
            else:
                # D5: an entry with empty fields is a deleted entry XAUTOCLAIM
                # re-surfaced; it must be XACKed (spec, literal) or it stays in
                # the PEL and is re-claimed every claim_idle forever.
                self.ack(stream, item[0])
                metrics.ingest_core_skipped_total.labels("empty_fields").inc()
                obs_logging.log_event(
                    _log, event="wal.empty_fields", component="stream",
                    level=logging.WARNING, stream=stream, id=item[0],
                )

        read = self._client.xreadgroup(
            group, self._consumer, {stream: ">"}, count=batch, block=block_ms,
        )
        if read:
            for item in read[0][1]:
                parsed = _fields_to_dict(item[1])
                if parsed:
                    entries.append((item[0], parsed))
                else:
                    self.ack(stream, item[0])
                    metrics.ingest_core_skipped_total.labels("empty_fields").inc()
                    obs_logging.log_event(
                        _log, event="wal.empty_fields", component="stream",
                        level=logging.WARNING, stream=stream, id=item[0],
                    )

        return entries

    def ack(self, stream: str, entry_id: str) -> None:
        self._client.xack(stream, self._group, entry_id)

    def park_dlq(self, stream: str, entry_id: str, raw_data: str, reason: str) -> None:
        """XADD the entry onto the DLQ stream with the forwarder's
        stream/id/data field shape; the caller then ACKs (it lives on DLQ)."""
        self._client.xadd(
            self._dlq_stream,
            {"stream": stream, "id": entry_id, "data": raw_data, "reason": reason},
        )

    def record_attempt(self, stream: str, entry_id: str) -> int:
        """INCR the per-entry attempts counter (7-day TTL), returning the count."""
        key = f"core:fwd:attempts:{stream}:{entry_id}"
        attempts = int(self._client.incr(key))
        self._client.expire(key, 7 * 24 * 3600)
        return attempts

    def extend_dedupe_marker(self, session_id: str, provider_message_id: str) -> None:
        """P1.1.7: after COMMIT, extend the gateway's dedupe marker to the long
        "done" TTL - ONLY if it still exists (a fresh SET would resurrect a
        correctly-expired marker). Mirrors dedupe.js markDone."""
        key = f"dedupe:{session_id}:{provider_message_id}"
        try:
            if self._client.exists(key):
                self._client.psetex(key, self._settings.dedupe_done_ttl_s * 1000, "done")
        except RedisError as exc:
            # Best-effort only (the real guarantee is UNIQUE inbound_events), but
            # never silent (H3): log it so a sustained extension failure is visible.
            obs_logging.log_event(
                _log, event="dedupe.extend_failed", component="stream",
                level=logging.WARNING, session_id=session_id, error=str(exc),
            )

    def group_stats(self, stream: str) -> tuple[int, int]:
        """(lag, pel_count) for our group on `stream` from XINFO GROUPS."""
        for info in self._client.xinfo_groups(stream):
            if info.get("name") == self._group:
                return int(info.get("lag", 0) or 0), int(info.get("pending", 0) or 0)
        return 0, 0


def parse_data_field(fields: dict[str, str]) -> tuple[dict[str, Any], str]:
    """Return (obj, raw_json_string) from a stream entry's `data` field; raises
    PermanentError on malformed JSON."""
    raw = fields.get("data")
    if raw is None:
        raise PermanentError("stream entry has no 'data' field")
    try:
        return json.loads(raw), raw
    except (json.JSONDecodeError, TypeError) as exc:
        raise PermanentError(f"corrupt JSON in stream entry: {exc}") from exc
