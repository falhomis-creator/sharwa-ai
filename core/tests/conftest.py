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
os.environ.setdefault("PLATFORM_WEBHOOK_SECRET", "test-platform-webhook-secret")
os.environ.setdefault("COMMERCE_BASE_URL", "http://127.0.0.1:4100")
os.environ.setdefault("LLM_PROVIDER", "fake")

from app.config import Settings

TENANT_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings.load()


# M0 (P1.5 §0.3, owner decision): the two Postgres-dependent fixtures below are
# NOT autouse any more. They are auto-applied ONLY to tests marked
# `@pytest.mark.db` via pytest_collection_modifyitems(), so a bare
# `pytest core/tests` runs every pure test with no live Postgres and simply
# skips the db-marked ones (addopts = -m "not db" in pyproject.toml).


@pytest.fixture(scope="session")
def db_pool(settings):
    # N2 (P1.5 audit): the driver import is moved here, inside the only fixture
    # that needs it, so the pure test collection no longer requires psycopg /
    # psycopg_pool just to IMPORT the package.
    from app import db as core_db

    core_db.init_pool(settings, system_dsn=os.environ["CORE_SYSTEM_DATABASE_URL"])
    yield
    core_db.close_pool()


@pytest.fixture()
def _clean_kill_switches():
    """Real Postgres, real table - each test starts from a clean kill_switches
    table so cross-test interference cannot hide a real bug (H7: no flaky
    tests papered over). Cleanup goes through the migration role directly."""
    from app.db import testsupport as db_testsupport

    db_testsupport.clean_kill_switches_and_audit(os.environ["CORE_MIGRATION_DATABASE_URL"])
    yield


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.get_closest_marker("db"):
            item.add_marker(pytest.mark.usefixtures("db_pool", "_clean_kill_switches"))

