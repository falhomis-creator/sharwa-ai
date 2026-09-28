"""core/app/workers/config.py - WorkerSettings (P1.0.3).

A frozen dataclass in the exact style of app/config.py (same `_required`/
`_optional`/`_int` helpers, same fail-fast posture) but loaded ONLY from the
worker entry point - never from app.config.Settings, and never imported by
`api`. This is critical: the `api` service in docker-compose.yml does not pass
any REDIS_DURABLE_* variables, so adding a required redis-durable field to
`Settings` would stop `api` from booting (P1.0.3's own warning).

Every tunable below has a documented default (H4: no unbounded buffer/queue),
and the one hard invariant - CORE_DB_POOL_MAX >= INGEST_SHARDS + 1 - is
enforced here with a ConfigError so the worker refuses to start rather than
run a shard without a pool connection.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from app.config import ConfigError, DatabaseConfig


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is required and must be non-empty (see .env)")
    return value


def _optional(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _csv(name: str, default: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        raw = default
    parts = [p.strip() for p in raw.split(",")]
    return tuple(p for p in parts if p)


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be a boolean, got {raw!r}")


# P1.1.5 default opt-out phrases (overridable without a code deploy, H4/OQ-P1-05).
DEFAULT_OPTOUT_AR = (
    "إيقاف",
    "ايقاف",
    "توقف",
    "الغاء الاشتراك",
    "إلغاء الاشتراك",
    "لا اريد",
    "لا أريد",
    "لا ترسل",
    "اوقف الرسائل",
)
DEFAULT_OPTOUT_EN = ("stop", "unsubscribe", "opt out", "optout")

# D6: deterministic "hand me to a human" phrases (same whole/beginning match,
# H25 - no classifier). customer_requested is reserved for an ACTUAL match.
DEFAULT_HANDOFF_AR = ("موظف", "شخص حقيقي", "بشري", "ممثل", "خدمة العملاء", "حولني", "انسان", "إنسان")
DEFAULT_HANDOFF_EN = ("human", "agent", "representative", "person", "talk to a human")

# P1.6 output-verifier default blocklists (owner policy data, overridable via env
# without a code deploy - H4). Conservative defaults; the owner's review of these
# lists is an open item (docs/P1_DEVIATIONS.md / PROMPT_P1_06 §12 OQ-P1-14).
DEFAULT_VERIFY_PROFANITY_AR = ("تبا", "لعنة", "سافل", "حقير")
DEFAULT_VERIFY_PROFANITY_EN = ("fuck", "shit", "bitch", "asshole", "bastard")
DEFAULT_VERIFY_COMPETITORS = ("salla", "zid", "shopify", "woocommerce", "bigcommerce", "squarespace")
# disclosure = machine-revealing vocabulary + provider names. The provider names
# belong here by necessity (S8 rule 3 forbids them outside app/llm/adapters/ and
# the config files - and this file IS the exempted worker config).
DEFAULT_VERIFY_DISCLOSURE = (
    "بوت", "ذكاء اصطناعي", "روبوت", "نموذج لغوي", "chatbot",
    "deepseek", "gpt", "claude", "openai", "anthropic",
)

# P1.7 order tracking: a reasonable order-reference pattern (a 4-8 digit token).
# The real sharwa_saas format is UNCONFIRMED (OQ-P1-24) - this is a placeholder,
# compiled at load and overridable via ORDER_REF_PATTERN.
DEFAULT_ORDER_REF_PATTERN = r"\b\d{4,8}\b"
DEFAULT_COUNTRY_CODE = "967"

# ops/pgbouncer.ini default_pool_size (read in P1.0.1, not assumed). The
# worker's tenant pool goes THROUGH PgBouncer, so it must leave headroom for
# the `api` service's own tenant pool + PgBouncer's reserve_pool_size on the
# same default_pool_size budget (D1: 5.2GB is the memory cap, but the
# connection budget is 25 per (db,user) pair).
PGBOUNCER_DEFAULT_POOL_SIZE = 25

# P1.5 LLM price table default: micro-USD per 1000 tokens in/out. The fake
# provider is free (owner decision); a real provider's table is JSON-overridable
# without a code deploy.
DEFAULT_LLM_PRICE_TABLE: dict[str, Any] = {
    "fake": {"fake-router": {"input": 0, "output": 0}},
}

# P2.2 address resolver: written confidence weights (H65). JSON-overridable
# without a code deploy; these mirror app/geo/resolve.py's documented defaults.
DEFAULT_ADDRESS_W_LEVEL: dict[str, Any] = {
    "landmark": 1.00, "neighborhood": 0.95, "area": 0.90,
    "district": 0.85, "governorate": 0.50, "country": 0.30,
}
DEFAULT_ADDRESS_W_MATCH: dict[str, Any] = {"exact": 1.00, "synonym": 0.90, "prefix": 0.70}


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _json_table(name: str, default: dict[str, Any]) -> dict[str, Any]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{name} must be valid JSON, got {raw!r}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a JSON object")
    return value


def validate_llm_provider(env: str, provider: str) -> None:
    """M13: a fake provider must never reach a real customer (the same H-safety
    posture as issue-dev-token refusing under ENV=production)."""
    if env == "production" and provider == "fake":
        raise ConfigError("LLM_PROVIDER=fake is forbidden when ENV=production")


def validate_embedding_provider(env: str, provider: str) -> None:
    """P1.5b V1: the fake embedding provider is a test approximation, so it must
    never run against real customers either (same H-safety as the fake LLM)."""
    if env == "production" and provider == "fake":
        raise ConfigError("EMBEDDING_PROVIDER=fake is forbidden when ENV=production")


# H44: the embedding column is vector(1024) - a written contract with the
# database. The config dimension must equal it, and the provider output must be
# checked to that same width before any write (repos_catalog / embed.py).
EMBEDDING_DIM_CONTRACT = 1024


def validate_embedding_dim(dim: int) -> None:
    """H44: any EMBEDDING_DIM != 1024 refuses to boot (a different width means a
    column migration decision, never a runtime surprise)."""
    if dim != EMBEDDING_DIM_CONTRACT:
        raise ConfigError(
            f"EMBEDDING_DIM must equal {EMBEDDING_DIM_CONTRACT} "
            f"(the vector column width), got {dim}"
        )


def validate_safe_template_id(template_id: str) -> None:
    """P1.6 §6: VERIFY_SAFE_TEMPLATE_ID must be a member of the CLOSED
    templates.SAFE_FALLBACK_TEMPLATES list, else ConfigError at load."""
    from app.workers import templates
    if template_id not in templates.SAFE_FALLBACK_TEMPLATES:
        raise ConfigError(
            f"VERIFY_SAFE_TEMPLATE_ID ({template_id!r}) must be one of "
            f"{sorted(templates.SAFE_FALLBACK_TEMPLATES)}"
        )


@dataclass(frozen=True)
class WorkerSettings:
    db: DatabaseConfig
    system_pool_max: int
    core_turn_workers: int
    core_dispatch_interval_ms: int
    core_dispatch_batch: int
    core_dispatch_lease_s: int
    core_dispatch_max_attempts: int
    core_max_consecutive_bot_replies: int
    core_evt_group: str
    core_evt_dlq_stream: str
    gateway_base_url: str
    gateway_api_key: str
    redis_durable_host: str
    redis_durable_port: int
    redis_durable_password: str
    redis_durable_timeout_ms: int
    redis_cache_host: str
    redis_cache_port: int
    redis_cache_password: str
    inbox_events_retention_days: int
    inbox_events_delete_batch: int
    inbox_events_delete_max_batches: int
    inbox_events_retention_interval_s: int
    ingest_shards: int
    core_ingest_group: str
    core_ingest_batch: int
    core_ingest_block_ms: int
    core_ingest_claim_idle_ms: int
    core_ingest_max_attempts: int
    core_ingest_dlq_stream: str
    core_ingest_max_body_chars: int
    core_ingest_shutdown_timeout_s: int
    core_worker_metrics_port: int
    core_optout_phrases_ar: tuple[str, ...]
    core_optout_phrases_en: tuple[str, ...]
    core_handoff_phrases_ar: tuple[str, ...]
    core_handoff_phrases_en: tuple[str, ...]
    dedupe_done_ttl_s: int
    metrics_token: str
    env: str
    # P1.4 catalog reconciliation (PROMPT §5.4/§5.5). commerce_base_url is OPTIONAL
    # (empty => the worker boots but skips catalog reconciliation with a warning);
    # outbound commerce is not an H5 "refuse-boot" secret and the platform is not
    # implemented yet.
    commerce_base_url: str = ""
    commerce_timeout_s: float = 3.0
    catalog_reconcile_interval_s: int = 900
    catalog_reconcile_max_tenants: int = 100
    catalog_reconcile_max_events_per_tenant: int = 500
    # P1.5 LLM layer (PROMPT §5). All defaults are written (H4).
    llm_provider: str = "fake"
    llm_timeout_s: float = 8.0
    llm_breaker_fail_threshold: int = 5
    llm_breaker_reset_s: float = 60.0
    tenant_monthly_budget_micro_usd: int = 20_000_000
    llm_price_table: dict[str, Any] = field(default_factory=dict)
    llm_router_history: int = 6
    llm_router_max_input_chars: int = 2000
    llm_router_min_confidence: float = 0.6
    llm_router_max_output_tokens: int = 64
    # P1.5b embedding + vector search (PROMPT §5.1-§5.3). All defaults written (H4).
    embedding_provider: str = "fake"
    embedding_dim: int = 1024
    embed_interval_s: int = 300
    embed_batch: int = 32
    embed_max_products_per_cycle: int = 200
    embed_max_text_chars: int = 2000
    embed_timeout_s: float = 8.0
    embed_query_cache_ttl_s: int = 3600
    # P1.5b rolling summary (PROMPT §5.4). All ceilings are written and enforced
    # in code (V9). summary_interval_s is the worker thread's wake interval.
    summary_interval_s: int = 120
    summary_max_per_cycle: int = 50
    summary_trigger_messages: int = 12
    summary_input_messages: int = 20
    summary_input_max_chars: int = 6000
    summary_max_output_tokens: int = 400
    summary_max_chars: int = 1600
    summary_min_messages_between: int = 10
    summary_max_per_conversation_per_day: int = 6
    # P1.6 output verifier (PROMPT §6). All defaults written (H4).
    verify_enabled: bool = True
    verify_max_chars: int = 4000
    verify_excerpt_max_chars: int = 200
    verify_safe_template_id: str = "handoff_notice"
    verify_profanity_ar: tuple[str, ...] = DEFAULT_VERIFY_PROFANITY_AR
    verify_profanity_en: tuple[str, ...] = DEFAULT_VERIFY_PROFANITY_EN
    verify_competitors: tuple[str, ...] = DEFAULT_VERIFY_COMPETITORS
    verify_disclosure: tuple[str, ...] = DEFAULT_VERIFY_DISCLOSURE
    # P1.7 §0.2: the space-evasion join-window cap (written cap, H4).
    verify_join_window_max: int = 6
    # P1.7 order tracking (PROMPT §7). All defaults written (H4) except the HMAC
    # key (order_ref_hash_key), which is _required at load (H5/H54).
    tools_enabled: bool = True
    order_ref_pattern: Any = None  # compiled at load (re.Pattern) - no static default
    order_lookup_timeout_s: float = 4.0
    order_lookup_max_phone_candidates: int = 3
    order_lookup_max_per_conversation_per_day: int = 10
    order_status_max_chars: int = 600
    default_country_code: str = DEFAULT_COUNTRY_CODE
    # F-P1-10: the Yemeni national mobile format (9 digits starting with 7, no
    # leading zero) - written, overridable settings, not hardcoded in extract.py.
    national_number_len: int = 9
    mobile_prefixes: tuple[str, ...] = ("7",)
    order_ref_hash_key: str = ""
    # P2.2 address resolver (H64-H68). All defaults written (H4); the weight
    # tables are JSON-overridable without a code deploy.
    address_accept_threshold: float = 0.75
    address_max_candidates: int = 3
    address_parent_bonus: float = 1.05
    address_w_level: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_ADDRESS_W_LEVEL))
    address_w_match: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_ADDRESS_W_MATCH))

    @staticmethod
    def load() -> WorkerSettings:
        ingest_shards = _int("INGEST_SHARDS", 4)
        if ingest_shards < 1:
            raise ConfigError("INGEST_SHARDS must be >= 1")
        turn_workers = _int("CORE_TURN_WORKERS", 2)
        if turn_workers < 1:
            raise ConfigError("CORE_TURN_WORKERS must be >= 1")

        # P1.2 (§6.1): the process now runs ingest (INGEST_SHARDS) + turn
        # (CORE_TURN_WORKERS) + evt (INGEST_SHARDS) thread groups; each holds at
        # most one tenant connection during its transaction, +2 headroom.
        tenant_min = ingest_shards + turn_workers + ingest_shards + 2
        pool_min = _int("CORE_DB_POOL_MIN", 2)
        # Default raised from 8 to 16 for the P1.2 thread groups (2*INGEST_SHARDS
        # + CORE_TURN_WORKERS + 2 = 12 minimum at defaults), still < 25.
        pool_max = _int("CORE_DB_POOL_MAX", 16)
        if pool_max < tenant_min:
            raise ConfigError(
                f"CORE_DB_POOL_MAX ({pool_max}) must be >= INGEST_SHARDS + "
                f"CORE_TURN_WORKERS + INGEST_SHARDS + 2 ({tenant_min})"
            )
        if pool_max >= PGBOUNCER_DEFAULT_POOL_SIZE:
            raise ConfigError(
                f"CORE_DB_POOL_MAX ({pool_max}) must be < pgbouncer "
                f"default_pool_size ({PGBOUNCER_DEFAULT_POOL_SIZE}) to leave "
                "headroom for the api service on the same pool"
            )
        # System pool: the dispatcher also calls app.claim_outbox via system_tx.
        system_pool_max = _int("CORE_SYSTEM_POOL_MAX", tenant_min + 2)
        if system_pool_max < tenant_min + 2:
            raise ConfigError(
                f"CORE_SYSTEM_POOL_MAX ({system_pool_max}) must be >= "
                f"tenant_min + 2 ({tenant_min + 2})"
            )

        db = DatabaseConfig(
            dsn=_required("CORE_DATABASE_URL"),
            system_dsn=_required("CORE_SYSTEM_DATABASE_URL"),
            pool_min=pool_min,
            pool_max=pool_max,
        )

        block_ms = _int("CORE_INGEST_BLOCK_MS", 3000)
        timeout_ms = _int("REDIS_DURABLE_TIMEOUT_MS", 5000)
        # F-P0 lesson (forwarder.js BLOCK_MS comment): the server-side BLOCK must
        # stay below the client-side command timeout by a safety margin, or an
        # idle stream races the watchdog into a noisy poll loop.
        if block_ms >= timeout_ms:
            raise ConfigError(
                f"CORE_INGEST_BLOCK_MS ({block_ms}) must be < "
                f"REDIS_DURABLE_TIMEOUT_MS ({timeout_ms}) by a safety margin"
            )

        # _required() already refuses an empty value (H5), so no second check
        # is needed here - a non-empty METRICS_TOKEN is guaranteed.
        metrics_token = _required("METRICS_TOKEN")

        env = _optional("ENV", "production")
        llm_provider = _optional("LLM_PROVIDER", "fake")
        validate_llm_provider(env, llm_provider)

        embedding_provider = _optional("EMBEDDING_PROVIDER", "fake")
        validate_embedding_provider(env, embedding_provider)
        embedding_dim = _int("EMBEDDING_DIM", 1024)
        validate_embedding_dim(embedding_dim)

        verify_safe_template_id = _optional("VERIFY_SAFE_TEMPLATE_ID", "handoff_notice")
        validate_safe_template_id(verify_safe_template_id)

        order_ref_pattern_src = _optional("ORDER_REF_PATTERN", DEFAULT_ORDER_REF_PATTERN)
        try:
            order_ref_pattern = re.compile(order_ref_pattern_src)
        except re.error as exc:
            raise ConfigError(f"ORDER_REF_PATTERN does not compile: {exc}") from exc
        order_ref_hash_key = _required("ORDER_REF_HASH_KEY")

        address_w_level = _json_table("ADDRESS_W_LEVEL", DEFAULT_ADDRESS_W_LEVEL)
        address_w_match = _json_table("ADDRESS_W_MATCH", DEFAULT_ADDRESS_W_MATCH)

        return WorkerSettings(
            db=db,
            system_pool_max=system_pool_max,
            core_turn_workers=turn_workers,
            core_dispatch_interval_ms=_int("CORE_DISPATCH_INTERVAL_MS", 500),
            core_dispatch_batch=_int("CORE_DISPATCH_BATCH", 20),
            core_dispatch_lease_s=_int("CORE_DISPATCH_LEASE_S", 60),
            core_dispatch_max_attempts=_int("CORE_DISPATCH_MAX_ATTEMPTS", 8),
            core_max_consecutive_bot_replies=_int("CORE_MAX_CONSECUTIVE_BOT_REPLIES", 8),
            core_evt_group=_optional("CORE_EVT_GROUP", "ai-core-evt"),
            core_evt_dlq_stream=_optional("CORE_EVT_DLQ_STREAM", "dlq:evt:core"),
            gateway_base_url=_required("GATEWAY_BASE_URL"),
            gateway_api_key=_required("GATEWAY_API_KEY"),
            redis_durable_host=_required("REDIS_DURABLE_HOST"),
            redis_durable_port=_int("REDIS_DURABLE_PORT", 6379),
            redis_durable_password=_required("REDIS_DURABLE_PASSWORD"),
            redis_durable_timeout_ms=timeout_ms,
            redis_cache_host=_required("REDIS_CACHE_HOST"),
            redis_cache_port=_int("REDIS_CACHE_PORT", 6379),
            redis_cache_password=_required("REDIS_CACHE_PASSWORD"),
            inbox_events_retention_days=_int("INBOX_EVENTS_RETENTION_DAYS", 7),
            inbox_events_delete_batch=_int("INBOX_EVENTS_DELETE_BATCH", 5000),
            inbox_events_delete_max_batches=_int("INBOX_EVENTS_DELETE_MAX_BATCHES", 20),
            inbox_events_retention_interval_s=_int("INBOX_EVENTS_RETENTION_INTERVAL_S", 3600),
            ingest_shards=ingest_shards,
            core_ingest_group=_optional("CORE_INGEST_GROUP", "ai-core-ingest"),
            core_ingest_batch=_int("CORE_INGEST_BATCH", 50),
            core_ingest_block_ms=block_ms,
            core_ingest_claim_idle_ms=_int("CORE_INGEST_CLAIM_IDLE_MS", 30000),
            core_ingest_max_attempts=_int("CORE_INGEST_MAX_ATTEMPTS", 10),
            core_ingest_dlq_stream=_optional("CORE_INGEST_DLQ_STREAM", "dlq:in:core"),
            core_ingest_max_body_chars=_int("CORE_INGEST_MAX_BODY_CHARS", 65536),
            core_ingest_shutdown_timeout_s=_int("CORE_INGEST_SHUTDOWN_TIMEOUT_S", 20),
            core_worker_metrics_port=_int("CORE_WORKER_METRICS_PORT", 4003),
            core_optout_phrases_ar=_csv(
                "CORE_OPTOUT_PHRASES_AR", ",".join(DEFAULT_OPTOUT_AR)
            ),
            core_optout_phrases_en=_csv(
                "CORE_OPTOUT_PHRASES_EN", ",".join(DEFAULT_OPTOUT_EN)
            ),
            core_handoff_phrases_ar=_csv(
                "CORE_HANDOFF_PHRASES_AR", ",".join(DEFAULT_HANDOFF_AR)
            ),
            core_handoff_phrases_en=_csv(
                "CORE_HANDOFF_PHRASES_EN", ",".join(DEFAULT_HANDOFF_EN)
            ),
            dedupe_done_ttl_s=_int("DEDUPE_DONE_TTL_S", 172800),
            metrics_token=metrics_token,
            commerce_base_url=_optional("COMMERCE_BASE_URL", ""),
            commerce_timeout_s=float(_optional("COMMERCE_TIMEOUT_S", "3.0")),
            catalog_reconcile_interval_s=_int("CATALOG_RECONCILE_INTERVAL_S", 900),
            catalog_reconcile_max_tenants=_int("CATALOG_RECONCILE_MAX_TENANTS", 100),
            catalog_reconcile_max_events_per_tenant=_int("CATALOG_RECONCILE_MAX_EVENTS_PER_TENANT", 500),
            env=env,
            llm_provider=llm_provider,
            llm_timeout_s=_float("LLM_TIMEOUT_S", 8.0),
            llm_breaker_fail_threshold=_int("LLM_BREAKER_FAIL_THRESHOLD", 5),
            llm_breaker_reset_s=_float("LLM_BREAKER_RESET_S", 60.0),
            tenant_monthly_budget_micro_usd=_int("TENANT_MONTHLY_BUDGET_MICRO_USD", 20_000_000),
            llm_price_table=_json_table("LLM_PRICE_TABLE", DEFAULT_LLM_PRICE_TABLE),
            llm_router_history=_int("LLM_ROUTER_HISTORY", 6),
            llm_router_max_input_chars=_int("LLM_ROUTER_MAX_INPUT_CHARS", 2000),
            llm_router_min_confidence=_float("LLM_ROUTER_MIN_CONFIDENCE", 0.6),
            llm_router_max_output_tokens=_int("LLM_ROUTER_MAX_OUTPUT_TOKENS", 64),
            embedding_provider=embedding_provider,
            embedding_dim=embedding_dim,
            embed_interval_s=_int("EMBED_INTERVAL_S", 300),
            embed_batch=_int("EMBED_BATCH", 32),
            embed_max_products_per_cycle=_int("EMBED_MAX_PRODUCTS_PER_CYCLE", 200),
            embed_max_text_chars=_int("EMBED_MAX_TEXT_CHARS", 2000),
            embed_timeout_s=_float("EMBED_TIMEOUT_S", 8.0),
            embed_query_cache_ttl_s=_int("EMBED_QUERY_CACHE_TTL_S", 3600),
            summary_interval_s=_int("SUMMARY_INTERVAL_S", 120),
            summary_max_per_cycle=_int("SUMMARY_MAX_PER_CYCLE", 50),
            summary_trigger_messages=_int("SUMMARY_TRIGGER_MESSAGES", 12),
            summary_input_messages=_int("SUMMARY_INPUT_MESSAGES", 20),
            summary_input_max_chars=_int("SUMMARY_INPUT_MAX_CHARS", 6000),
            summary_max_output_tokens=_int("SUMMARY_MAX_OUTPUT_TOKENS", 400),
            summary_max_chars=_int("SUMMARY_MAX_CHARS", 1600),
            summary_min_messages_between=_int("SUMMARY_MIN_MESSAGES_BETWEEN", 10),
            summary_max_per_conversation_per_day=_int("SUMMARY_MAX_PER_CONVERSATION_PER_DAY", 6),
            verify_enabled=_bool("VERIFY_ENABLED", True),
            verify_max_chars=_int("VERIFY_MAX_CHARS", 4000),
            verify_excerpt_max_chars=_int("VERIFY_EXCERPT_MAX_CHARS", 200),
            verify_safe_template_id=verify_safe_template_id,
            verify_profanity_ar=_csv("VERIFY_PROFANITY_AR", ",".join(DEFAULT_VERIFY_PROFANITY_AR)),
            verify_profanity_en=_csv("VERIFY_PROFANITY_EN", ",".join(DEFAULT_VERIFY_PROFANITY_EN)),
            verify_competitors=_csv("VERIFY_COMPETITORS", ",".join(DEFAULT_VERIFY_COMPETITORS)),
            verify_disclosure=_csv("VERIFY_DISCLOSURE", ",".join(DEFAULT_VERIFY_DISCLOSURE)),
            verify_join_window_max=_int("VERIFY_JOIN_WINDOW_MAX", 6),
            tools_enabled=_bool("TOOLS_ENABLED", True),
            order_ref_pattern=order_ref_pattern,
            order_lookup_timeout_s=_float("ORDER_LOOKUP_TIMEOUT_S", 4.0),
            order_lookup_max_phone_candidates=_int("ORDER_LOOKUP_MAX_PHONE_CANDIDATES", 3),
            order_lookup_max_per_conversation_per_day=_int("ORDER_LOOKUP_MAX_PER_CONVERSATION_PER_DAY", 10),
            order_status_max_chars=_int("ORDER_STATUS_MAX_CHARS", 600),
            default_country_code=_optional("DEFAULT_COUNTRY_CODE", DEFAULT_COUNTRY_CODE),
            national_number_len=_int("NATIONAL_NUMBER_LEN", 9),
            mobile_prefixes=_csv("MOBILE_PREFIXES", "7"),
            order_ref_hash_key=order_ref_hash_key,
            address_accept_threshold=_float("ADDRESS_ACCEPT_THRESHOLD", 0.75),
            address_max_candidates=_int("ADDRESS_MAX_CANDIDATES", 3),
            address_parent_bonus=_float("ADDRESS_PARENT_BONUS", 1.05),
            address_w_level=address_w_level,
            address_w_match=address_w_match,
        )
