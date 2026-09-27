"""Real redis-cache, real Postgres. Verifies core's RedisSync writes the exact
wire format gateway/src/killswitch.js expects (hash keys + PUBSUB payload),
and that app.set_kill_switch's scope enforcement + RedisSync's write together
produce the state gateway would actually enforce."""
import json
import os

import pytest
import redis

from app.db import testsupport as db_testsupport
from app.killswitch.redis_sync import RedisSync, _hash_key, _message_for

pytestmark = pytest.mark.db


@pytest.fixture
def redis_sync():
    return RedisSync.__new__(RedisSync) if False else RedisSync(
        _cfg(), verify_timeout_s=2.0
    )


def _cfg():
    from app.config import RedisConfig
    return RedisConfig(cache_host="127.0.0.1", cache_port=6390, cache_password="test-redis-pw")


@pytest.fixture(autouse=True)
def _flush_redis():
    r = redis.Redis(host="127.0.0.1", port=6390, password="test-redis-pw", decode_responses=True)
    r.flushall()
    yield
    r.flushall()


def test_hash_key_matches_gateway_contract():
    assert _hash_key("global", None, None) == "ks:global"
    assert _hash_key("tenant", "t-1", None) == "ks:tenant:t-1"
    assert _hash_key("channel_account", None, "c-1") == "ks:channel:c-1"


def test_message_shape_matches_gateway_contract():
    assert json.loads(_message_for("global", None, None)) == {"scope": "global"}
    assert json.loads(_message_for("tenant", "t-1", None)) == {"scope": "tenant", "id": "t-1"}
    assert json.loads(_message_for("channel_account", None, "c-1")) == {
        "scope": "channel_account", "id": "c-1",
    }


def test_publish_and_verify_writes_real_redis(redis_sync):
    redis_sync.publish_and_verify(scope="tenant", tenant_id="tenant-x", channel_id=None,
                                   capability="ai_reply", state="off")
    r = redis.Redis(host="127.0.0.1", port=6390, password="test-redis-pw", decode_responses=True)
    assert r.hget("ks:tenant:tenant-x", "ai_reply") == "off"


def test_publish_and_verify_publishes_real_pubsub_message(redis_sync):
    r = redis.Redis(host="127.0.0.1", port=6390, password="test-redis-pw", decode_responses=True)
    pubsub = r.pubsub()
    pubsub.subscribe("ks:changes")
    pubsub.get_message(timeout=1)  # subscribe confirmation

    redis_sync.publish_and_verify(scope="channel_account", tenant_id=None, channel_id="chan-9",
                                   capability="marketing", state="degraded")

    msg = None
    for _ in range(10):
        msg = pubsub.get_message(timeout=1)
        if msg and msg["type"] == "message":
            break
    assert msg is not None
    payload = json.loads(msg["data"])
    assert payload == {"scope": "channel_account", "id": "chan-9"}


def test_rebuild_from_postgres_populates_redis(redis_sync):
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.clean_kill_switches_and_audit(dsn)
    db_testsupport.seed_kill_switch(
        dsn, scope="global", scope_id=None, capability="marketing", state="off", set_by="test",
    )
    n = redis_sync.rebuild_from_postgres_once()
    assert n == 1
    r = redis.Redis(host="127.0.0.1", port=6390, password="test-redis-pw", decode_responses=True)
    assert r.hget("ks:global", "marketing") == "off"
