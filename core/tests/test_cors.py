"""CORS tests (P1.8 §3.1, H56): explicit-origin allowlist, no open default, and
the WebSocket Origin check (which CORSMiddleware does not protect)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import create_app


def test_allowed_origin_preflight_passes(monkeypatch):
    monkeypatch.setenv("CONSOLE_ALLOWED_ORIGINS", "https://console.example.com")
    client = TestClient(create_app())
    resp = client.options(
        "/v1/conversations",
        headers={
            "Origin": "https://console.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "https://console.example.com"


def test_disallowed_origin_has_no_allow_header(monkeypatch):
    monkeypatch.setenv("CONSOLE_ALLOWED_ORIGINS", "https://console.example.com")
    client = TestClient(create_app())
    resp = client.options(
        "/v1/conversations",
        headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in resp.headers


def test_empty_origins_registers_no_middleware(monkeypatch):
    monkeypatch.delenv("CONSOLE_ALLOWED_ORIGINS", raising=False)
    client = TestClient(create_app())
    resp = client.options(
        "/v1/conversations",
        headers={"Origin": "https://console.example.com", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in resp.headers


def test_ws_origin_not_allowed_rejected():
    from app.api import ws

    class FakeWs:
        def __init__(self) -> None:
            self.headers = {"origin": "https://evil.example.com"}
            self.app = SimpleNamespace(state=SimpleNamespace(
                ws_hub=None,
                redis_async=None,
                settings=SimpleNamespace(console_allowed_origins=("https://console.example.com",)),
            ))
            self.query_params: dict[str, str] = {}
            self.closed: int | None = None

        async def close(self, code: int) -> None:
            self.closed = code

    sock = FakeWs()
    asyncio.run(ws.websocket_endpoint(sock))
    assert sock.closed == 1008


def test_ws_missing_origin_is_allowed():
    # A non-browser client (no Origin header) is not subject to CORS.
    from app.api import ws

    class FakeWs:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self.app = SimpleNamespace(state=SimpleNamespace(
                ws_hub=None,
                redis_async=None,
                settings=SimpleNamespace(console_allowed_origins=("https://console.example.com",)),
            ))
            self.query_params = {"token": "jwt"}  # falls through to the token check
            self.closed: int | None = None

        async def close(self, code: int) -> None:
            self.closed = code

    sock = FakeWs()
    asyncio.run(ws.websocket_endpoint(sock))
    # Origin check passes (no Origin); the token-in-URL check closes with 1008.
    assert sock.closed == 1008