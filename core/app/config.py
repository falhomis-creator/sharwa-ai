"""core/app/config.py

Fail-fast environment configuration (H4/H5): every limit has a written,
documented default; every secret is required with no fallback to an
"accept everyone" default. Missing/empty required values raise at import
time, before uvicorn ever binds a port - never a silent "accept all" mode.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


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

    @staticmethod
    def load() -> Settings:
        env = _optional("ENV", "production")
        jwt = JwtConfig(
            issuer=_required("JWT_ISSUER"),
            audience=_required("JWT_AUDIENCE"),
            jwks_url=os.environ.get("JWKS_URL", "").strip() or None,
            public_key_pem=os.environ.get("JWT_PUBLIC_KEY_PEM", "").strip() or None,
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
        )
