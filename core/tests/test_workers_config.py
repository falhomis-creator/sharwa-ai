"""WorkerSettings fail-fast validation (P1.0.3, H4/H5).

Verifies the worker refuses to start on the exact unsafe configurations the
spec calls out - never by actually connecting (no DB/Redis here), only by the
pure env parsing + invariant checks in WorkerSettings.load().
"""
from __future__ import annotations

import pytest

from app.config import ConfigError
from app.workers.config import WorkerSettings


def _base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORE_DATABASE_URL", "host=pg dbname=x user=app password=p")
    monkeypatch.setenv("CORE_SYSTEM_DATABASE_URL", "host=pg dbname=x user=sys password=p")
    monkeypatch.setenv("REDIS_DURABLE_HOST", "127.0.0.1")
    monkeypatch.setenv("REDIS_DURABLE_PASSWORD", "pw")
    monkeypatch.setenv("METRICS_TOKEN", "tok")
    monkeypatch.setenv("GATEWAY_BASE_URL", "http://127.0.0.1:4001")
    monkeypatch.setenv("GATEWAY_API_KEY", "gk")
    monkeypatch.setenv("ORDER_REF_HASH_KEY", "test-order-ref-hash-key")
    monkeypatch.delenv("INGEST_SHARDS", raising=False)
    monkeypatch.delenv("CORE_DB_POOL_MAX", raising=False)
    monkeypatch.delenv("CORE_INGEST_BLOCK_MS", raising=False)


def test_load_succeeds_with_defaults(monkeypatch: pytest.MonkeyPatch):
    _base_env(monkeypatch)
    settings = WorkerSettings.load()
    assert settings.ingest_shards == 4
    assert settings.core_ingest_group == "ai-core-ingest"
    assert settings.core_ingest_dlq_stream == "dlq:in:core"
    assert settings.db.pool_max >= settings.ingest_shards + 1
    # D2/P1.2: the system pool must cover ingest + turn + evt threads (+2 for
    # the dispatcher's claim_outbox via system_tx).
    tenant_min = 2 * settings.ingest_shards + settings.core_turn_workers + 2
    assert settings.system_pool_max >= tenant_min + 2


def test_system_pool_max_below_minimum_is_rejected(monkeypatch: pytest.MonkeyPatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("INGEST_SHARDS", "4")
    monkeypatch.setenv("CORE_SYSTEM_POOL_MAX", "5")  # < 2*4 + 2 + 2 + 2 = 14
    with pytest.raises(ConfigError):
        WorkerSettings.load()


def test_pool_max_below_shards_plus_one_is_rejected(monkeypatch: pytest.MonkeyPatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("INGEST_SHARDS", "10")
    monkeypatch.setenv("CORE_DB_POOL_MAX", "5")
    with pytest.raises(ConfigError):
        WorkerSettings.load()


def test_block_ms_not_below_timeout_is_rejected(monkeypatch: pytest.MonkeyPatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("CORE_INGEST_BLOCK_MS", "5000")
    monkeypatch.setenv("REDIS_DURABLE_TIMEOUT_MS", "5000")
    with pytest.raises(ConfigError):
        WorkerSettings.load()


def test_empty_metrics_token_is_rejected(monkeypatch: pytest.MonkeyPatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("METRICS_TOKEN", "")
    with pytest.raises(ConfigError):
        WorkerSettings.load()


def test_missing_redis_password_is_rejected(monkeypatch: pytest.MonkeyPatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("REDIS_DURABLE_PASSWORD", "")
    with pytest.raises(ConfigError):
        WorkerSettings.load()
