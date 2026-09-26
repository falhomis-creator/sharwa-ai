"""Real end-to-end test of the assembled FastAPI app (app.main.create_app):
a genuine TestClient (httpx under the hood) driving real startup/shutdown
lifespan events, a real Postgres tenant + JWT, a real local JWKS server, and
a real local redis-cache and stub gateway - not mocks of any of them."""
from __future__ import annotations

import base64
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt as pyjwt
import psycopg
import pytest
import redis
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient


def _gen_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return key, priv_pem


def _jwk_from_public_key(pub_key, kid: str) -> dict:
    numbers = pub_key.public_numbers()

    def b64(n: int) -> str:
        raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid, "n": b64(numbers.n), "e": b64(numbers.e)}


@pytest.fixture(scope="module")
def jwks_server():
    key, priv_pem = _gen_keypair()
    jwk = _jwk_from_public_key(key.public_key(), "app-test-kid")
    body = json.dumps({"keys": [jwk]}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}/.well-known/jwks.json", priv_pem
    server.shutdown()


@pytest.fixture(scope="module")
def stub_gateway():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/healthz":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture()
def real_tenant():
    """A real row in tenants, cleaned up after the test."""
    platform_ref = f"main-app-test-{uuid.uuid4()}"
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "INSERT INTO tenants (platform_ref, name, default_currency) VALUES (%s, %s, 'SAR') RETURNING id",
            (platform_ref, "Main App Test Tenant"),
        ).fetchone()
        conn.commit()
    yield platform_ref, row[0]
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM audit_log WHERE tenant_id = %s", (row[0],))
        conn.execute("DELETE FROM tenants WHERE id = %s", (row[0],))


def _token(priv_pem: str, *, platform_ref: str, role: str = "merchant_admin") -> str:
    now = int(time.time())
    claims = {
        "iss": os.environ["JWT_ISSUER"], "aud": os.environ["JWT_AUDIENCE"],
        "sub": "test-user", "tenant": platform_ref, "role": role,
        "iat": now, "exp": now + 3600,
    }
    return pyjwt.encode(claims, priv_pem, algorithm="RS256", headers={"kid": "app-test-kid"})


@pytest.fixture()
def client(jwks_server, stub_gateway, monkeypatch):
    jwks_url, priv_pem = jwks_server
    monkeypatch.setenv("JWKS_URL", jwks_url)
    monkeypatch.setenv("GATEWAY_BASE_URL", stub_gateway)

    # conftest's session-scoped db_pool fixture already called core_db.init_pool()
    # once for the whole test session, and other test modules rely on that SAME
    # pool staying open for the rest of the session. In production, main.py's own
    # lifespan owning init_pool()/close_pool() for the process is exactly right -
    # but here it would tear down the shared session pool the moment this one
    # TestClient's lifespan exits. No-op the pool lifecycle for this app instance;
    # real DB access in these tests goes through the already-open session pool.
    import app.main as main_module
    monkeypatch.setattr(main_module.core_db, "init_pool", lambda *a, **kw: None)
    monkeypatch.setattr(main_module.core_db, "close_pool", lambda: None)

    from app.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c, priv_pem


def test_healthz_needs_no_auth(client):
    c, _ = client
    resp = c.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert "X-Request-Id" in resp.headers


def test_readyz_reports_real_checks(client):
    c, _ = client
    resp = c.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["checks"] == {"postgres": True, "redis_cache": True, "gateway": True}


def test_metrics_requires_bearer_token(client):
    c, _ = client
    assert c.get("/metrics").status_code == 401
    assert c.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = c.get("/metrics", headers={"Authorization": f"Bearer {os.environ['METRICS_TOKEN']}"})
    assert ok.status_code == 200


def test_kill_switches_end_to_end_through_real_http(client, real_tenant):
    c, priv_pem = client
    platform_ref, tenant_id = real_tenant
    token = _token(priv_pem, platform_ref=platform_ref, role="merchant_admin")
    headers = {"Authorization": f"Bearer {token}", "X-Request-Id": "test-req-1"}

    listed = c.get("/v1/kill-switches", headers=headers)
    assert listed.status_code == 200
    assert listed.json() == []

    put = c.put(
        "/v1/kill-switches/ai_reply",
        headers=headers,
        json={"scope": "tenant", "state": "off", "reason": "e2e test"},
    )
    assert put.status_code == 200, put.text
    assert put.json() == {"capability": "ai_reply", "state": "off"}
    assert put.headers["X-Request-Id"] == "test-req-1"

    # Real proof, independent of the API: audit_log actually got the row, AND
    # redis-cache actually has the new state (not just Postgres).
    with psycopg.connect(os.environ["CORE_MIGRATION_DATABASE_URL"]) as conn:
        row = conn.execute(
            "SELECT action, request_id FROM audit_log WHERE tenant_id = %s", (tenant_id,)
        ).fetchone()
    assert row == ("kill_switch.set", "test-req-1")

    r = redis.Redis(host="127.0.0.1", port=6390, password="test-redis-pw", decode_responses=True)
    assert r.hget(f"ks:tenant:{tenant_id}", "ai_reply") == "off"

    listed_again = c.get("/v1/kill-switches", headers=headers)
    assert listed_again.status_code == 200
    assert listed_again.json()[0]["state"] == "off"


def test_kill_switches_rejects_wrong_role(client, real_tenant):
    c, priv_pem = client
    platform_ref, _ = real_tenant
    token = _token(priv_pem, platform_ref=platform_ref, role="staff")
    resp = c.put(
        "/v1/kill-switches/ai_reply",
        headers={"Authorization": f"Bearer {token}"},
        json={"scope": "tenant", "state": "off"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN_ROLE"


def test_global_kill_switch_platform_admin_writes_null_tenant_audit_row(client, real_tenant):
    c, priv_pem = client
    platform_ref, _ = real_tenant
    token = _token(priv_pem, platform_ref=platform_ref, role="platform_admin")
    resp = c.put(
        "/v1/admin/kill-switches/global/marketing",
        headers={"Authorization": f"Bearer {token}", "X-Request-Id": "global-req-1"},
        json={"scope": "tenant", "state": "degraded", "reason": "incident"},
    )
    assert resp.status_code == 200, resp.text

    with psycopg.connect(os.environ["CORE_MIGRATION_DATABASE_URL"]) as conn:
        row = conn.execute(
            "SELECT tenant_id, action, request_id FROM audit_log WHERE action = 'kill_switch.set_global'"
        ).fetchone()
    assert row == (None, "kill_switch.set_global", "global-req-1")

    r = redis.Redis(host="127.0.0.1", port=6390, password="test-redis-pw", decode_responses=True)
    assert r.hget("ks:global", "marketing") == "degraded"


def test_unauthenticated_request_rejected(client):
    c, _ = client
    resp = c.get("/v1/kill-switches")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHENTICATED"
