"""core/tests/test_routes_channels.py - P0.7 channel-lifecycle routes
(app/api/routes_channels.py), end to end through a real TestClient/app.main,
a real Postgres tenant + JWT, a real local JWKS server, and a real,
configurable local stub gateway (never a mocked GatewayClient/httpx
transport - H7). Same fixture style as test_main_app.py, extended so each
test can script the stub gateway's responses for POST /sessions,
GET /sessions/:id/health, GET /sessions/:id/qr and POST /sessions/:id/reconnect.
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt as pyjwt
import pytest
import redis
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.db import testsupport as db_testsupport


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
    jwk = _jwk_from_public_key(key.public_key(), "channels-test-kid")
    body = json.dumps({"keys": [jwk]}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            return None

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}/.well-known/jwks.json", priv_pem
    server.shutdown()


def _default_gateway_state() -> dict:
    return {
        "create_status": 200,
        "create_body": {"status": "QR_PENDING", "qr_image_base64": None, "connected_phone_number": None},
        "health_status": 200,
        "health_body": {"reconnect_attempts": 0, "last_disconnect_reason": None, "queue_depth": 0},
        "qr_status": 200,
        "qr_body": {"qr": "data:image/png;base64,FAKE"},
        "reconnect_status": 200,
        "reconnect_body": {
            "status": "CONNECTED", "qr_image_base64": None, "connected_phone_number": "201234567890",
        },
        "requests": [],  # (method, path, body-dict-or-None), in call order
    }


@pytest.fixture()
def gateway_state():
    return _default_gateway_state()


@pytest.fixture()
def stub_gateway(gateway_state):
    state = gateway_state

    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, code: int, body: dict) -> None:
            payload = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path == "/healthz":
                self._send_json(200, {"status": "ok"})
                return
            if re.match(r"^/sessions/[^/]+/health$", self.path):
                state["requests"].append(("GET", self.path, None))
                self._send_json(state["health_status"], state["health_body"])
                return
            if re.match(r"^/sessions/[^/]+/qr$", self.path):
                state["requests"].append(("GET", self.path, None))
                self._send_json(state["qr_status"], state["qr_body"])
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw else {}
            if self.path == "/sessions":
                state["requests"].append(("POST", self.path, body))
                self._send_json(state["create_status"], state["create_body"])
                return
            if re.match(r"^/sessions/[^/]+/reconnect$", self.path):
                state["requests"].append(("POST", self.path, body))
                self._send_json(state["reconnect_status"], state["reconnect_body"])
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, *a):
            return None

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture()
def real_tenant():
    """A real row in tenants, cleaned up after the test (channel_accounts and
    api_idempotency rows this test created are cleaned up first - neither has
    ON DELETE CASCADE to tenants - via delete_tenant_full())."""
    platform_ref = f"channels-test-{uuid.uuid4()}"
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    tenant_id = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=platform_ref, name="Channels Test Tenant",
    )
    yield platform_ref, tenant_id
    db_testsupport.delete_tenant_full(dsn, tenant_id)


def _token(priv_pem: str, *, platform_ref: str, role: str = "merchant_admin") -> str:
    now = int(time.time())
    claims = {
        "iss": os.environ["JWT_ISSUER"], "aud": os.environ["JWT_AUDIENCE"],
        "sub": "test-user", "tenant": platform_ref, "role": role,
        "iat": now, "exp": now + 3600,
    }
    return pyjwt.encode(claims, priv_pem, algorithm="RS256", headers={"kid": "channels-test-kid"})


@pytest.fixture()
def client(jwks_server, stub_gateway, monkeypatch):
    jwks_url, priv_pem = jwks_server
    monkeypatch.setenv("JWKS_URL", jwks_url)
    monkeypatch.setenv("GATEWAY_BASE_URL", stub_gateway)

    import app.main as main_module
    monkeypatch.setattr(main_module.core_db, "init_pool", lambda *a, **kw: None)
    monkeypatch.setattr(main_module.core_db, "close_pool", lambda: None)

    from app.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c, priv_pem


def _headers(priv_pem: str, platform_ref: str, *, role: str = "merchant_admin") -> dict:
    return {"Authorization": f"Bearer {_token(priv_pem, platform_ref=platform_ref, role=role)}"}


# --- GET /v1/me ---------------------------------------------------------


def test_get_me_returns_tenant_name_and_role(client, real_tenant):
    c, priv_pem = client
    platform_ref, _ = real_tenant
    resp = c.get("/v1/me", headers=_headers(priv_pem, platform_ref))
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"name": "Channels Test Tenant", "role": "merchant_admin"}


# --- GET /v1/channels ----------------------------------------------------


def test_list_channels_reflects_created_channel(client, real_tenant):
    c, priv_pem = client
    platform_ref, tenant_id = real_tenant
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]

    empty = c.get("/v1/channels", headers=_headers(priv_pem, platform_ref))
    assert empty.status_code == 200
    assert empty.json() == []

    channel_id = db_testsupport.insert_channel_account(
        dsn, tenant_id=tenant_id, type_="whatsapp_baileys", session_id=f"sess-{uuid.uuid4()}",
        status="connected", engine="ai_core",
    )

    listed = c.get("/v1/channels", headers=_headers(priv_pem, platform_ref))
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["id"] == str(channel_id)
    assert rows[0]["engine"] == "ai_core"
    assert rows[0]["status"] == "connected"


# --- POST /v1/channels/whatsapp ------------------------------------------


def test_create_whatsapp_channel_happy_path(client, real_tenant, gateway_state):
    c, priv_pem = client
    platform_ref, tenant_id = real_tenant
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    gateway_state["create_body"] = {
        "status": "QR_PENDING", "qr_image_base64": "FAKE_QR", "connected_phone_number": None,
    }

    resp = c.post(
        "/v1/channels/whatsapp",
        headers={**_headers(priv_pem, platform_ref), "Idempotency-Key": "create-1"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["type"] == "whatsapp_baileys"
    assert body["engine"] == "ai_core"
    assert body["status"] == "qr_pending"  # gateway's QR_PENDING mapped to the DB's lower_snake vocabulary
    assert body["phone_e164"] is None

    # Independent-of-the-app-code proof: the DB row really has these values,
    # and the gateway really received tenant_id/channel_account_id/engine.
    row = db_testsupport.fetch_channel_account_row(dsn, uuid.UUID(body["id"]))
    assert row is not None
    assert row["tenant_id"] == tenant_id
    assert row["engine"] == "ai_core"
    assert row["status"] == "qr_pending"

    assert len(gateway_state["requests"]) == 1
    method, path, sent_body = gateway_state["requests"][0]
    assert (method, path) == ("POST", "/sessions")
    assert sent_body["tenant_id"] == str(tenant_id)
    assert sent_body["channel_account_id"] == body["id"]
    assert sent_body["engine"] == "ai_core"
    assert sent_body["session_id"]  # server-generated, unpredictable, but must be present


def test_create_whatsapp_channel_requires_idempotency_key(client, real_tenant):
    c, priv_pem = client
    platform_ref, _ = real_tenant
    resp = c.post("/v1/channels/whatsapp", headers=_headers(priv_pem, platform_ref))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


def test_create_whatsapp_channel_rejects_non_merchant_admin(client, real_tenant):
    c, priv_pem = client
    platform_ref, _ = real_tenant
    resp = c.post(
        "/v1/channels/whatsapp",
        headers={**_headers(priv_pem, platform_ref, role="staff"), "Idempotency-Key": "create-staff"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN_ROLE"


def test_create_whatsapp_channel_idempotent_replay_returns_identical_response(
    client, real_tenant, gateway_state,
):
    c, priv_pem = client
    platform_ref, _ = real_tenant
    headers = {**_headers(priv_pem, platform_ref), "Idempotency-Key": "replay-key-1"}

    first = c.post("/v1/channels/whatsapp", headers=headers)
    assert first.status_code == 201, first.text

    second = c.post("/v1/channels/whatsapp", headers=headers)
    assert second.status_code == 201
    assert second.json() == first.json()

    # The replay must not have touched the gateway a second time.
    assert len(gateway_state["requests"]) == 1


def test_create_whatsapp_channel_same_key_different_stored_hash_is_rejected(client, real_tenant):
    """The route's own hashing is always sha256({}) since this endpoint takes
    no real request body - so a genuine "same key, different body" conflict
    can never arise through the HTTP API alone. This proves the conflict
    branch itself is correct by seeding a stale/mismatched hash directly,
    exactly as api_idempotency's own schema comment describes the case."""
    c, priv_pem = client
    platform_ref, tenant_id = real_tenant
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.insert_idempotency_record(
        dsn, tenant_id=tenant_id, idempotency_key="mismatched-key", request_hash="not-the-real-hash",
        response_status=201, response_body={"id": str(uuid.uuid4()), "type": "whatsapp_baileys",
                                             "engine": "ai_core", "status": "connected", "phone_e164": None},
    )
    resp = c.post(
        "/v1/channels/whatsapp",
        headers={**_headers(priv_pem, platform_ref), "Idempotency-Key": "mismatched-key"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


def test_create_whatsapp_channel_already_exists_for_second_active_channel(client, real_tenant):
    c, priv_pem = client
    platform_ref, _ = real_tenant

    first = c.post(
        "/v1/channels/whatsapp",
        headers={**_headers(priv_pem, platform_ref), "Idempotency-Key": "first-channel"},
    )
    assert first.status_code == 201, first.text

    second = c.post(
        "/v1/channels/whatsapp",
        headers={**_headers(priv_pem, platform_ref), "Idempotency-Key": "second-channel"},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "CHANNEL_ALREADY_EXISTS"


def test_create_whatsapp_channel_gateway_unavailable_maps_to_503(client, real_tenant, gateway_state):
    c, priv_pem = client
    platform_ref, _ = real_tenant
    gateway_state["create_status"] = 500
    gateway_state["create_body"] = {"error": "boom"}

    resp = c.post(
        "/v1/channels/whatsapp",
        headers={**_headers(priv_pem, platform_ref), "Idempotency-Key": "gw-down"},
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "GATEWAY_UNAVAILABLE"


# --- GET /v1/channels/{id} ------------------------------------------------


def test_get_channel_merges_gateway_health_fields(client, real_tenant, gateway_state):
    c, priv_pem = client
    platform_ref, tenant_id = real_tenant
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    channel_id = db_testsupport.insert_channel_account(
        dsn, tenant_id=tenant_id, type_="whatsapp_baileys", session_id=f"sess-{uuid.uuid4()}",
        status="connected", engine="ai_core",
    )
    gateway_state["health_body"] = {
        "reconnect_attempts": 3, "last_disconnect_reason": "timeout", "queue_depth": 7,
    }

    resp = c.get(f"/v1/channels/{channel_id}", headers=_headers(priv_pem, platform_ref))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(channel_id)
    assert body["reconnect_attempts"] == 3
    assert body["last_disconnect_reason"] == "timeout"
    assert body["queue_depth"] == 7


def test_get_channel_not_found_for_another_tenants_channel(client, real_tenant):
    c, priv_pem = client
    platform_ref, _ = real_tenant
    # A random UUID never inserted at all behaves identically to "belongs to
    # another tenant" - fetch_channel_account() returns None either way, and
    # the route must answer NOT_FOUND, never FORBIDDEN, in both cases.
    resp = c.get(f"/v1/channels/{uuid.uuid4()}", headers=_headers(priv_pem, platform_ref))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_get_channel_gateway_unavailable_maps_to_503(client, real_tenant, gateway_state):
    c, priv_pem = client
    platform_ref, tenant_id = real_tenant
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    channel_id = db_testsupport.insert_channel_account(
        dsn, tenant_id=tenant_id, type_="whatsapp_baileys", session_id=f"sess-{uuid.uuid4()}",
        status="connected", engine="ai_core",
    )
    gateway_state["health_status"] = 500

    resp = c.get(f"/v1/channels/{channel_id}", headers=_headers(priv_pem, platform_ref))
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "GATEWAY_UNAVAILABLE"


# --- GET /v1/channels/{id}/qr ---------------------------------------------


def test_get_channel_qr_returns_qr_and_no_store_header(client, real_tenant, gateway_state):
    c, priv_pem = client
    platform_ref, tenant_id = real_tenant
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    channel_id = db_testsupport.insert_channel_account(
        dsn, tenant_id=tenant_id, type_="whatsapp_baileys", session_id=f"sess-{uuid.uuid4()}",
        status="qr_pending", engine="ai_core",
    )
    gateway_state["qr_body"] = {"qr": "data:image/png;base64,ABC123"}

    resp = c.get(f"/v1/channels/{channel_id}/qr", headers=_headers(priv_pem, platform_ref))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"qr": "data:image/png;base64,ABC123"}
    assert resp.headers["Cache-Control"] == "no-store"


def test_get_channel_qr_not_found_for_unknown_channel(client, real_tenant):
    c, priv_pem = client
    platform_ref, _ = real_tenant
    resp = c.get(f"/v1/channels/{uuid.uuid4()}/qr", headers=_headers(priv_pem, platform_ref))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


# --- POST /v1/channels/{id}/reconnect -------------------------------------


def test_reconnect_requires_merchant_admin_role(client, real_tenant):
    c, priv_pem = client
    platform_ref, tenant_id = real_tenant
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    channel_id = db_testsupport.insert_channel_account(
        dsn, tenant_id=tenant_id, type_="whatsapp_baileys", session_id=f"sess-{uuid.uuid4()}",
        status="disconnected", engine="ai_core",
    )
    resp = c.post(
        f"/v1/channels/{channel_id}/reconnect",
        headers=_headers(priv_pem, platform_ref, role="staff"),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN_ROLE"


def test_reconnect_rejects_when_status_not_eligible(client, real_tenant):
    c, priv_pem = client
    platform_ref, tenant_id = real_tenant
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    channel_id = db_testsupport.insert_channel_account(
        dsn, tenant_id=tenant_id, type_="whatsapp_baileys", session_id=f"sess-{uuid.uuid4()}",
        status="connected", engine="ai_core",
    )
    resp = c.post(f"/v1/channels/{channel_id}/reconnect", headers=_headers(priv_pem, platform_ref))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CHANNEL_CONFLICT"


@pytest.mark.parametrize("status", ["conflict", "disconnected", "logged_out"])
def test_reconnect_happy_path_for_each_eligible_status(client, real_tenant, gateway_state, status):
    c, priv_pem = client
    platform_ref, tenant_id = real_tenant
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    channel_id = db_testsupport.insert_channel_account(
        dsn, tenant_id=tenant_id, type_="whatsapp_baileys", session_id=f"sess-{uuid.uuid4()}",
        status=status, engine="ai_core",
    )
    resp = c.post(f"/v1/channels/{channel_id}/reconnect", headers=_headers(priv_pem, platform_ref))
    assert resp.status_code == 200, resp.text
    assert len(gateway_state["requests"]) == 1
    method, path, _ = gateway_state["requests"][0]
    assert method == "POST"
    assert path.endswith("/reconnect")


def test_reconnect_not_found_for_unknown_channel(client, real_tenant):
    c, priv_pem = client
    platform_ref, _ = real_tenant
    resp = c.post(f"/v1/channels/{uuid.uuid4()}/reconnect", headers=_headers(priv_pem, platform_ref))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_reconnect_is_rate_limited_on_second_call(client, real_tenant, gateway_state):
    c, priv_pem = client
    platform_ref, tenant_id = real_tenant
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    channel_id = db_testsupport.insert_channel_account(
        dsn, tenant_id=tenant_id, type_="whatsapp_baileys", session_id=f"sess-{uuid.uuid4()}",
        status="disconnected", engine="ai_core",
    )
    headers = _headers(priv_pem, platform_ref)

    first = c.post(f"/v1/channels/{channel_id}/reconnect", headers=headers)
    assert first.status_code == 200, first.text

    second = c.post(f"/v1/channels/{channel_id}/reconnect", headers=headers)
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "RATE_LIMITED"
    assert second.json()["error"]["retry_after_s"] == 30

    # Clean up the rate-limit key so other tests in this module (which reuse
    # the same real redis-cache instance) never see a stale RATE_LIMITED.
    r = redis.Redis(host="127.0.0.1", port=6390, password="test-redis-pw", decode_responses=True)
    r.delete(f"reconnect_rl:{channel_id}")


def test_reconnect_gateway_unavailable_maps_to_503(client, real_tenant, gateway_state):
    c, priv_pem = client
    platform_ref, tenant_id = real_tenant
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    channel_id = db_testsupport.insert_channel_account(
        dsn, tenant_id=tenant_id, type_="whatsapp_baileys", session_id=f"sess-{uuid.uuid4()}",
        status="disconnected", engine="ai_core",
    )
    gateway_state["reconnect_status"] = 500

    resp = c.post(f"/v1/channels/{channel_id}/reconnect", headers=_headers(priv_pem, platform_ref))
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "GATEWAY_UNAVAILABLE"
