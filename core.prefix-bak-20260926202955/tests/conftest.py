import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("ENV", "test")
os.environ.setdefault("CORE_DATABASE_URL", "postgresql://p07_app:p07_app_pw@127.0.0.1:5432/sharwa_ai_p07")
os.environ.setdefault("CORE_SYSTEM_DATABASE_URL", "postgresql://p07_system:p07_system_pw@127.0.0.1:5432/sharwa_ai_p07")
os.environ.setdefault("CORE_MIGRATION_DATABASE_URL", "postgresql://p07_migration:p07_migration_pw@127.0.0.1:5432/sharwa_ai_p07")
os.environ.setdefault("JWT_ISSUER", "https://sso.sharwa.test")
os.environ.setdefault("JWT_AUDIENCE", "sharwa-ai-core")
os.environ.setdefault("JWT_PUBLIC_KEY_PEM", "placeholder-overridden-by-fixture")
os.environ.setdefault("REDIS_CACHE_HOST", "127.0.0.1")
os.environ.setdefault("REDIS_CACHE_PORT", "6390")
os.environ.setdefault("REDIS_CACHE_PASSWORD", "test-redis-pw")
os.environ.setdefault("GATEWAY_BASE_URL", "http://127.0.0.1:4099")
os.environ.setdefault("GATEWAY_API_KEY", "test-gateway-api-key")
os.environ.setdefault("METRICS_TOKEN", "test-metrics-token")
os.environ.setdefault("SSO_LOGIN_URL", "https://sso.sharwa.test/login")

from app import db as core_db
from app.config import Settings
from app.db import testsupport as db_testsupport

TENANT_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings.load()


@pytest.fixture(scope="session", autouse=True)
def db_pool(settings):
    core_db.init_pool(settings, system_dsn=os.environ["CORE_SYSTEM_DATABASE_URL"])
    yield
    core_db.close_pool()


@pytest.fixture(autouse=True)
def _clean_kill_switches():
    """Real Postgres, real table - each test starts from a clean kill_switches
    table so cross-test interference cannot hide a real bug (H7: no flaky
    tests papered over). sharwa_system only has SELECT on kill_switches (by
    design, schema.sql), so cleanup goes through the migration role directly -
    exactly like a real migration/ops task would, never through the app's own
    tenant_tx()/system_tx() entry points (that would be testing infra using
    itself to reset itself)."""
    db_testsupport.clean_kill_switches_and_audit(os.environ["CORE_MIGRATION_DATABASE_URL"])
    yield
