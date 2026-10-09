"""Commerce API secret wiring (OQ-P4-03, H60): WorkerSettings refuses to boot
with COMMERCE_BASE_URL set but no COMMERCE_API_SECRET, and passes the secret
through when present. Pure env parsing - no DB/Redis/network."""
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


def test_base_url_without_secret_refuses_boot(monkeypatch: pytest.MonkeyPatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("COMMERCE_BASE_URL", "http://127.0.0.1:4100")
    monkeypatch.delenv("COMMERCE_API_SECRET", raising=False)
    with pytest.raises(ConfigError, match="COMMERCE_API_SECRET"):
        WorkerSettings.load()


def test_no_base_url_needs_no_secret(monkeypatch: pytest.MonkeyPatch):
    _base_env(monkeypatch)
    monkeypatch.delenv("COMMERCE_BASE_URL", raising=False)
    monkeypatch.delenv("COMMERCE_API_SECRET", raising=False)
    settings = WorkerSettings.load()
    assert settings.commerce_base_url == ""


def test_base_url_with_secret_loads(monkeypatch: pytest.MonkeyPatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("COMMERCE_BASE_URL", "http://127.0.0.1:4100")
    monkeypatch.setenv("COMMERCE_API_SECRET", "shh")
    settings = WorkerSettings.load()
    assert settings.commerce_api_secret == "shh"
