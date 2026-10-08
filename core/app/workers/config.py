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
from app.policy.types import TemplateMeta


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

# P3.3 (H96): default opt-IN phrases. FULL-EQUALITY match only (optout.detect_optin)
# - a "نعم", a purchase, silence, or a waitlist join is NEVER a marketing
# consent. FORBIDDEN here: "نعم"/"موافق"/"ok"/"yes" (H96, literal). Env-tunable
# without a code deploy like STOP (OQ-P3-13: the owner reviews these lists).
DEFAULT_OPTIN_AR = (
    "اشتراك",
    "اشترك",
    "اشتراك في الرسائل",
    "فعل الرسائل",
    "فعّل الرسائل",
)
DEFAULT_OPTIN_EN = ("subscribe", "start", "opt in", "optin")

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
# P4.2: deepseek rates from the official pricing page (api-docs.deepseek.com,
# PEAK column - the higher one, so the tenant budget never under-counts):
# deepseek-flash and the deepseek-chat alias are $0.30/M in + $1.20/M out;
# deepseek-v4-pro is $1.32/M in + $3.96/M out. Per 1k tokens that is
# 0.3/1.2 and 1.32/3.96 micro-USD. 'local' embeddings are free (no entry).
DEFAULT_LLM_PRICE_TABLE: dict[str, Any] = {
    "fake": {"fake-router": {"input": 0, "output": 0}},
    "deepseek": {
        "deepseek-chat": {"input": 0.3, "output": 1.2},
        "deepseek-flash": {"input": 0.3, "output": 1.2},
        "deepseek-v4-pro": {"input": 1.32, "output": 3.96},
    },
}

# P2.2 address resolver: written confidence weights (H65). JSON-overridable
# without a code deploy; these mirror app/geo/resolve.py's documented defaults.
DEFAULT_ADDRESS_W_LEVEL: dict[str, Any] = {
    "landmark": 1.00, "neighborhood": 0.95, "area": 0.90,
    "district": 0.85, "governorate": 0.50, "country": 0.30,
}
DEFAULT_ADDRESS_W_MATCH: dict[str, Any] = {"exact": 1.00, "synonym": 0.90, "prefix": 0.70}

# H103 (P3.4): the opt-out footer shown under EVERY marketing message. PROPOSED
# text, pending the owner's approval (OQ-P3-15); MARKETING_FOOTER_AR overrides it
# and `marketing enable` requires that variable to be set explicitly.
DEFAULT_MARKETING_FOOTER_AR = "لإيقاف الرسائل الترويجية أرسل: إيقاف"

# P3.1 proactive template catalog (H77/H83): template_id -> (meta, text, merge_keys).
# H83: no proactive text without an approved template; the message_class is DERIVED
# here, never passed by the producer. P3.4 registers the first MARKETING template
# (cart_reminder) - but registration is NOT activation: a tenant gets marketing
# only through `marketing enable` (H100), so every tenant stays dark by default.
PROACTIVE_TEMPLATES: dict[str, tuple[TemplateMeta, str, tuple[str, ...]]] = {
    "stock_available": (
        TemplateMeta(
            template_id="stock_available", message_class="utility",
            consent_scope="back_in_stock", capability="back_in_stock",
            quiet_hours=False, footer_required=False,
        ),
        "عاد «title» للتوفر! سارع بالطلب الآن. 🛍️",
        ("title",),
    ),
    "stock_hold_expired": (
        TemplateMeta(
            template_id="stock_hold_expired", message_class="utility",
            consent_scope="back_in_stock", capability="back_in_stock",
            quiet_hours=False, footer_required=False,
        ),
        "انتهت مدة حجزك. ما زال بإمكانك الطلب من جديد متى شئت.",
        (),
    ),
    # P3.4 / OQ-P3-15 PROPOSED text (pending the owner's verbatim approval, H103).
    # The one merge key is built by cart_reminder.items_phrase (pure, OQ-P3-08).
    "cart_reminder": (
        TemplateMeta(
            template_id="cart_reminder", message_class="marketing",
            consent_scope="marketing", capability="marketing",
            quiet_hours=True, footer_required=True,
        ),
        "مرحباً، تركتَ في سلّتك «items_phrase». إذا أحببتَ إكمال طلبك فأخبرنا هنا.",
        ("items_phrase",),
    ),
}


def template_ids_for_scope(scope: str) -> tuple[str, ...]:
    """Scope -> its template_ids, derived from the catalog (never re-written)."""
    return tuple(
        tid for tid, (meta, _text, _keys) in PROACTIVE_TEMPLATES.items()
        if meta.consent_scope == scope
    )


