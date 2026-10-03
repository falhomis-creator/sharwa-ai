"""core/app/obs/metrics.py - the realtime worker's Prometheus registry.

P1.1.6 mandates every family below on the worker's OWN registry (never shared
with `api` - app.main keeps its own CollectorRegistry for the same reason the
gateway/forwarder split theirs: a metric registered without a real increment
point reads as a misleading confirmed "0"). Every family here is wired to a
real increment/observe/set point in the same change (app/workers/*), following
P0 D-22's rule.

H20: no high-cardinality labels. Labels are limited to the fixed, bounded
vocabularies {type,outcome,reason,lang,shard}; tenant_id/session_id are never
labels (disaster #16 - unbounded label cardinality).
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

registry = CollectorRegistry()

# ingest_core_messages_total{type,outcome} - one increment per processed entry;
# outcome is a bounded enum: committed|duplicate|skipped|unknown_session|dlq.
ingest_core_messages_total = Counter(
    "ingest_core_messages_total",
    "Total WAL entries processed by the core-ingest worker, by entry type and outcome.",
    labelnames=["type", "outcome"],
    registry=registry,
)

# ingest_core_commit_seconds - histogram: entry read -> COMMIT (P1.1.6/A14).
ingest_core_commit_seconds = Histogram(
    "ingest_core_commit_seconds",
    "Seconds from reading one WAL entry until its PostgreSQL transaction COMMITs.",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=registry,
)

# ingest_core_ack_seconds - histogram: entry read -> XACK.
ingest_core_ack_seconds = Histogram(
    "ingest_core_ack_seconds",
    "Seconds from reading one WAL entry until it is XACKed.",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=registry,
)

ingest_core_duplicates_total = Counter(
    "ingest_core_duplicates_total",
    "WAL entries dropped as duplicates by the inbound_events UNIQUE constraint (permanent, Redis-independent dedupe).",
    registry=registry,
)

ingest_core_unknown_session_total = Counter(
    "ingest_core_unknown_session_total",
    "WAL entries whose session_id did not resolve in channel_accounts (ACKed, no write - gateway acted in good faith).",
    registry=registry,
)

ingest_core_skipped_total = Counter(
    "ingest_core_skipped_total",
    "WAL entries deliberately skipped by the worker, by reason.",
    labelnames=["reason"],
    registry=registry,
)

ingest_core_dlq_total = Counter(
    "ingest_core_dlq_total",
    "WAL entries parked on dlq:in:core, by reason (corrupt_json|schema_mismatch|max_attempts).",
    labelnames=["reason"],
    registry=registry,
)

ingest_core_unknown_fields_total = Counter(
    "ingest_core_unknown_fields_total",
    "Unknown JSON fields observed in WAL entries (tolerated per the additive-extension contract, but measured).",
    registry=registry,
)

ingest_core_body_truncated_total = Counter(
    "ingest_core_body_truncated_total",
    "Message bodies truncated at CORE_INGEST_MAX_BODY_CHARS (counted + logged, never silent).",
    registry=registry,
)

ingest_core_identity_updates_total = Counter(
    "ingest_core_identity_updates_total",
    "identity_update entries applied to customers.phone_e164.",
    registry=registry,
)

handoff_transitions_total = Counter(
    "handoff_transitions_total",
    "Human-takeover handoff transitions, by reason (staff_replied).",
    labelnames=["reason"],
    registry=registry,
)

optout_detected_total = Counter(
    "optout_detected_total",
    "Opt-out phrases detected on inbound customer messages, by language (ar|en). Never stores the text (H20).",
    labelnames=["lang"],
    registry=registry,
)

# Stream-health gauges, refreshed by the lag collector (stream.py) every <=15s.
ingest_core_stream_lag = Gauge(
    "ingest_core_stream_lag",
    "Per-shard consumer-group lag (XINFO GROUPS lag field).",
    labelnames=["shard"],
    registry=registry,
)

ingest_core_pel_size = Gauge(
    "ingest_core_pel_size",
    "Per-shard pending-entry-list size (XINFO GROUPS pel-count).",
    labelnames=["shard"],
    registry=registry,
)

ingest_core_last_success_timestamp = Gauge(
    "ingest_core_last_success_timestamp",
    "Unix timestamp of the last successful commit+ack per shard.",
    labelnames=["shard"],
    registry=registry,
)

core_worker_up = Gauge(
    "core_worker_up",
    "1 while the worker process is running (set at startup, cleared on shutdown).",
    registry=registry,
)

# --- P1.2 outbound metrics ----------------------------------------------------

turn_processed_total = Counter(
    "turn_processed_total",
    "Conversation turns processed, by decision (handoff|optout_confirm|safe_ack).",
    labelnames=["decision"],
    registry=registry,
)

turn_invalidated_total = Counter(
    "turn_invalidated_total",
    "Turns cancelled by the H26 invalidation rule (a new inbound arrived mid-turn).",
    registry=registry,
)

turn_skipped_total = Counter(
    "turn_skipped_total",
    "Turns skipped, by reason.",
    labelnames=["reason"],
    registry=registry,
)

turn_duration_seconds = Histogram(
    "turn_duration_seconds",
    "Seconds for one turn transaction.",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=registry,
)

outbox_written_total = Counter(
    "outbox_written_total",
    "Outbox rows written, by message_class.",
    labelnames=["message_class"],
    registry=registry,
)

dispatch_attempts_total = Counter(
    "dispatch_attempts_total",
    "Dispatch attempts, by result (sent|dropped_stale|dropped_policy|retry|failed).",
    labelnames=["result"],
    registry=registry,
)

dispatch_duration_seconds = Histogram(
    "dispatch_duration_seconds",
    "Seconds for one dispatch attempt.",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=registry,
)

dispatch_queue_depth = Gauge(
    "dispatch_queue_depth",
    "Current outbox depth (pending + sending).",
    registry=registry,
)

dispatch_oldest_pending_seconds = Gauge(
    "dispatch_oldest_pending_seconds",
    "Age in seconds of the oldest pending outbox row.",
    registry=registry,
)

evt_processed_total = Counter(
    "evt_processed_total",
    "evt:{shard} events processed, by type and outcome.",
    labelnames=["type", "outcome"],
    registry=registry,
)

evt_unknown_client_msg_total = Counter(
    "evt_unknown_client_msg_total",
    "evt events whose client_msg_id is unknown to core (sent by something else).",
    registry=registry,
)

evt_unknown_type_total = Counter(
    "evt_unknown_type_total",
    "evt events with an unknown type (e.g. delivered - not produced yet).",
    registry=registry,
)

evt_stream_lag = Gauge(
    "evt_stream_lag",
    "Per-shard evt consumer-group lag.",
    labelnames=["shard"],
    registry=registry,
)

evt_pel_size = Gauge(
    "evt_pel_size",
    "Per-shard evt pending-entry-list size.",
    labelnames=["shard"],
    registry=registry,
)


# --- P1.3 inbox metrics (PROMPT §5.7) -----------------------------------------

inbox_api_requests_total = Counter(
    "inbox_api_requests_total",
    "Inbox REST API requests, by route and HTTP status.",
    labelnames=["route", "status"],
    registry=registry,
)

inbox_api_request_seconds = Histogram(
    "inbox_api_request_seconds",
    "Seconds for one inbox REST API request, by route.",
    labelnames=["route"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=registry,
)

staff_messages_total = Counter(
    "staff_messages_total",
    "Staff replies written (messages with sent_by='staff', direction='out').",
    registry=registry,
)

state_transitions_total = Counter(
    "state_transitions_total",
    "Inbox conversation state transitions, by action and result.",
    labelnames=["action", "result"],
    registry=registry,
)

inbox_events_written_total = Counter(
    "inbox_events_written_total",
    "inbox_events rows written, by event type.",
    labelnames=["type"],
    registry=registry,
)

idempotency_hits_total = Counter(
    "idempotency_hits_total",
    "Idempotency-Key resolutions, by result (replay|conflict).",
    labelnames=["result"],
    registry=registry,
)

# --- P1.3b WebSocket metrics (PROMPT §4.7) ------------------------------------

ws_connections = Gauge(
    "ws_connections",
    "Currently-open WebSocket connections in this api process.",
    registry=registry,
)

ws_connects_total = Counter(
    "ws_connects_total",
    "WebSocket connections accepted (past ticket + limit checks).",
    registry=registry,
)

ws_disconnects_total = Counter(
    "ws_disconnects_total",
    "WebSocket disconnections, by reason (normal|pong_timeout|error|policy|frame_too_large|rate_limit).",
    labelnames=["reason"],
    registry=registry,
)

ws_rejected_total = Counter(
    "ws_rejected_total",
    "WebSocket connections rejected, by reason (ticket_reused|ticket_invalid|per_staff|per_tenant|global|subscribe_attempt).",
    labelnames=["reason"],
    registry=registry,
)

ws_frames_sent_total = Counter(
    "ws_frames_sent_total",
    "WebSocket frames sent, by frame type (hello|event|ping|resync_required).",
    labelnames=["type"],
    registry=registry,
)

ws_pong_timeouts_total = Counter(
    "ws_pong_timeouts_total",
    "WebSocket connections closed because no pong arrived within the heartbeat window.",
    registry=registry,
)

ws_resync_events_total = Counter(
    "ws_resync_events_total",
    "inbox_events delivered via the resync path (not the live broadcast).",
    registry=registry,
)

ws_publish_failures_total = Counter(
    "ws_publish_failures_total",
    "redis-cache Pub/Sub publishes that failed (best-effort; the event is still committed).",
    registry=registry,
)

inbox_events_deleted_total = Counter(
    "inbox_events_deleted_total",
    "inbox_events rows deleted by the retention sweep.",
    registry=registry,
)

inbox_events_retention_last_run_timestamp = Gauge(
    "inbox_events_retention_last_run_timestamp",
    "Unix timestamp of the last completed retention sweep.",
    registry=registry,
)

# --- P1.4 catalog metrics (PROMPT §7) -----------------------------------------

catalog_events_total = Counter(
    "catalog_events_total",
    "Platform catalog events ingested, by type and outcome (applied|stale|dropped|unknown_type).",
    labelnames=["type", "outcome"],
    registry=registry,
)

catalog_dropped_fields_total = Counter(
    "catalog_dropped_fields_total",
    "Fields dropped at the catalog gate because they are outside the per-type allowlist (H36).",
    registry=registry,
)

catalog_cost_field_seen_total = Counter(
    "catalog_cost_field_seen_total",
    "Cost/wholesale/margin fields seen at the catalog gate (a platform-side leak worth flagging; H36).",
    registry=registry,
)

catalog_webhook_requests_total = Counter(
    "catalog_webhook_requests_total",
    "POST /webhooks/platform/catalog requests, by HTTP status.",
    labelnames=["status"],
    registry=registry,
)

catalog_reconcile_runs_total = Counter(
    "catalog_reconcile_runs_total",
    "Catalog reconciliation runs, by result (ok|failed|partial).",
    labelnames=["result"],
    registry=registry,
)

catalog_reconcile_duration_seconds = Histogram(
    "catalog_reconcile_duration_seconds",
    "Seconds for one full catalog reconciliation cycle.",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=registry,
)

catalog_staleness_seconds = Gauge(
    "catalog_staleness_seconds",
    "Seconds since the most-stale tenant's last successful catalog reconciliation.",
    registry=registry,
)

search_queries_total = Counter(
    "search_queries_total",
    "Catalog search queries, by source (fts|trgm).",
    labelnames=["source"],
    registry=registry,
)

search_duration_seconds = Histogram(
    "search_duration_seconds",
    "Seconds for one catalog search (search_products / kb_search).",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=registry,
)

search_empty_results_total = Counter(
    "search_empty_results_total",
    "Catalog searches that returned zero results.",
    registry=registry,
)

# --- P1.5 LLM metrics (PROMPT §5.8) ------------------------------------------

llm_calls_total = Counter(
    "llm_calls_total",
    "LLM calls, by purpose, provider, and status (ok|error|timeout|breaker_open).",
    labelnames=["purpose", "provider", "status"],
    registry=registry,
)

llm_tokens_total = Counter(
    "llm_tokens_total",
    "LLM tokens consumed, by direction (input|output).",
    labelnames=["direction"],
    registry=registry,
)

llm_cost_micro_usd_total = Counter(
    "llm_cost_micro_usd_total",
    "LLM spend in micro-USD.",
    registry=registry,
)

llm_latency_seconds = Histogram(
    "llm_latency_seconds",
    "LLM call latency in seconds, by purpose.",
    labelnames=["purpose"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=registry,
)

llm_breaker_state = Gauge(
    "llm_breaker_state",
    "LLM circuit-breaker state, by provider (0=closed, 1=open, 2=half_open).",
    labelnames=["provider"],
    registry=registry,
)

budget_state = Gauge(
    "budget_state",
    "Number of tenants in each budget state (ok|warn_80|degraded|exhausted).",
    labelnames=["state"],
    registry=registry,
)

router_intents_total = Counter(
    "router_intents_total",
    "Router intents, by intent (product_search|policy_question|handoff_request|other).",
    labelnames=["intent"],
    registry=registry,
)

router_invalid_total = Counter(
    "router_invalid_total",
    "Router responses that failed strict schema validation (=> other).",
    registry=registry,
)

router_low_confidence_total = Counter(
    "router_low_confidence_total",
    "Router responses below the confidence threshold (=> other).",
    registry=registry,
)

compose_replies_total = Counter(
    "compose_replies_total",
    "Composed bot replies, by kind (template|product_list|policy_answer).",
    labelnames=["kind"],
    registry=registry,
)

turn_llm_skipped_total = Counter(
    "turn_llm_skipped_total",
    "Turns that skipped the router, by reason (budget_degraded|breaker_open).",
    labelnames=["reason"],
    registry=registry,
)

# --- P1.5b embedding + vector search metrics (PROMPT §5.7) ---------------------

embed_products_total = Counter(
    "embed_products_total",
    "Product embedding batch results, by result (ok|dim_mismatch|error|breaker_open).",
    labelnames=["result"],
    registry=registry,
)

embed_batch_duration_seconds = Histogram(
    "embed_batch_duration_seconds",
    "Seconds for one product-embedding provider call.",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=registry,
)

embed_dim_mismatch_total = Counter(
    "embed_dim_mismatch_total",
    "Provider vectors rejected because their length != EMBEDDING_DIM (H44 contract).",
    registry=registry,
)

embed_queue_depth = Gauge(
    "embed_queue_depth",
    "Number of products waiting for an embedding this cycle.",
    registry=registry,
)

search_vector_skipped_total = Counter(
    "search_vector_skipped_total",
    "Searches that skipped the vector source, by reason "
    "(breaker_open|budget_degraded|timeout|error|dim_mismatch).",
    labelnames=["reason"],
    registry=registry,
)

search_sources_used_total = Counter(
    "search_sources_used_total",
    "Search source-count histogram, by number of merged lists (2|3).",
    labelnames=["count"],
    registry=registry,
)

embed_query_cache_total = Counter(
    "embed_query_cache_total",
    "Query-embedding cache outcomes, by result (hit|miss|set).",
    labelnames=["result"],
    registry=registry,
)

# --- P1.5b rolling summary metrics (PROMPT §5.7) ------------------------------

summary_runs_total = Counter(
    "summary_runs_total",
    "Summary worker cycles, by result (ok|error).",
    labelnames=["result"],
    registry=registry,
)

summary_skipped_total = Counter(
    "summary_skipped_total",
    "Conversations skipped for a summary, by reason "
    "(below_trigger|min_between|daily_cap|budget_degraded|breaker_open|no_messages|error).",
    labelnames=["reason"],
    registry=registry,
)

summary_output_chars = Histogram(
    "summary_output_chars",
    "Stored rolling-summary length in characters (post-truncation).",
    buckets=(0, 100, 200, 400, 800, 1200, 1600, 2000, 4000),
    registry=registry,
)

# --- P1.6 output verifier metrics (PROMPT §7) ---------------------------------

verify_checks_total = Counter(
    "verify_checks_total",
    "Verifier checks, by result (pass|violation).",
    labelnames=["result"],
    registry=registry,
)

verify_violations_total = Counter(
    "verify_violations_total",
    "Verifier violations, by rule_id (closed list: empty|oversize|control_chars|placeholder|profanity|competitor|disclosure|verifier_error).",
    labelnames=["rule_id"],
    registry=registry,
)

verify_errors_total = Counter(
    "verify_errors_total",
    "Verifier internal errors (check_text raised) - each means a customer got a safe template instead of a real reply (H47 fail-closed).",
    registry=registry,
)

verify_duration_seconds = Histogram(
    "verify_duration_seconds",
    "Seconds for one insert_verified_outbox call.",
    buckets=(0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0),
    registry=registry,
)

verify_blocklist_phrases = Gauge(
    "verify_blocklist_phrases",
    "Number of active blocklist phrases per category (profanity|competitor|disclosure) - set at boot.",
    labelnames=["category"],
    registry=registry,
)

verify_enabled = Gauge(
    "verify_enabled",
    "1 when the output verifier is enabled, 0 when disabled (H4: disable is visible, not hidden).",
    registry=registry,
)

verify_safe_template_sent_total = Counter(
    "verify_safe_template_sent_total",
    "Safe-fallback templates sent after a verifier violation.",
    registry=registry,
)

# --- P1.7 order-tracking metrics (PROMPT §8) ---------------------------------

tool_calls_total = Counter(
    "tool_calls_total",
    "Tool calls, by tool and result (card|unverified|need_order_ref|need_phone|unavailable|blocked).",
    labelnames=["tool", "result"],
    registry=registry,
)

tools_enabled = Gauge(
    "tools_enabled",
    "1 when the tools layer is enabled, 0 when disabled (H4: visible, not hidden).",
    registry=registry,
)

order_lookup_total = Counter(
    "order_lookup_total",
    "Order-tracking attempts, by path (same_number|other_number) and outcome (allowed|denied|blocked).",
    labelnames=["path", "outcome"],
    registry=registry,
)

order_lookup_duration_seconds = Histogram(
    "order_lookup_duration_seconds",
    "Seconds for one order lookup platform call.",
    buckets=(0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 2.5, 5.0),
    registry=registry,
)

order_lookup_platform_errors_total = Counter(
    "order_lookup_platform_errors_total",
    "Order-lookup platform errors, by kind (timeout|unavailable|client_error).",
    labelnames=["kind"],
    registry=registry,
)

# --- P1.8 console rate limiting (H59) -----------------------------------------

rate_limit_hits_total = Counter(
    "rate_limit_hits_total",
    "Console requests rejected by the rate limiter, by group (read|write|ticket).",
    labelnames=["group"],
    registry=registry,
)

rate_limit_fail_open_total = Counter(
    "rate_limit_fail_open_total",
    "Console requests that PASSED because redis-cache was unavailable (fail-open, "
    "H59: a down cache must not take the console down), by group.",
    labelnames=["group"],
    registry=registry,
)

# --- P2.2 address resolver metrics (H67: no address/coords in labels) ---------
address_resolutions_total = Counter(
    "address_resolutions_total",
    "Address resolutions, by decision (accepted|confirm_with_customer|disambiguate|ask_for_pin|rejected).",
    labelnames=["decision"],
    registry=registry,
)


# --- P2.3 back-in-stock metrics (PROMPT §7) -----------------------------------
# action is a bounded enum: joined|duplicate|no_variant / held|expired|converted|released.

waitlist_entries_total = Counter(
    "waitlist_entries_total",
    "Waitlist writes, by action (joined|duplicate|no_variant|cap).",
    labelnames=["action"],
    registry=registry,
)

stock_holds_total = Counter(
    "stock_holds_total",
    "Stock-hold transitions, by action (held|expired|converted|released).",
    labelnames=["action"],
    registry=registry,
)

stock_allocation_duration_seconds = Histogram(
    "stock_allocation_duration_seconds",
    "Seconds for one app.allocate_stock_holds call (the short allocation transaction).",
    buckets=(0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0),
    registry=registry,
)

stock_observation_stale_total = Counter(
    "stock_observation_stale_total",
    "Sweep observations skipped because they are older than STOCK_OBSERVATION_MAX_AGE_S (H73).",
    registry=registry,
)

stock_sweep_runs_total = Counter(
    "stock_sweep_runs_total",
    "Back-in-stock sweep cycles.",
    registry=registry,
)



