"""core/app/main.py - FastAPI app assembly.

Startup: load Settings.load() (fail-fast on a missing required env var,
before uvicorn ever binds a port), init_pool() (both DB pools), build
RedisSync + GatewayClient, launch the redis-cache rebuild loop as a real
asyncio background task (spec, literal: "asyncio، لا BackgroundTasks" - never
FastAPI's BackgroundTasks, which only runs after the response is already
sent and is dropped entirely if the worker is killed mid-request).
Shutdown: cancel the rebuild task, close_pool().

Every response carries an X-Request-Id (echoed from the caller's own header
when present, so a merchant's own client-generated id round-trips for
correlation; otherwise generated here) - this is what error_body()'s
request_id and audit_log's request_id column both come from.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from prometheus_client import CollectorRegistry

from app import db as core_db
from app.api.errors import ApiError, api_error_handler, unhandled_exception_handler
from app.api.routes_health import router as health_router
from app.api.routes_killswitches import router as killswitches_router
from app.channels.gateway_client import GatewayClient
from app.config import Settings
from app.killswitch.redis_sync import RedisSync


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings.load()
    core_db.init_pool(settings, system_dsn=settings.db.system_dsn)

    redis_sync = RedisSync(settings.redis, verify_timeout_s=settings.ks_publish_verify_timeout_s)
    gateway_client = GatewayClient(settings.gateway, api_key=settings.gateway_api_key)

    app.state.settings = settings
    app.state.redis_sync = redis_sync
    app.state.gateway_client = gateway_client
    app.state.metrics_registry = CollectorRegistry()

    rebuild_task = None
    if settings.env != "test":
        # In tests, redis-cache is a per-test local server and the periodic
        # rebuild task would keep running (and hitting a torn-down server)
        # past the individual test's own assertions - tests call
        # rebuild_from_postgres_once() explicitly instead, when they need it.
        import asyncio
        rebuild_task = asyncio.create_task(redis_sync.rebuild_loop())

    try:
        yield
    finally:
        if rebuild_task is not None:
            rebuild_task.cancel()
        core_db.close_pool()


def create_app() -> FastAPI:
    app = FastAPI(title="sharwa_ai core", lifespan=lifespan)

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    app.include_router(health_router)
    app.include_router(killswitches_router)

    return app


app = create_app()
