"""core/app/config.py

Fail-fast environment configuration (H4/H5): every limit has a written,
documented default; every secret is required with no fallback to an
"accept everyone" default. Missing/empty required values raise at import
time, before uvicorn ever binds a port - never a silent "accept all" mode.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


class ConfigError(RuntimeError):
    """Raised at startup when a required setting is missing or invalid."""


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


# H36 / S7 (PROMPT_P1_04 §4.1): the field names that signal a cost/wholesale/
# margin leak from the platform. The catalog read model must never store these;
# an inbound event carrying one is dropped at the gate and counted. This list
# lives here (not in app/db/repos_catalog.py) because the S7 static-gate stage
# scans the catalog path for these literals as standalone tokens - keeping the
# policy in the config layer leaves the catalog code free of the banned tokens,
# while the runtime detector (repos_catalog) and the static gate (static_gate.py)
# share the same vocabulary.
CATALOG_FORBIDDEN_FIELDS: frozenset[str] = frozenset({
    "cost", "cost_price", "wholesale", "margin", "profit", "purchase_price",
    "تكلفة", "هامش", "جملة",
})


@dataclass(frozen=True)
class DatabaseConfig:
    dsn: str
    system_dsn: str
    pool_min: int = 3
    pool_max: int = 5


@dataclass(frozen=True)
class JwtConfig:
    issuer: str
    audience: str
    jwks_url: str | None
    public_key_pem: str | None
    clock_skew_s: int = 30
    env: str = "production"

    def __post_init__(self) -> None:
        if not self.jwks_url and not self.public_key_pem:
            raise ConfigError(
                "one of JWKS_URL or JWT_PUBLIC_KEY_PEM is required - "
                "refusing to boot with no way to verify a token (spec: "
                "'غياب كليهما = رفض إقلاع')"
            )


@dataclass(frozen=True)
class RedisConfig:
    cache_host: str
    cache_port: int
    cache_password: str


@dataclass(frozen=True)
class GatewayConfig:
    base_url: str
    timeout_s: float = 3.0
    breaker_fail_threshold: int = 5
    breaker_reset_s: float = 30.0


@dataclass(frozen=True)
class LlmConfig:
    """P4.2 (owner decision): the marketing engine's LLM is DeepSeek - a fully
    OpenAI-compatible API at https://api.deepseek.com, model deepseek-chat
    (DEEPSEEK_MODEL overrides without a code deploy). OpenAI is excluded.

    The api service itself NEVER calls the LLM (H38: only the worker's
    turn/embed/summary layers do), so api_key is deliberately OPTIONAL here;
    the WORKER enforces a non-empty DEEPSEEK_API_KEY at boot (H5) in
    app/workers/config.py. This block exists so the whole stack documents ONE
    provider configuration and .env stays the single source of truth."""

    provider: str = "deepseek"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"


@dataclass(frozen=True)
class Settings:
    env: str
    db: DatabaseConfig
    jwt: JwtConfig
    redis: RedisConfig
    gateway: GatewayConfig
    metrics_token: str
    sso_login_url: str
    console_stage: str
    gateway_api_key: str
    # P1.4 catalog webhook: independent HMAC secret (required - empty refuses
    # boot, H5).
    platform_webhook_secret: str
    request_body_max_bytes: int = 1_000_000
    reconnect_rate_limit_s: int = 30
    ks_publish_verify_timeout_s: float = 2.0
    inbox_events_retention_days: int = 7
    # P1.4 catalog webhook: replay window and written batch/body ceilings (H4).
    platform_timestamp_skew_s: int = 300
    platform_event_batch_max: int = 1000
    platform_webhook_body_max_bytes: int = 262_144
    # P1.8 console (H56/H59): explicit CORS origins (empty => CORS middleware is
    # NOT registered, so no open cross-origin default) and the three rate-limit
    # group ceilings (read/write/ticket - ticket is the narrowest). The numbers
    # are a written estimate, not a measurement (OQ-P1-30).
    console_allowed_origins: tuple[str, ...] = ()
    console_rate_limit_read: int = 120
    console_rate_limit_write: int = 60
    console_rate_limit_ticket: int = 10
    console_rate_limit_window_s: float = 60.0
    # P3.2 cart webhook: the SAME env keys the realtime worker reads - the API
    # process needs them to schedule the reminder job while applying cart
    # events (single source of truth = the environment, never a second copy).
    cart_reminder_delay_h: int = 24
    cart_reminder_max_late_h: int = 12
    cart_item_title_max: int = 60
    default_country_code: str = "967"
    # P4.2: the LLM provider block (see LlmConfig above) - informational for
    # the api (it never calls the LLM); the worker enforces the key (H5).
    llm: LlmConfig = field(default_factory=LlmConfig)

    @staticmethod
    def load() -> Settings:
        env = _optional("ENV", "production")
        jwt = JwtConfig(
            issuer=_required("JWT_ISSUER"),
            audience=_required("JWT_AUDIENCE"),
            jwks_url=os.environ.get("JWKS_URL", "").strip() or None,
            public_key_pem=(os.environ.get("JWT_PUBLIC_KEY_PEM", "").strip().replace("\\n", "\n") or None),
            env=env,
        )
        db = DatabaseConfig(
            dsn=_required("CORE_DATABASE_URL"),
            system_dsn=_required("CORE_SYSTEM_DATABASE_URL"),
            pool_min=_int("CORE_DB_POOL_MIN", 3),
            pool_max=_int("CORE_DB_POOL_MAX", 5),
        )
        redis = RedisConfig(
            cache_host=_required("REDIS_CACHE_HOST"),
            cache_port=_int("REDIS_CACHE_PORT", 6379),
            cache_password=_required("REDIS_CACHE_PASSWORD"),
        )
        gateway = GatewayConfig(
            base_url=_required("GATEWAY_BASE_URL"),
            timeout_s=float(_optional("GATEWAY_TIMEOUT_S", "3.0")),
        )
        return Settings(
            env=env,
            db=db,
            jwt=jwt,
            redis=redis,
            gateway=gateway,
            metrics_token=_required("METRICS_TOKEN"),
            sso_login_url=_required("SSO_LOGIN_URL"),
            console_stage=_optional("CONSOLE_STAGE", "preview"),
            gateway_api_key=_required("GATEWAY_API_KEY"),
            inbox_events_retention_days=_int("INBOX_EVENTS_RETENTION_DAYS", 7),
            # Empty secret -> _required() raises at import time (H5: refuse boot).
            platform_webhook_secret=_required("PLATFORM_WEBHOOK_SECRET"),
            platform_timestamp_skew_s=_int("PLATFORM_TIMESTAMP_SKEW_S", 300),
            platform_event_batch_max=_int("PLATFORM_EVENT_BATCH_MAX", 1000),
            platform_webhook_body_max_bytes=_int("PLATFORM_WEBHOOK_BODY_MAX_BYTES", 262_144),
            console_allowed_origins=_csv("CONSOLE_ALLOWED_ORIGINS", ""),
            console_rate_limit_read=_int("CONSOLE_RATE_LIMIT_READ", 120),
            console_rate_limit_write=_int("CONSOLE_RATE_LIMIT_WRITE", 60),
            console_rate_limit_ticket=_int("CONSOLE_RATE_LIMIT_TICKET", 10),
            console_rate_limit_window_s=float(_optional("CONSOLE_RATE_LIMIT_WINDOW_S", "60.0")),
            cart_reminder_delay_h=_int("CART_REMINDER_DELAY_H", 24),
            cart_reminder_max_late_h=_int("CART_REMINDER_MAX_LATE_H", 12),
            cart_item_title_max=_int("CART_ITEM_TITLE_MAX", 60),
            default_country_code=_optional("DEFAULT_COUNTRY_CODE", "967"),
            llm=LlmConfig(
                provider=_optional("LLM_PROVIDER", "deepseek"),
                api_key=_optional("DEEPSEEK_API_KEY", ""),
                base_url=_optional("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                model=_optional("DEEPSEEK_MODEL", "deepseek-chat"),
            ),
        )