def proactive_template(template_id: str) -> tuple[TemplateMeta, str, tuple[str, ...]] | None:
    return PROACTIVE_TEMPLATES.get(template_id)


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _gap_pair(src: str) -> tuple[int, int]:
    parts = src.split(",")
    if len(parts) != 2:
        raise ConfigError(f"gap must be 'min,max', got {src!r}")
    lo, hi = int(parts[0]), int(parts[1])
    if lo > hi:
        raise ConfigError(f"gap min ({lo}) must be <= max ({hi})")
    return lo, hi


def _ladder(src: str) -> dict[int, int]:
    out: dict[int, int] = {}
    for chunk in src.split(","):
        chunk = chunk.strip()
        if chunk:
            d, c = chunk.split(":", 1)
            out[int(d)] = int(c)
    if not out:
        raise ConfigError("warm-up ladder is empty")
    return out


def _hhmm(src: str) -> tuple[int, int]:
    try:
        h, m = src.split(":", 1)
        return int(h), int(m)
    except ValueError as exc:
        raise ConfigError(f"bad HH:MM {src!r}") from exc


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


# P4.2 (owner decision): LLM providers with a REAL adapter under
# app/llm/adapters/ - i.e. allowed to boot under ENV=production. 'fake' is not
# in this set by design (M13), and neither is any name without an adapter.
REAL_LLM_PROVIDERS = frozenset({"deepseek"})


def validate_llm_provider(env: str, provider: str) -> None:
    """M13: a fake provider must never reach a real customer (the same H-safety
    posture as issue-dev-token refusing under ENV=production).

    P4.2: the guard now RECOGNIZES the real provider - 'deepseek' (via
    app/llm/adapters/deepseek.py) boots the worker in production. Production
    also refuses any name outside REAL_LLM_PROVIDERS (a typo must stop the
    boot, not silently fall back); non-production envs keep 'fake' for
    dev/tests and pass unknown names through to the registry's own refusal."""
    if env == "production" and provider == "fake":
        raise ConfigError("LLM_PROVIDER=fake is forbidden when ENV=production")
    if env == "production" and provider not in REAL_LLM_PROVIDERS:
        raise ConfigError(
            f"LLM_PROVIDER={provider!r} is not a real provider; production allows "
            f"one of {sorted(REAL_LLM_PROVIDERS)} (M13/H-safety)"
        )


