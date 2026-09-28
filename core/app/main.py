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

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CollectorRegistry
from redis import asyncio as redis_asyncio

from app import db as core_db
from app import ws_publish
from app.api.errors import ApiError, api_error_handler, unhandled_exception_handler
from app.api.routes_catalog import router as catalog_router
from app.api.routes_health import router as health_router
from app.api.routes_inbox import router as inbox_router
from app.api.routes_killswitches import router as killswitches_router
from app.api.ws import WsHub
from app.api.ws import router as ws_router
from app.channels.gateway_client import GatewayClient
from app.config import Settings
from app.killswitch.redis_sync import RedisSync
from app.obs import metrics


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings.load()
    core_db.init_pool(settings, system_dsn=settings.db.system_dsn)

    redis_sync = RedisSync(settings.redis, verify_timeout_s=settings.ks_publish_verify_timeout_s)
    gateway_client = GatewayClient(settings.gateway, api_key=settings.gateway_api_key)
    ws_publish.configure(redis_sync._client)

    app.state.settings = settings
    app.state.redis_sync = redis_sync
    app.state.gateway_client = gateway_client
    app.state.metrics_registry = CollectorRegistry()

    redis_async = redis_asyncio.Redis(
        host=settings.redis.cache_host, port=settings.redis.cache_port,
        password=settings.redis.cache_password, decode_responses=True,
    )
    app.state.redis_async = redis_async
    app.state.ws_hub = WsHub(redis_async)

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
        try:
            await redis_async.aclose()
        except Exception:
            pass
        core_db.close_pool()


def create_app() -> FastAPI:
    settings = Settings.load()
    app = FastAPI(title="sharwa_ai core", lifespan=lifespan)
    app.state.settings = settings

    # CORS (P1.8, H56): the console is a browser on a different origin. Origins are
    # an explicit env allowlist (CONSOLE_ALLOWED_ORIGINS); an empty list => the
    # middleware is NOT registered (no open cross-origin default, and never
    # allow_origins=["*"] + allow_credentials). Methods/headers are explicit.
    if settings.console_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.console_allowed_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Idempotency-Key", "X-Request-Id", "Content-Type"],
            expose_headers=["X-Request-Id"],
        )

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    @app.middleware("http")
    async def inbox_metrics_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if path.startswith("/v1/conversations"):
            route = "conversations"
        elif path.startswith("/v1/inbox"):
            route = "inbox_events"
        else:
            return await call_next(request)
        started = time.monotonic()
        response = await call_next(request)
        metrics.inbox_api_requests_total.labels(route, str(response.status_code)).inc()
        metrics.inbox_api_request_seconds.labels(route).observe(time.monotonic() - started)
        return response

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    app.include_router(health_router)
    app.include_router(killswitches_router)
    app.include_router(inbox_router)
    app.include_router(catalog_router)
    app.include_router(ws_router)

    return app


app = create_app()
