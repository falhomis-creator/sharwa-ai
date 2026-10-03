"""core/app/workers/realtime.py - entry point `python -m app.workers.realtime`.

P1.0.4/P1.1: one thread per shard (INGEST_SHARDS threads), each shard processed
serially within its thread so two entries from the same session are never
processed concurrently (shardFor pins a session to one shard). SIGTERM/SIGINT:
stop reading new entries, finish the in-flight one, XACK what committed, close
pools+client, exit 0 within CORE_INGEST_SHUTDOWN_TIMEOUT_S. An unhandled
exception in any shard thread fails the whole process fast (H3) so Docker
restarts it - no silently dead thread.
"""
from __future__ import annotations

import datetime
import logging
import random
import signal
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Any

import redis
from pydantic import ValidationError
from redis.exceptions import RedisError

from app import db as core_db
from app import ws_publish
from app.channels.commerce_client import CommerceClient
from app.channels.gateway_client import GatewayClient
from app.commerce.adapter import SharwaCommerceAdapter
from app.config import GatewayConfig
from app.db import repos
from app.db import repos_geo
from app.db import repos_ingest
from app.db import repos_inbox
from app.db import repos_llm
from app.db import repos_outbox
from app.db import repos_policy
from app.db.context import system_tx, tenant_tx
from app.obs import http as obs_http
from app.obs import logging as obs_logging
from app.obs import metrics
from app.workers import catalog, dispatch, embed, evt, optout, policy_sweep, proactive, schema, stock, summary, turn, verify
from app.workers.config import WorkerSettings
from app.workers.stream import (
    PermanentError,
    StreamReader,
    TransientError,
    create_redis_client,
    parse_data_field,
    wait_for_ready,
)

_log = obs_logging.get_logger("realtime")


def _jitter(base_ms: int) -> float:
    return base_ms / 1000.0 * (0.5 + random.random())


def _truncate(body: str, max_chars: int) -> tuple[str, bool]:
    if len(body) <= max_chars:
        return body, False
    return body[:max_chars], True


@dataclass(frozen=True)
class CommitResult:
    outcome: str  # 'committed' | 'duplicate'
    session_id: str
    provider_message_id: str | None
    type: str
    optout_langs: tuple[str, ...]
    body_truncated: bool
    handoff: bool


def _readable_contact_body(entry: schema.WalEntry) -> str | None:
    # OQ-P1-02 decision (recorded in P1_DEVIATIONS.md): the contact card's
    # display name is kept as readable body text; the numbers list and the
    # reaction target id are NOT stored (no column/table added this batch).
    if entry.contact is None:
        return None
    return entry.contact.name or None


def _readable_reaction_body(entry: schema.WalEntry) -> str | None:
    if entry.reaction is None:
        return None
    return entry.reaction.text or None


def _message_fields(
    entry: schema.WalEntry, max_body_chars: int,
) -> tuple[str | None, str | None, str | None, dict[str, Any] | None, bool]:
    """(body, media_object_key, media_type, location_jsonb, body_truncated)."""
    body: str | None = None
    media_object_key: str | None = None
    media_type: str | None = None
    location: dict[str, Any] | None = None
    truncated = False

    if entry.type in ("text", "unsupported"):
        if entry.text:
            body, truncated = _truncate(entry.text, max_body_chars)
    elif entry.type in ("image", "audio", "video", "document", "sticker"):
        if entry.media is not None:
            media_type = entry.media.kind
            if entry.media.status == "ok":
                media_object_key = entry.media.object_key
        if entry.text:
            body, truncated = _truncate(entry.text, max_body_chars)
    elif entry.type == "location":
        if entry.location is not None:
            location = {
                "lat": entry.location.lat,
                "lng": entry.location.lng,
                "name": entry.location.name,
                "address": entry.location.address,
            }
            if entry.location.is_live is not None:
                location["is_live"] = entry.location.is_live
    elif entry.type == "contact":
        body = _readable_contact_body(entry)
    elif entry.type == "reaction":
        body = _readable_reaction_body(entry)

    return body, media_object_key, media_type, location, truncated


