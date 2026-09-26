"""core/app/killswitch/redis_sync.py

Writes the fast copy of kill_switches to redis-cache and publishes the
change-notification the gateway subscribes to. Wire format matches
gateway/src/killswitch.js EXACTLY (verified against that real file, not
guessed):

  ks:global                    HASH  field=capability, value='on'|'degraded'|'off'
  ks:tenant:{tenant_id}        HASH  same shape
  ks:channel:{channel_id}      HASH  same shape
  ks:changes                   PUBSUB  {"scope":"global"} |
                                       {"scope":"tenant","id":"<tenant_id>"} |
                                       {"scope":"channel_account","id":"<channel_account_id>"}
                                       (gateway's scopeKeyFromChangePayload
                                       accepts both 'channel' and
                                       'channel_account' spellings - we use
                                       'channel_account' since it matches the
                                       DB's own kill_switches.scope value and
                                       needs no separate mapping table here)

Also runs the periodic Postgres -> redis-cache rebuild the spec requires
(redis-cache is lossy): once at startup and every 60s, via asyncio - never
FastAPI BackgroundTasks (spec, literal: "asyncio، لا BackgroundTasks").
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import redis
from fastapi import Request

from app import db as core_db
from app.api.errors import ApiError
from app.config import RedisConfig
from app.db import repos

CHANGES_CHANNEL = "ks:changes"

logger = logging.getLogger(__name__)


def _hash_key(scope: str, tenant_id: str | None, channel_id: str | None) -> str:
    if scope == "global":
        return "ks:global"
    if scope == "tenant":
        return f"ks:tenant:{tenant_id}"
    if scope == "channel_account":
        return f"ks:channel:{channel_id}"
    raise ValueError(f"unknown scope {scope!r}")


def _message_for(scope: str, tenant_id: str | None, channel_id: str | None) -> str:
    if scope == "global":
        return json.dumps({"scope": "global"})
    if scope == "tenant":
        return json.dumps({"scope": "tenant", "id": tenant_id})
    if scope == "channel_account":
        return json.dumps({"scope": "channel_account", "id": channel_id})
    raise ValueError(f"unknown scope {scope!r}")


class RedisSync:
    def __init__(self, cfg: RedisConfig, *, verify_timeout_s: float = 2.0):
        self._client = redis.Redis(
            host=cfg.cache_host, port=cfg.cache_port, password=cfg.cache_password,
            decode_responses=True, socket_timeout=verify_timeout_s,
        )
        self._verify_timeout_s = verify_timeout_s

    def publish_and_verify(self, *, scope: str, tenant_id: str | None,
                            channel_id: str | None, capability: str, state: str) -> None:
        key = _hash_key(scope, tenant_id, channel_id)
        try:
            self._client.hset(key, capability, state)
            self._client.publish(CHANGES_CHANNEL, _message_for(scope, tenant_id, channel_id))
            # Verify the write actually landed in the cache layer we just wrote
            # to (read-after-write) - this is the propagation guarantee core/
            # itself controls end-to-end; the gateway's own <1s pickup of the
            # PUBLISH is a separate, already-measured guarantee (P0.6 Batch B,
            # 11/11 real tests against real redis-cache) that core does not
            # re-verify per request to avoid tight request/response coupling
            # to gateway's internal cache refresh cycle.
            deadline = time.monotonic() + self._verify_timeout_s
            while time.monotonic() < deadline:
                if self._client.hget(key, capability) == state:
                    return
                time.sleep(0.05)
            raise ApiError("GATEWAY_UNAVAILABLE")
        except redis.RedisError as exc:
            raise ApiError("GATEWAY_UNAVAILABLE") from exc

    def rebuild_from_postgres_once(self) -> int:
        """Full rebuild: read every kill_switches row via system_tx() (sharwa_system
        has SELECT on kill_switches per schema.sql) and HSET each into its scope's
        hash. Returns the number of rows written."""
        n = 0
        with core_db.system_tx() as conn:
            rows = repos.fetch_all_kill_switches_raw(conn)
        for scope, scope_id, capability, state in rows:
            tenant_id = str(scope_id) if scope == "tenant" else None
            channel_id = str(scope_id) if scope == "channel_account" else None
            key = _hash_key(scope, tenant_id, channel_id)
            self._client.hset(key, capability, state)
            n += 1
        return n

    async def rebuild_loop(self, *, interval_s: float = 60.0) -> None:
        while True:
            try:
                self.rebuild_from_postgres_once()
            except Exception:  # a failed rebuild must not crash the app; next tick retries
                logger.exception("kill-switch redis-cache rebuild failed; will retry next tick")
            await asyncio.sleep(interval_s)


def get_redis_sync(request: Request) -> RedisSync:
    redis_sync: RedisSync = request.app.state.redis_sync
    return redis_sync
