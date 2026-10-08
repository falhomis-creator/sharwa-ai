"""P4 Task 17: the public front door (ops/proxy) and the compose wiring the
domain needs - static checks on the files the owner installs on the VPS. The
proxies themselves were exercised end to end locally (execution_log, Task 17);
these tests keep the security-relevant lines from silently disappearing."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CADDY = (ROOT / "ops/proxy/Caddyfile").read_text(encoding="utf-8")
NGINX = (ROOT / "ops/proxy/nginx-api.sharwa.app.conf").read_text(encoding="utf-8")


def _api_service_block() -> str:
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    match = re.search(r"\n  api:\n(.*?)\n  [a-z][\w-]*:\n", text, re.S)
    assert match, "api service not found in docker-compose.yml"
    return match.group(1)


def test_compose_passes_the_console_origins_to_the_api():
    # F-P4-11: without these the /v1/ws handshake through the domain is refused
    # (Origin not allowed) and the SSO launch is off. Empty default = today.
    api = _api_service_block()
    assert "CONSOLE_ALLOWED_ORIGINS: ${CONSOLE_ALLOWED_ORIGINS:-}" in api
    assert "CONSOLE_SSO_PLATFORM_ORIGINS: ${CONSOLE_SSO_PLATFORM_ORIGINS:-}" in api


def test_api_port_stays_on_loopback():
    assert re.search(r'- "127\.0\.0\.1:\$\{API_HOST_PORT:-8000\}:8080"', _api_service_block())


def test_caddy_is_an_allow_list_with_a_404_catch_all():
    assert "@app path /console/* /v1/* /healthz" in CADDY
    assert "@webhooks path /webhooks/platform/catalog /webhooks/platform/cart" in CADDY
    assert re.search(r"\thandle \{\n\t\trespond 404\n\t\}", CADDY)
    assert "/metrics" not in CADDY.split("@app path", 1)[1].split("\n", 1)[0]


def test_caddy_strips_the_ws_ticket_from_the_log_and_sends_hsts():
    assert re.search(r"request>uri query \{\n\t+delete ticket\n", CADDY)
    assert 'Strict-Transport-Security "max-age=31536000"' in CADDY


def test_nginx_mirrors_the_allow_list_and_logs_no_query_string():
    assert re.search(r"    location / \{\n        return 404;\n    \}\n\}", NGINX)
    log_format = NGINX.split("log_format sharwa_ai_noquery", 1)[1].split(";", 1)[0]
    assert "$uri" in log_format and "$request_uri" not in log_format and "$args" not in log_format
    for exact in ("/webhooks/platform/catalog", "/webhooks/platform/cart", "/v1/ws", "/healthz"):
        assert f"location = {exact} " in NGINX
    assert "location /metrics" not in NGINX and "location = /metrics" not in NGINX