class RealtimeWorker:
    """Owns the worker's whole runtime: connections, threads, health state."""

    def __init__(self, settings: WorkerSettings) -> None:
        self.settings = settings
        self.client: Any = None
        self.cache_client: Any = None
        self.reader: StreamReader | None = None
        self.evt_reader: StreamReader | None = None
        self.gateway_client: GatewayClient | None = None
        self.commerce_port: SharwaCommerceAdapter | None = None
        self.llm_router: Any = None
        self.embed_handle: Any = None
        self.summary_handle: Any = None
        self.stop_event = threading.Event()
        self._fatal: BaseException | None = None
        self._shard_alive: dict[int, bool] = {}
        self._shard_last_success: dict[int, float] = {}
        self._shard_last_cycle: dict[int, float] = {}
        self._seen_unknown_fields: set[str] = set()
        self._seen_unknown_catalog_types: set[str] = set()
        self._health_server: obs_http.ObservabilityServer | None = None

    def _setup(self) -> None:
        core_db.init_pool(
            self.settings,
            system_dsn=self.settings.db.system_dsn,
            system_pool_max=self.settings.system_pool_max,
        )
        self.client = create_redis_client(self.settings)
        wait_for_ready(self.client, timeout_ms=self.settings.redis_durable_timeout_ms)
        # redis-cache client for inbox Pub/Sub (W5): the workers publish the
        # same committed events the api subscribes to. Same fail-fast posture as
        # the durable client.
        self.cache_client = redis.Redis(
            host=self.settings.redis_cache_host, port=self.settings.redis_cache_port,
            password=self.settings.redis_cache_password, decode_responses=True,
        )
        ws_publish.configure(self.cache_client)
        self.reader = StreamReader(self.client, self.settings)
        for shard in range(self.settings.ingest_shards):
            self.reader.ensure_group(f"in:{shard}")
        self.evt_reader = StreamReader(
            self.client, self.settings,
            group=self.settings.core_evt_group, dlq_stream=self.settings.core_evt_dlq_stream,
        )
        for shard in range(self.settings.ingest_shards):
            self.evt_reader.ensure_group(f"evt:{shard}")
        self.gateway_client = GatewayClient(
            GatewayConfig(base_url=self.settings.gateway_base_url),
            api_key=self.settings.gateway_api_key,
        )
        if self.settings.commerce_base_url:
            self.commerce_port = SharwaCommerceAdapter(
                CommerceClient(self.settings.commerce_base_url, timeout_s=self.settings.commerce_timeout_s)
            )
        else:
            obs_logging.log_event(
                _log, event="catalog.reconcile.disabled", component="catalog",
                level=logging.WARNING, reason="COMMERCE_BASE_URL empty",
            )
        self.llm_router = turn.build_router(self.settings)
        self.embed_handle = embed.build_embed(self.settings)
        self.summary_handle = summary.build_summary(self.settings)
        self.verify_rules = verify.build_rules(self.settings)
        metrics.tools_enabled.set(1 if self.settings.tools_enabled else 0)
        self._refresh_gazetteer_gauge()
        metrics.core_worker_up.set(1)

    def _refresh_gazetteer_gauge(self) -> None:
        """H75: publish gazetteer_rows{level} at boot. A bare tenant-pool
        connection (no SET LOCAL app.tenant_id) sees only the shared reference
        rows (tenant_id IS NULL) under the gazetteer_read RLS policy."""
        try:
            from app.db import context as core_db_context
            with core_db_context._tenant_pool.connection() as conn, conn.transaction():
                for level, count in repos_geo.count_gazetteer_by_level(conn):
                    metrics.gazetteer_rows.labels(level).set(count)
        except Exception as exc:  # noqa: BLE001 - a gauge refresh must not block boot
            obs_logging.log_event(
                _log, event="gazetteer.gauge.error", component="realtime",
                level=logging.WARNING, error=str(exc),
            )

    def _teardown(self) -> None:
        metrics.core_worker_up.set(0)
        if self._health_server is not None:
            self._health_server.stop()
        try:
            core_db.close_pool()
        except Exception as exc:
            obs_logging.log_event(
                _log, event="worker.teardown.close_pool_error", component="realtime",
                level=logging.WARNING, error=str(exc),
            )
        if self.client is not None:
            try:
                self.client.close()
            except Exception as exc:
                obs_logging.log_event(
                    _log, event="worker.teardown.close_client_error", component="realtime",
                    level=logging.WARNING, error=str(exc),
                )

    def _commit(
        self, entry: schema.WalEntry, resolution: schema.SessionResolution,
    ) -> CommitResult:
        ws_publish.reset_pending()
        try:
            with tenant_tx(resolution.tenant_id) as conn:
                if entry.type == "identity_update":
                    result = self._commit_identity_update(conn, entry, resolution)
                elif entry.type == "human_takeover_signal":
                    result = self._commit_human_takeover(conn, entry, resolution)
                else:
                    result = self._commit_customer_message(conn, entry, resolution)
            # Commit landed; publish the queued inbox_events now (H33: after COMMIT).
            ws_publish.flush_publishes()
            return result
        except PermanentError:
            raise
        except Exception as exc:
            if repos_ingest.classify_db_error(exc) == "transient":
                raise TransientError(f"transient db error: {exc}") from exc
            raise

    def _commit_identity_update(
        self, conn: Any, entry: schema.WalEntry, resolution: schema.SessionResolution,
    ) -> CommitResult:
        if not entry.lid:
            raise PermanentError("identity_update entry has no lid")
        repos_ingest.upsert_customer(
            conn, tenant_id=resolution.tenant_id, wa_id=entry.lid,
            phone_e164=entry.phone_e164, display_name=None,
        )
        return CommitResult(
            outcome="committed", session_id=entry.session_id,
            provider_message_id=entry.provider_message_id, type=entry.type,
            optout_langs=(), body_truncated=False, handoff=False,
        )

    def _commit_human_takeover(
        self, conn: Any, entry: schema.WalEntry, resolution: schema.SessionResolution,
    ) -> CommitResult:
        provider_message_id = entry.provider_message_id
        if not provider_message_id:
            raise PermanentError("human_takeover_signal has no provider_message_id")
        if entry.identity is None or not entry.identity.wa_id:
            raise PermanentError("human_takeover_signal has no identity.wa_id")

        if not repos_ingest.insert_inbound_event(
            conn, tenant_id=resolution.tenant_id,
            channel_account_id=resolution.channel_account_id,
            provider_message_id=provider_message_id,
        ):
            return CommitResult(
                outcome="duplicate", session_id=entry.session_id,
                provider_message_id=provider_message_id, type=entry.type,
                optout_langs=(), body_truncated=False, handoff=False,
            )

        customer_id = repos_ingest.upsert_customer(
            conn, tenant_id=resolution.tenant_id, wa_id=entry.identity.wa_id,
            phone_e164=entry.identity.phone_e164, display_name=None,
        )
        conv = repos_ingest.upsert_conversation(
            conn, tenant_id=resolution.tenant_id,
            channel_account_id=resolution.channel_account_id, customer_id=customer_id,
        )
        # The trigger pauses the bot only when it was active; a second human
        # reply on an already-paused conversation is not a NEW transition.
        was_active = conv.bot_status == "active"
        body, truncated = _truncate(entry.text or "", self.settings.core_ingest_max_body_chars)
        repos_ingest.insert_message(
            conn, tenant_id=resolution.tenant_id, conversation_id=conv.id,
            direction="out", sent_by="staff", msg_type="text",
            body=body or None, media_object_key=None, media_type=None,
            location=None, provider_message_id=provider_message_id,
            status="sent", staff_id=None,
        )
        return CommitResult(
            outcome="committed", session_id=entry.session_id,
            provider_message_id=provider_message_id, type=entry.type,
            optout_langs=(), body_truncated=truncated, handoff=was_active,
        )

    def _commit_customer_message(
        self, conn: Any, entry: schema.WalEntry, resolution: schema.SessionResolution,
    ) -> CommitResult:
        provider_message_id = entry.provider_message_id
        if not provider_message_id:
            raise PermanentError("customer message has no provider_message_id")
        if entry.identity is None or not entry.identity.wa_id:
            raise PermanentError("customer message has no identity.wa_id")

        if not repos_ingest.insert_inbound_event(
            conn, tenant_id=resolution.tenant_id,
            channel_account_id=resolution.channel_account_id,
            provider_message_id=provider_message_id,
        ):
            return CommitResult(
                outcome="duplicate", session_id=entry.session_id,
                provider_message_id=provider_message_id, type=entry.type,
                optout_langs=(), body_truncated=False, handoff=False,
            )

        customer_id = repos_ingest.upsert_customer(
            conn, tenant_id=resolution.tenant_id, wa_id=entry.identity.wa_id,
            phone_e164=entry.identity.phone_e164, display_name=None,
        )
        conv = repos_ingest.upsert_conversation(
            conn, tenant_id=resolution.tenant_id,
            channel_account_id=resolution.channel_account_id, customer_id=customer_id,
        )

        # Reopen a closed conversation BEFORE inserting the message, so the
        # trigger's needs_turn condition sees bot_status='active' (P1.1.3 #4).
        if conv.bot_status == "closed":
            if not repos_ingest.reopen_closed_conversation(
                conn, conversation_id=conv.id, expected_version=conv.version,
            ):
                fresh = repos_ingest.fetch_conversation(conn, conversation_id=conv.id)
                if not repos_ingest.reopen_closed_conversation(
                    conn, conversation_id=fresh.id, expected_version=fresh.version,
                ):
                    raise TransientError("stale version while reopening closed conversation")

        optout_langs: tuple[str, ...] = ()
        # D6: opt-out must be detected on ANY customer text body - a media
        # caption is stored in `body` exactly like a text message, so an image
        # captioned "إيقاف" must opt out too (marketing fails closed, H4).
        # location/contact/reaction carry no free-text body and are excluded.
        if entry.type not in ("location", "contact", "reaction") and entry.text:
            optout_langs = optout.detect(
                entry.text,
                phrases_ar=self.settings.core_optout_phrases_ar,
                phrases_en=self.settings.core_optout_phrases_en,
            )

        body, media_object_key, media_type, location, truncated = _message_fields(
            entry, self.settings.core_ingest_max_body_chars,
        )
        msg_type = schema.message_type_for(entry.type)
        message_id, message_seq = repos_ingest.insert_message(
            conn, tenant_id=resolution.tenant_id, conversation_id=conv.id,
            direction="in", sent_by="customer", msg_type=msg_type,
            body=body, media_object_key=media_object_key, media_type=media_type,
            location=location, provider_message_id=provider_message_id,
            status="received", staff_id=None,
        )
        message_payload = {
            "conversation_id": str(conv.id), "message_id": str(message_id),
            "seq": message_seq, "direction": "in", "sent_by": "customer",
            "type": msg_type,
        }
        inbox_seq = repos_inbox.write_inbox_event(
            conn, tenant_id=resolution.tenant_id, event_type="message.new",
            payload=message_payload,
        )
        ws_publish.queue_publish(
            tenant_id=resolution.tenant_id, seq=inbox_seq, event_type="message.new",
            payload=message_payload,
        )
        metrics.inbox_events_written_total.labels("message.new").inc()

        if optout_langs:
            repos_ingest.insert_suppressions(
                conn, tenant_id=resolution.tenant_id, customer_id=customer_id,
                scopes=repos_ingest.OPTOUT_SCOPES, reason=repos_ingest.OPTOUT_REASON,
            )
            # D4: STOP cancels the queue immediately - every pending automation row
            # for a suppressed scope's templates is dropped (sending rows are caught
            # by the send-time gate, H76). The scope->templates map is DERIVED from
            # the catalog, never re-written here.
            for scope in repos_ingest.OPTOUT_SCOPES:
                template_ids = proactive.template_ids_for_scope(scope)
                if template_ids:
                    repos_policy.cancel_pending_proactive(
                        conn, tenant_id=resolution.tenant_id, customer_id=customer_id,
                        template_ids=template_ids,
                    )

        return CommitResult(
            outcome="committed", session_id=entry.session_id,
            provider_message_id=provider_message_id, type=entry.type,
            optout_langs=optout_langs, body_truncated=truncated, handoff=False,
        )

    # --- per-entry processing -------------------------------------------------

    def _park_and_ack(
        self, reader: StreamReader, stream: str, entry_id: str,
        raw_data: str, reason: str, entry_type: str,
    ) -> None:
        metrics.ingest_core_dlq_total.labels(reason).inc()
        metrics.ingest_core_messages_total.labels(entry_type, "dlq").inc()
        reader.park_dlq(stream, entry_id, raw_data, reason)
        reader.ack(stream, entry_id)

    def _finalize(
        self, reader: StreamReader, stream: str, entry_id: str,
        result: CommitResult, entry_type: str,
    ) -> None:
        if result.outcome == "duplicate":
            metrics.ingest_core_duplicates_total.inc()
        if result.outcome == "committed":
            for lang in result.optout_langs:
                metrics.optout_detected_total.labels(lang).inc()
            if result.body_truncated:
                metrics.ingest_core_body_truncated_total.inc()
            if result.handoff:
                metrics.handoff_transitions_total.labels("staff_replied").inc()
            if result.type == "identity_update":
                metrics.ingest_core_identity_updates_total.inc()
            if result.provider_message_id:
                reader.extend_dedupe_marker(result.session_id, result.provider_message_id)
        metrics.ingest_core_messages_total.labels(entry_type, result.outcome).inc()
        reader.ack(stream, entry_id)

    def _log_unknown_fields(self, unknown: frozenset[str]) -> None:
        for field in sorted(unknown):
            metrics.ingest_core_unknown_fields_total.inc()
            if field not in self._seen_unknown_fields:
                self._seen_unknown_fields.add(field)
                obs_logging.log_event(
                    _log, event="wal.unknown_field", component="realtime",
                    level=logging.WARNING, field=field,
                )

    def _mark_shard_success(self, shard: int) -> None:
        now = time.time()
        self._shard_last_success[shard] = time.monotonic()
        metrics.ingest_core_last_success_timestamp.labels(str(shard)).set(now)

    def process_one(
        self, shard: int, stream: str, entry_id: str, fields: dict[str, str],
    ) -> None:
        started = time.monotonic()
        entry_type = "unknown"
        raw_data = fields.get("data", "")
        reader = self.reader
        assert reader is not None

        try:
            obj, raw_data = parse_data_field(fields)
        except PermanentError:
            self._park_and_ack(reader, stream, entry_id, raw_data, "corrupt_json", entry_type)
            return

        try:
            try:
                entry, unknown = schema.parse_entry(obj)
            except ValidationError:
                self._park_and_ack(reader, stream, entry_id, raw_data, "schema_mismatch", entry_type)
                return
            entry_type = entry.type
            self._log_unknown_fields(unknown)

            resolution = schema.resolve_session(entry.session_id)
            if resolution is None:
                metrics.ingest_core_unknown_session_total.inc()
                metrics.ingest_core_messages_total.labels(entry_type, "unknown_session").inc()
                reader.ack(stream, entry_id)
                obs_logging.log_event(
                    _log, event="wal.unknown_session", component="realtime",
                    session_id=entry.session_id, outcome="unknown_session",
                )
                self._mark_shard_success(shard)
                return
            if resolution.engine != "ai_core":
                metrics.ingest_core_skipped_total.labels("engine_not_ai_core").inc()
                metrics.ingest_core_messages_total.labels(entry_type, "skipped").inc()
                reader.ack(stream, entry_id)
                self._mark_shard_success(shard)
                return

            result = self._commit(entry, resolution)
            metrics.ingest_core_commit_seconds.observe(time.monotonic() - started)
            self._finalize(reader, stream, entry_id, result, entry_type)
            metrics.ingest_core_ack_seconds.observe(time.monotonic() - started)
            self._mark_shard_success(shard)
        except PermanentError:
            self._park_and_ack(reader, stream, entry_id, raw_data, "schema_mismatch", entry_type)
            return
        except TransientError:
            attempts = reader.record_attempt(stream, entry_id)
            if attempts >= self.settings.core_ingest_max_attempts:
                self._park_and_ack(reader, stream, entry_id, raw_data, "max_attempts", entry_type)
                return
            raise

    # --- shard loop -----------------------------------------------------------

    def _run_shard(self, shard: int) -> None:
        stream = f"in:{shard}"
        self._shard_alive[shard] = True
        reader = self.reader
        assert reader is not None
        try:
            while not self.stop_event.is_set():
                try:
                    batch = reader.read_batch(stream)
                    # A successful (even empty) read is a healthy cycle - record
                    # it so /healthz can detect a stuck shard (P1.0.4).
                    self._shard_last_cycle[shard] = time.monotonic()
                    for entry_id, fields in batch:
                        if self.stop_event.is_set():
                            break
                        self.process_one(shard, stream, entry_id, fields)
                except TransientError:
                    time.sleep(_jitter(1000))
                    continue
                except RedisError:
                    # A transient redis-durable outage during read/ack/attempt:
                    # the entry stays in the PEL and is redelivered (idempotent),
                    # so back off instead of failing the whole process (D3 fix).
                    time.sleep(_jitter(1000))
                    continue
                except Exception as exc:
                    # A DB error is retryable iff classify_db_error says so;
                    # anything else is a genuine bug -> fail fast (H3).
                    if repos_ingest.classify_db_error(exc) == "transient":
                        time.sleep(_jitter(1000))
                        continue
                    raise
        except Exception as exc:  # noqa: BLE001 - fail-fast on a real bug
            self._fatal = exc
            # D4: the one moment the worker dies is the one moment the cause
            # must not be lost (H3). The exception is never a customer message
            # (H20), so logging its text/class/traceback is safe.
            obs_logging.log_event(
                _log, event="worker.fatal", component="realtime",
                level=logging.ERROR, shard=shard,
                error=str(exc), error_class=type(exc).__name__,
                traceback=traceback.format_exc(),
            )
        finally:
            self._shard_alive[shard] = False
            self.stop_event.set()

    def _lag_loop(self) -> None:
        reader = self.reader
        evt_reader = self.evt_reader
        assert reader is not None and evt_reader is not None
        while not self.stop_event.is_set():
            for shard in range(self.settings.ingest_shards):
                try:
                    lag, pel = reader.group_stats(f"in:{shard}")
                    metrics.ingest_core_stream_lag.labels(str(shard)).set(lag)
                    metrics.ingest_core_pel_size.labels(str(shard)).set(pel)
                except Exception as exc:
                    obs_logging.log_event(
                        _log, event="worker.lag_collect_error", component="realtime",
                        level=logging.WARNING, shard=shard, error=str(exc),
                    )
                try:
                    lag, pel = evt_reader.group_stats(f"evt:{shard}")
                    metrics.evt_stream_lag.labels(str(shard)).set(lag)
                    metrics.evt_pel_size.labels(str(shard)).set(pel)
                except Exception as exc:
                    obs_logging.log_event(
                        _log, event="worker.evt_lag_collect_error", component="realtime",
                        level=logging.WARNING, shard=shard, error=str(exc),
                    )
            self.stop_event.wait(15.0)

    def _health_ok(self) -> bool:
        reason = self._health_reason()
        if reason is None:
            return True
        # D4: "unhealthy" with no reason is impossible to diagnose in production.
        obs_logging.log_event(
            _log, event="worker.unhealthy", component="realtime",
            level=logging.WARNING, reason=reason,
        )
        return False

    def _health_reason(self) -> str | None:
        if self.stop_event.is_set() or self._fatal is not None:
            return "shutting_down_or_fatal"
        if self.reader is None or self.client is None:
            return "not_initialized"
        try:
            self.client.ping()
        except Exception as exc:
            return f"redis_unreachable:{type(exc).__name__}"
        try:
            with system_tx() as conn:
                repos.check_alive(conn)
        except Exception as exc:
            return f"postgres_unreachable:{type(exc).__name__}"
        for shard in range(self.settings.ingest_shards):
            if not self._shard_alive.get(shard):
                return f"shard_thread_dead:{shard}"
            # A shard must have completed a read cycle within a written threshold
            # (5x the BLOCK time, min 15s) - a shard stuck in a long DB/Redis call
            # or a dead loop fails health even though the thread is nominally alive.
            last_cycle = self._shard_last_cycle.get(shard)
            stale_s = max(15.0, self.settings.core_ingest_block_ms * 5 / 1000.0)
            if last_cycle is None or time.monotonic() - last_cycle > stale_s:
                return f"shard_stale:{shard}"
        return None

    def _on_signal(self, signum: int, _frame: object) -> None:
        obs_logging.log_event(
            _log, event="worker.signal", component="realtime", signal=signum,
        )
        self.stop_event.set()

    # --- P1.2 thread groups ---------------------------------------------------

    def _run_turn_worker(self) -> None:
        while not self.stop_event.is_set():
            try:
                with system_tx() as conn:
                    candidates = repos_outbox.claim_due_turns(
                        conn, limit=self.settings.core_dispatch_batch,
                    )
                for conversation_id, tenant_id in candidates:
                    if self.stop_event.is_set():
                        break
                    try:
                        ws_publish.reset_pending()
                        turn.process_turn(
                            settings=self.settings,
                            conversation_id=conversation_id, tenant_id=tenant_id,
                            router=self.llm_router,
                            embed=self.embed_handle, cache_client=self.cache_client,
                            rules=self.verify_rules, commerce=self.commerce_port,
                        )
                        # Commit landed; publish the turn's queued inbox_events (H33).
                        ws_publish.flush_publishes()
                    except TransientError:
                        # concurrent modification / stale version => a real
                        # invalidation, not a silent drop.
                        metrics.turn_invalidated_total.inc()
                        time.sleep(_jitter(500))
            except Exception as exc:  # noqa: BLE001 - fail-fast on a real bug
                if repos_ingest.classify_db_error(exc) == "transient":
                    time.sleep(_jitter(1000))
                    continue
                self._fatal = exc
                obs_logging.log_event(
                    _log, event="worker.fatal", component="turn",
                    level=logging.ERROR, error=str(exc), error_class=type(exc).__name__,
                    traceback=traceback.format_exc(),
                )
                self.stop_event.set()
                return
            self.stop_event.wait(0.5)

    def _run_retention(self) -> None:
        """W7: periodically delete inbox_events older than the retention window via
        the SECURITY DEFINER function (0006_p1_ws.sql), in bounded batches (H4).
        Never touches tenant_counters.inbox_seq - the seq stays monotonic forever."""
        while not self.stop_event.is_set():
            try:
                cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
                    days=self.settings.inbox_events_retention_days,
                )
                with system_tx() as conn:
                    deleted = repos_inbox.delete_old_inbox_events(
                        conn, cutoff=cutoff,
                        batch=self.settings.inbox_events_delete_batch,
                        max_batches=self.settings.inbox_events_delete_max_batches,
                    )
                metrics.inbox_events_deleted_total.inc(deleted)
                metrics.inbox_events_retention_last_run_timestamp.set(time.time())
            except Exception as exc:
                obs_logging.log_event(
                    _log, event="retention.error", component="realtime",
                    level=logging.ERROR, error=str(exc), error_class=type(exc).__name__,
                )
            self.stop_event.wait(self.settings.inbox_events_retention_interval_s)

    def _run_dispatch_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                dispatch.dispatch_cycle(self.settings, self.gateway_client)
            except Exception as exc:
                obs_logging.log_event(
                    _log, event="dispatch.cycle_error", component="dispatch",
                    level=logging.ERROR, error=str(exc),
                )
            self.stop_event.wait(self.settings.core_dispatch_interval_ms / 1000.0)

    def _run_evt(self, shard: int) -> None:
        stream = f"evt:{shard}"
        reader = self.evt_reader
        assert reader is not None
        while not self.stop_event.is_set():
            try:
                batch = reader.read_batch(stream)
                for entry_id, fields in batch:
                    if self.stop_event.is_set():
                        break
                    try:
                        evt.process_event(reader, stream, entry_id, fields)
                    except PermanentError:
                        reader.park_dlq(stream, entry_id, fields.get("data", ""), "corrupt_json")
                        reader.ack(stream, entry_id)
            except TransientError:
                time.sleep(_jitter(1000))
                continue
            except Exception as exc:  # noqa: BLE001 - fail-fast on a real bug
                if repos_ingest.classify_db_error(exc) == "transient":
                    time.sleep(_jitter(1000))
                    continue
                self._fatal = exc
                obs_logging.log_event(
                    _log, event="worker.fatal", component="evt",
                    level=logging.ERROR, shard=shard, error=str(exc),
                    error_class=type(exc).__name__, traceback=traceback.format_exc(),
                )
                self.stop_event.set()
                return

    # --- lifecycle ------------------------------------------------------------


    def _run_catalog_reconcile(self) -> None:
        """P1.4 C3: periodically pull platform catalog events and apply them.

        Runs as a daemon thread; a transient error is logged and the loop keeps
        going (a reconcile is a background refresh, not a fail-fast path).
        """
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                if self.commerce_port is not None:
                    catalog.reconcile_once(
                        self.commerce_port, settings=self.settings,
                        seen_unknown_types=self._seen_unknown_catalog_types,
                    )
            except Exception as exc:  # noqa: BLE001 - survive transient errors, keep the thread alive
                obs_logging.log_event(
                    _log, event="catalog.reconcile.crash", component="catalog",
                    level=logging.ERROR, error=str(exc),
                )
            # P1.5: refresh the cross-tenant budget_state gauge (no tenant_id label, H20).
            try:
                with system_tx() as conn:
                    for state, count in repos_llm.budget_state_counts(conn):
                        metrics.budget_state.labels(state).set(count)
            except Exception as exc:  # noqa: BLE001 - gauge refresh is best-effort
                obs_logging.log_event(
                    _log, event="budget.gauge.error", component="catalog",
                    level=logging.WARNING, error=str(exc),
                )
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(1.0, self.settings.catalog_reconcile_interval_s - elapsed))

    def _run_embed(self) -> None:
        """P1.5b V2: periodically embed products whose vectors are missing/stale.

        A background refresh (not a fail-fast path): a transient error is logged
        and the loop keeps going; products left unembedded are retried next cycle.
        """
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                embed.embed_once(settings=self.settings, handle=self.embed_handle)
            except Exception as exc:  # noqa: BLE001 - background refresh, keep the thread alive
                obs_logging.log_event(
                    _log, event="embed.crash", component="embed",
                    level=logging.ERROR, error=str(exc),
                )
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(1.0, self.settings.embed_interval_s - elapsed))

    def _run_summary(self) -> None:
        """P1.5b V6: periodically refresh rolling summaries in a SEPARATE thread
        (the customer never waits on a summary)."""
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                summary.summary_once(settings=self.settings, handle=self.summary_handle)
            except Exception as exc:  # noqa: BLE001 - background refresh, keep the thread alive
                obs_logging.log_event(
                    _log, event="summary.crash", component="summary",
                    level=logging.ERROR, error=str(exc),
                )
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(1.0, self.settings.summary_interval_s - elapsed))

    def _run_stock_sweep(self) -> None:
        """P2.3 §5.2: periodically sweep waiting variants - read the platform
        stock observation (OUTSIDE any transaction, H40), then allocate + notify
        in ONE short transaction. A background refresh, not a fail-fast path."""
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                if self.commerce_port is not None:
                    stock.sweep_once(
                        self.commerce_port, settings=self.settings, rules=self.verify_rules,
                    )
            except Exception as exc:  # noqa: BLE001 - background refresh, keep the thread alive
                obs_logging.log_event(
                    _log, event="stock.sweep.crash", component="stock",
                    level=logging.ERROR, error=str(exc),
                )
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(1.0, self.settings.stock_sweep_interval_s - elapsed))

    def _run_policy_sweep(self) -> None:
        """P3.1 §3 F: the send-policy sweeper (number_health + warm-up + health),
        beside the dispatcher. A background refresh, not a fail-fast path."""
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                policy_sweep.sweep_once(self.settings)
            except Exception as exc:  # noqa: BLE001 - background refresh, keep the thread alive
                obs_logging.log_event(
                    _log, event="policy.sweep.crash", component="policy_sweep",
                    level=logging.ERROR, error=str(exc),
                )
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(1.0, self.settings.send_policy_sweep_interval_s - elapsed))

    def run(self) -> int:
        self._setup()

        self._health_server = obs_http.ObservabilityServer(
            port=self.settings.core_worker_metrics_port,
            metrics_token=self.settings.metrics_token,
            health_ok=self._health_ok,
        )
        self._health_server.start()

        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)

        threads: list[threading.Thread] = []
        for shard in range(self.settings.ingest_shards):
            t = threading.Thread(target=self._run_shard, args=(shard,), daemon=True)
            t.start()
            threads.append(t)
        for _ in range(self.settings.core_turn_workers):
            t = threading.Thread(target=self._run_turn_worker, daemon=True)
            t.start()
            threads.append(t)
        for shard in range(self.settings.ingest_shards):
            t = threading.Thread(target=self._run_evt, args=(shard,), daemon=True)
            t.start()
            threads.append(t)
        dispatch_thread = threading.Thread(target=self._run_dispatch_loop, daemon=True)
        dispatch_thread.start()
        threads.append(dispatch_thread)
        lag_thread = threading.Thread(target=self._lag_loop, daemon=True)
        lag_thread.start()
        retention_thread = threading.Thread(target=self._run_retention, daemon=True)
        retention_thread.start()
        threads.append(retention_thread)
        catalog_thread = threading.Thread(target=self._run_catalog_reconcile, daemon=True)
        catalog_thread.start()
        threads.append(catalog_thread)
        embed_thread = threading.Thread(target=self._run_embed, daemon=True)
        embed_thread.start()
        threads.append(embed_thread)
        summary_thread = threading.Thread(target=self._run_summary, daemon=True)
        summary_thread.start()
        threads.append(summary_thread)
        stock_thread = threading.Thread(target=self._run_stock_sweep, daemon=True)
        stock_thread.start()
        threads.append(stock_thread)
        policy_thread = threading.Thread(target=self._run_policy_sweep, daemon=True)
        policy_thread.start()
        threads.append(policy_thread)

        obs_logging.log_event(_log, event="worker.started", component="realtime")

        self.stop_event.wait()
        # D1 fix: the shutdown deadline must start NOW (when the signal arrived
        # or a shard failed), not at process start - otherwise a worker that ran
        # longer than shutdown_timeout_s would join its threads with a 0 timeout
        # and never let in-flight entries finish (graceful-shutdown defect).
        shutdown_started = time.monotonic()
        deadline = shutdown_started + self.settings.core_ingest_shutdown_timeout_s
        for t in threads:
            t.join(timeout=max(0.0, deadline - time.monotonic()))

        self._teardown()
        # D1: measure and report the real shutdown time (A17 evidence).
        shutdown_s = time.monotonic() - shutdown_started
        obs_logging.log_event(
            _log, event="worker.stopped", component="realtime",
            duration_ms=round(shutdown_s * 1000, 1),
        )
        return 0 if self._fatal is None else 1


def main() -> int:
    settings = WorkerSettings.load()
    worker = RealtimeWorker(settings)
    return worker.run()


if __name__ == "__main__":
    raise SystemExit(main())