def validate_embedding_provider(env: str, provider: str) -> None:
    """P1.5b V1: the fake embedding provider is a test approximation, so it must
    never run against real customers either (same H-safety as the fake LLM).
    P4.2: 'local' (app/llm/adapters/local_embedding.py) is REAL local code -
    deterministic, no network, no cost - and is therefore allowed in
    production: DeepSeek exposes no embeddings endpoint, so it is the
    production-safe embedding until the owner picks a paid one."""
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
    core_optin_phrases_ar: tuple[str, ...]
    core_optin_phrases_en: tuple[str, ...]
    core_handoff_phrases_ar: tuple[str, ...]
    core_handoff_phrases_en: tuple[str, ...]
    dedupe_done_ttl_s: int
    metrics_token: str
    env: str
    # P1.4 catalog reconciliation (PROMPT §5.4/§5.5). commerce_base_url is OPTIONAL;
    # when set it requires commerce_api_secret (OQ-P4-03, H60).
    commerce_base_url: str = ""
    commerce_api_secret: str = ""
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
    # P4.2: the real provider's credentials (DeepSeek, OpenAI-compatible API).
    # Defaults are written (H4); the key is REQUIRED at load whenever a real
    # provider is selected (H5 - see REAL_LLM_PROVIDERS above). The base URL
    # and model are overridable per environment without a code deploy.
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
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
    # P4 Task 18b-2b: the size advisor in the conversation turn. DARK by default
    # (owner decision): off => the turn behaves byte-for-byte as before.
    size_advice_enabled: bool = False
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
    # P2.3 back-in-stock (PROMPT §7). All defaults written (H4). The per-customer
    # ceiling is enforced in code (not just documented), unlike a bare constant.
    stock_hold_ttl_s: int = 3600
    stock_observation_max_age_s: int = 300
    stock_sweep_interval_s: int = 60
    stock_max_variants_per_cycle: int = 50
    stock_max_waitlist_per_customer: int = 10
    # P3.1 send policy (PROMPT §4). Owner-approved drip numbers (OQ-P3-03). The
    # warm-up ladder is a dict day_index -> cap; the cap column is SQL-enforced.
    send_policy_warmup_ladder: dict[int, int] = field(default_factory=lambda: {0: 20, 3: 40, 7: 80, 14: 150, 30: 250})
    send_policy_daily_ceiling: int = 250
    send_policy_utility_daily_cap: int = 100
    send_policy_marketing_gap: tuple[int, int] = (20, 60)
    send_policy_utility_gap: tuple[int, int] = (8, 20)
    send_policy_throttle_cap_factor: float = 0.5
    send_policy_gap_factor: float = 2.0
    send_policy_throttle_cooldown_h: int = 24
    send_policy_marketing_per_24h: int = 1
    send_policy_marketing_per_7d: int = 2
    send_policy_utility_per_24h: int = 3
    send_policy_utility_min_gap_s: int = 60
    send_policy_active_chat_cooldown_s: int = 1800
    send_policy_quiet_start: str = "22:00"
    send_policy_quiet_end: str = "09:00"
    send_policy_marketing_ttl_h: int = 24
    send_policy_utility_ttl_h: int = 6
    send_policy_interaction_window_d: int = 180
    send_policy_marketing_claim_per_cycle: int = 4
    send_policy_sweep_interval_s: int = 60
    send_policy_idle_reset_d: int = 14
    send_policy_complaint_words: tuple[str, ...] = ("سبام", "ازعاج", "إزعاج", "بلاغ", "ابلاغ", "report", "spam")
    marketing_footer_ar: str = DEFAULT_MARKETING_FOOTER_AR
    # P3.4 (H100): `enable` refuses a number whose warm-up is younger than this
    # many days (the ladder's first rung is day 0 - too young to carry marketing).
    marketing_min_warmup_days: int = 3
    # P3.2 scheduler + cart events (PROMPT §4). Architect PROPOSALS pending the
    # owner's OQ-P3-06 approval - env-overridable settings, never hardcoded at
    # the call sites (directive rule 8).
    scheduler_poll_interval_s: int = 15
    scheduler_batch: int = 50
    scheduler_lease_s: int = 120
    scheduler_max_attempts: int = 5
    scheduler_backoff_base_s: int = 60
    scheduler_backoff_cap_s: int = 3600
    cart_reminder_delay_h: int = 24
    cart_reminder_max_late_h: int = 12
    carts_retention_d: int = 14
    cart_item_title_max: int = 60

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
        # P4.2 (H5): a real provider requires its secret AT BOOT - an empty key
        # refuses to start with a clear message instead of failing per-call
        # (or, worse, billing) in production. Checked in EVERY env so a dev box
        # selecting the real provider gets the same fail-fast.
        llm_api_key = _optional("DEEPSEEK_API_KEY", "")
        if llm_provider in REAL_LLM_PROVIDERS and not llm_api_key:
            raise ConfigError(
                f"DEEPSEEK_API_KEY is required when LLM_PROVIDER={llm_provider} (see .env)"
            )
        llm_base_url = _optional("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        llm_model = _optional("DEEPSEEK_MODEL", "deepseek-chat")

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

        # P3.1 send policy (§4). Fail-fast: ladder ascending with a top <= ceiling;
        # marketing gap min >= 10; every cap >= 1 (OQ-P3-03 owner-approved).
        sp_ladder = _ladder(_optional("SEND_POLICY_WARMUP_LADDER", "0:20,3:40,7:80,14:150,30:250"))
        sp_ceiling = _int("SEND_POLICY_DAILY_CEILING", 250)
        sp_marketing_gap = _gap_pair(_optional("SEND_POLICY_MARKETING_GAP_S", "20,60"))
        sp_utility_gap = _gap_pair(_optional("SEND_POLICY_UTILITY_GAP_S", "8,20"))
        sp_marketing_24h = _int("SEND_POLICY_MARKETING_PER_24H", 1)
        sp_marketing_7d = _int("SEND_POLICY_MARKETING_PER_7D", 2)
        sp_utility_24h = _int("SEND_POLICY_UTILITY_PER_24H", 3)
        if max(sp_ladder.values()) > sp_ceiling:
            raise ConfigError("warm-up ladder top must be <= SEND_POLICY_DAILY_CEILING")
        if sorted(sp_ladder.values()) != sorted(set(sp_ladder.values())):
            raise ConfigError("warm-up ladder caps must be strictly ascending")
        if sp_marketing_gap[0] < 10:
            raise ConfigError("marketing gap min must be >= 10 (Baileys conservative)")
        if min(sp_marketing_24h, sp_marketing_7d, sp_utility_24h) < 1:
            raise ConfigError("per-customer caps must be >= 1")
        # D2 (H83): a registered marketing template must require a non-empty footer.
        # P3.4: the template IS registered now, so this is live - it checks the
        # EFFECTIVE footer (env or the code default), so a boot never silently
        # lacks one. That the footer is also a DELIBERATE owner-set value is
        # enforced where it matters - `marketing enable` preflight (H103).
        effective_footer = _optional("MARKETING_FOOTER_AR", DEFAULT_MARKETING_FOOTER_AR)
        for _tid, (meta, _text, _keys) in PROACTIVE_TEMPLATES.items():
            if meta.message_class == "marketing" and (not meta.footer_required or not effective_footer.strip()):
                raise ConfigError("a marketing proactive template must require a non-empty opt-out footer")
        if _int("MARKETING_MIN_WARMUP_DAYS", 3) < 0:
            raise ConfigError("MARKETING_MIN_WARMUP_DAYS must be >= 0")
        # N-4 / P3.1 §1.5: equal quiet start/end means "quiet forever" - refuse.
        quiet_start = _hhmm(_optional("SEND_POLICY_QUIET_START", "22:00"))
        quiet_end = _hhmm(_optional("SEND_POLICY_QUIET_END", "09:00"))
        if quiet_start == quiet_end:
            raise ConfigError(
                "SEND_POLICY_QUIET_START must differ from SEND_POLICY_QUIET_END "
                "(equal means a permanent quiet window)"
            )

        # P3.2 (§4): scheduler + cart knobs. Fail-fast: every number positive;
        # the lease must outlive the poll cycle; the backoff cap must cover its
        # base (OQ-P3-06 proposals, all env-tunable).
        sched_poll = _int("SCHEDULER_POLL_INTERVAL_S", 15)
        sched_batch = _int("SCHEDULER_BATCH", 50)
        sched_lease = _int("SCHEDULER_LEASE_S", 120)
        sched_max_attempts = _int("SCHEDULER_MAX_ATTEMPTS", 5)
        sched_backoff_base = _int("SCHEDULER_BACKOFF_BASE_S", 60)
        sched_backoff_cap = _int("SCHEDULER_BACKOFF_CAP_S", 3600)
        cart_delay_h = _int("CART_REMINDER_DELAY_H", 24)
        cart_max_late_h = _int("CART_REMINDER_MAX_LATE_H", 12)
        carts_retention_d = _int("CARTS_RETENTION_D", 14)
        cart_title_max = _int("CART_ITEM_TITLE_MAX", 60)
        for _key, _val in (
            ("SCHEDULER_POLL_INTERVAL_S", sched_poll), ("SCHEDULER_BATCH", sched_batch),
            ("SCHEDULER_LEASE_S", sched_lease), ("SCHEDULER_MAX_ATTEMPTS", sched_max_attempts),
            ("SCHEDULER_BACKOFF_BASE_S", sched_backoff_base),
            ("SCHEDULER_BACKOFF_CAP_S", sched_backoff_cap),
            ("CART_REMINDER_DELAY_H", cart_delay_h), ("CART_REMINDER_MAX_LATE_H", cart_max_late_h),
            ("CARTS_RETENTION_D", carts_retention_d), ("CART_ITEM_TITLE_MAX", cart_title_max),
        ):
            if _val <= 0:
                raise ConfigError(f"{_key} must be >= 1")
        if sched_lease <= sched_poll:
            raise ConfigError("SCHEDULER_LEASE_S must be > SCHEDULER_POLL_INTERVAL_S")
        if sched_backoff_cap < sched_backoff_base:
            raise ConfigError("SCHEDULER_BACKOFF_CAP_S must be >= SCHEDULER_BACKOFF_BASE_S")

        commerce_base_url = _optional("COMMERCE_BASE_URL", "")
        commerce_api_secret = _optional("COMMERCE_API_SECRET", "")
        if commerce_base_url and not commerce_api_secret:
            raise ConfigError(
                "COMMERCE_API_SECRET is required when COMMERCE_BASE_URL is set (see .env)"
            )

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
            core_optin_phrases_ar=_csv(
                "CORE_OPTIN_PHRASES_AR", ",".join(DEFAULT_OPTIN_AR)
            ),
            core_optin_phrases_en=_csv(
                "CORE_OPTIN_PHRASES_EN", ",".join(DEFAULT_OPTIN_EN)
            ),
            core_handoff_phrases_ar=_csv(
                "CORE_HANDOFF_PHRASES_AR", ",".join(DEFAULT_HANDOFF_AR)
            ),
            core_handoff_phrases_en=_csv(
                "CORE_HANDOFF_PHRASES_EN", ",".join(DEFAULT_HANDOFF_EN)
            ),
            dedupe_done_ttl_s=_int("DEDUPE_DONE_TTL_S", 172800),
            metrics_token=metrics_token,
            commerce_base_url=commerce_base_url,
            commerce_api_secret=commerce_api_secret,
            commerce_timeout_s=float(_optional("COMMERCE_TIMEOUT_S", "3.0")),
            catalog_reconcile_interval_s=_int("CATALOG_RECONCILE_INTERVAL_S", 900),
            catalog_reconcile_max_tenants=_int("CATALOG_RECONCILE_MAX_TENANTS", 100),
            catalog_reconcile_max_events_per_tenant=_int("CATALOG_RECONCILE_MAX_EVENTS_PER_TENANT", 500),
            env=env,
            llm_provider=llm_provider,
            llm_timeout_s=_float("LLM_TIMEOUT_S", 8.0),
            llm_breaker_fail_threshold=_int("LLM_BREAKER_FAIL_THRESHOLD", 5),
            llm_breaker_reset_s=_float("LLM_BREAKER_RESET_S", 60.0),
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
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
            size_advice_enabled=_bool("SIZE_ADVICE_ENABLED", False),
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
            stock_hold_ttl_s=_int("STOCK_HOLD_TTL_S", 3600),
            stock_observation_max_age_s=_int("STOCK_OBSERVATION_MAX_AGE_S", 300),
            stock_sweep_interval_s=_int("STOCK_SWEEP_INTERVAL_S", 60),
            stock_max_variants_per_cycle=_int("STOCK_MAX_VARIANTS_PER_CYCLE", 50),
            stock_max_waitlist_per_customer=_int("STOCK_MAX_WAITLIST_PER_CUSTOMER", 10),
            send_policy_warmup_ladder=sp_ladder,
            send_policy_daily_ceiling=sp_ceiling,
            send_policy_utility_daily_cap=_int("SEND_POLICY_UTILITY_DAILY_CAP", 100),
            send_policy_marketing_gap=sp_marketing_gap,
            send_policy_utility_gap=sp_utility_gap,
            send_policy_throttle_cap_factor=_float("SEND_POLICY_THROTTLE_CAP_FACTOR", 0.5),
            send_policy_gap_factor=_float("SEND_POLICY_GAP_FACTOR", 2.0),
            send_policy_throttle_cooldown_h=_int("SEND_POLICY_THROTTLE_COOLDOWN_H", 24),
            send_policy_marketing_per_24h=sp_marketing_24h,
            send_policy_marketing_per_7d=sp_marketing_7d,
            send_policy_utility_per_24h=sp_utility_24h,
            send_policy_utility_min_gap_s=_int("SEND_POLICY_UTILITY_MIN_GAP_S", 60),
            send_policy_active_chat_cooldown_s=_int("SEND_POLICY_ACTIVE_CHAT_COOLDOWN_S", 1800),
            send_policy_quiet_start=_optional("SEND_POLICY_QUIET_START", "22:00"),
            send_policy_quiet_end=_optional("SEND_POLICY_QUIET_END", "09:00"),
            send_policy_marketing_ttl_h=_int("SEND_POLICY_MARKETING_TTL_H", 24),
            send_policy_utility_ttl_h=_int("SEND_POLICY_UTILITY_TTL_H", 6),
            send_policy_interaction_window_d=_int("SEND_POLICY_INTERACTION_WINDOW_D", 180),
            send_policy_marketing_claim_per_cycle=_int("SEND_POLICY_MARKETING_CLAIM_PER_CYCLE", 4),
            send_policy_sweep_interval_s=_int("SEND_POLICY_SWEEP_INTERVAL_S", 60),
            send_policy_idle_reset_d=_int("SEND_POLICY_IDLE_RESET_D", 14),
            send_policy_complaint_words=_csv("SEND_POLICY_COMPLAINT_WORDS", "سبام,ازعاج,إزعاج,بلاغ,ابلاغ,report,spam"),
            marketing_footer_ar=_optional("MARKETING_FOOTER_AR", DEFAULT_MARKETING_FOOTER_AR),
            marketing_min_warmup_days=_int("MARKETING_MIN_WARMUP_DAYS", 3),
            scheduler_poll_interval_s=sched_poll,
            scheduler_batch=sched_batch,
            scheduler_lease_s=sched_lease,
            scheduler_max_attempts=sched_max_attempts,
            scheduler_backoff_base_s=sched_backoff_base,
            scheduler_backoff_cap_s=sched_backoff_cap,
            cart_reminder_delay_h=cart_delay_h,
            cart_reminder_max_late_h=cart_max_late_h,
            carts_retention_d=carts_retention_d,
            cart_item_title_max=cart_title_max,
        )
