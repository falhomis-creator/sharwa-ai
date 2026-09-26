"""JWT acceptance tests (spec: "JWT (منتهٍ/موقّع خطأً/alg خاطئ/kid مجهول)").
Real RSA keypairs generated per spec ("الاختبارات تولّد زوج مفاتيح RSA"), and a
real local HTTP server serving a real JWKS document - not a mocked HTTP client.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import JwtConfig
from app.security.jwt import TokenError, verify_token

ISSUER = "https://sso.sharwa.test"
AUDIENCE = "sharwa-ai-core"


def _gen_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_private = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pem_public = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return key, pem_private, pem_public


def _jwk_from_public_key(pub_key, kid: str) -> dict:
    numbers = pub_key.public_numbers()
    def b64(n: int) -> str:
        import base64
        raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return {
        "kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid,
        "n": b64(numbers.n), "e": b64(numbers.e),
    }


@pytest.fixture(scope="module")
def real_jwks_server():
    """A real, running HTTP server serving a real JWKS document (one real key,
    kid='real-kid') - not a mocked httpx transport."""
    key, priv_pem, _pub_pem = _gen_keypair()
    jwk = _jwk_from_public_key(key.public_key(), "real-kid")
    body = json.dumps({"keys": [jwk]}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # silence
            return None

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}/.well-known/jwks.json", key, priv_pem
    server.shutdown()


def _make_token(
    priv_pem: str, kid: str, *,
    claims_override: dict | None = None, alg: str = "RS256", exp_delta: int = 3600,
):
    now = int(time.time())
    claims = {
        "iss": ISSUER, "aud": AUDIENCE, "sub": "user-1", "tenant": "tenant-a",
        "role": "merchant_admin", "iat": now, "exp": now + exp_delta,
    }
    if claims_override:
        claims.update(claims_override)
    return pyjwt.encode(claims, priv_pem, algorithm=alg, headers={"kid": kid})


def test_valid_token_via_real_jwks(real_jwks_server):
    jwks_url, _key, priv_pem = real_jwks_server
    cfg = JwtConfig(issuer=ISSUER, audience=AUDIENCE, jwks_url=jwks_url, public_key_pem=None)
    token = _make_token(priv_pem, "real-kid")
    principal = verify_token(token, cfg)
    assert principal.sub == "user-1"
    assert principal.platform_ref == "tenant-a"
    assert principal.role == "merchant_admin"


def test_expired_token_rejected(real_jwks_server):
    jwks_url, _key, priv_pem = real_jwks_server
    cfg = JwtConfig(issuer=ISSUER, audience=AUDIENCE, jwks_url=jwks_url, public_key_pem=None)
    token = _make_token(priv_pem, "real-kid", exp_delta=-3600 - 60)  # well past the 30s leeway
    with pytest.raises(TokenError) as exc:
        verify_token(token, cfg)
    assert exc.value.code == "TOKEN_EXPIRED"


def test_wrong_signature_rejected(real_jwks_server):
    jwks_url, _key, _priv_pem = real_jwks_server
    _, wrong_priv_pem, _ = _gen_keypair()
    cfg = JwtConfig(issuer=ISSUER, audience=AUDIENCE, jwks_url=jwks_url, public_key_pem=None)
    # Header claims kid='real-kid' (so JWKS resolves the REAL public key), but the
    # token was actually signed with a DIFFERENT private key -> signature mismatch.
    token = _make_token(wrong_priv_pem, "real-kid")
    with pytest.raises(TokenError) as exc:
        verify_token(token, cfg)
    assert exc.value.code == "UNAUTHENTICATED"


def test_wrong_alg_none_rejected(real_jwks_server):
    jwks_url, _key, _priv_pem = real_jwks_server
    cfg = JwtConfig(issuer=ISSUER, audience=AUDIENCE, jwks_url=jwks_url, public_key_pem=None)
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "user-1", "tenant": "tenant-a",
              "role": "merchant_admin", "iat": now, "exp": now + 3600}
    forged = pyjwt.encode(claims, "", algorithm="none")
    with pytest.raises(TokenError) as exc:
        verify_token(forged, cfg)
    assert exc.value.code == "UNAUTHENTICATED"


def test_wrong_alg_hs256_rejected(real_jwks_server):
    jwks_url, _key, _priv_pem = real_jwks_server
    cfg = JwtConfig(issuer=ISSUER, audience=AUDIENCE, jwks_url=jwks_url, public_key_pem=None)
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "user-1", "tenant": "tenant-a",
              "role": "merchant_admin", "iat": now, "exp": now + 3600}
    forged = pyjwt.encode(claims, "some-shared-secret-guessed-from-somewhere", algorithm="HS256")
    with pytest.raises(TokenError) as exc:
        verify_token(forged, cfg)
    assert exc.value.code == "UNAUTHENTICATED"


def test_unknown_kid_falls_back_to_pem(real_jwks_server):
    jwks_url, _key, _priv_pem = real_jwks_server
    # Fallback PEM is a SEPARATE keypair; a token signed by IT with an unknown kid
    # must still verify via the PEM fallback path once JWKS lookup fails for that kid.
    _, fallback_priv_pem, fallback_pub_pem = _gen_keypair()
    cfg = JwtConfig(issuer=ISSUER, audience=AUDIENCE, jwks_url=jwks_url, public_key_pem=fallback_pub_pem)
    token = _make_token(fallback_priv_pem, "unknown-kid-not-in-jwks")
    principal = verify_token(token, cfg)
    assert principal.sub == "user-1"


def test_wrong_issuer_rejected(real_jwks_server):
    jwks_url, _key, priv_pem = real_jwks_server
    cfg = JwtConfig(issuer=ISSUER, audience=AUDIENCE, jwks_url=jwks_url, public_key_pem=None)
    token = _make_token(priv_pem, "real-kid", claims_override={"iss": "https://evil.example"})
    with pytest.raises(TokenError) as exc:
        verify_token(token, cfg)
    assert exc.value.code == "UNAUTHENTICATED"


def test_wrong_audience_rejected(real_jwks_server):
    jwks_url, _key, priv_pem = real_jwks_server
    cfg = JwtConfig(issuer=ISSUER, audience=AUDIENCE, jwks_url=jwks_url, public_key_pem=None)
    token = _make_token(priv_pem, "real-kid", claims_override={"aud": "some-other-service"})
    with pytest.raises(TokenError) as exc:
        verify_token(token, cfg)
    assert exc.value.code == "UNAUTHENTICATED"


def test_missing_role_claim_rejected(real_jwks_server):
    jwks_url, _key, priv_pem = real_jwks_server
    cfg = JwtConfig(issuer=ISSUER, audience=AUDIENCE, jwks_url=jwks_url, public_key_pem=None)
    now = int(time.time())
    claims = {
        "iss": ISSUER, "aud": AUDIENCE, "sub": "user-1", "tenant": "tenant-a", "iat": now, "exp": now + 3600,
    }
    token = pyjwt.encode(claims, priv_pem, algorithm="RS256", headers={"kid": "real-kid"})
    with pytest.raises(TokenError) as exc:
        verify_token(token, cfg)
    assert exc.value.code == "UNAUTHENTICATED"


def test_unknown_role_value_rejected(real_jwks_server):
    jwks_url, _key, priv_pem = real_jwks_server
    cfg = JwtConfig(issuer=ISSUER, audience=AUDIENCE, jwks_url=jwks_url, public_key_pem=None)
    token = _make_token(priv_pem, "real-kid", claims_override={"role": "superuser"})
    with pytest.raises(TokenError) as exc:
        verify_token(token, cfg)
    assert exc.value.code == "FORBIDDEN_ROLE"


def test_clock_skew_leeway_allows_slightly_expired(real_jwks_server):
    jwks_url, _key, priv_pem = real_jwks_server
    cfg = JwtConfig(issuer=ISSUER, audience=AUDIENCE, jwks_url=jwks_url, public_key_pem=None)
    # 15s past exp - within the 30s leeway spec calls for
    token = _make_token(priv_pem, "real-kid", exp_delta=-15)
    principal = verify_token(token, cfg)
    assert principal.sub == "user-1"
