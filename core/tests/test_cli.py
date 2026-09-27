"""Real tests for app.cli: create-tenant inserts a REAL row (verified by
reading it back with a plain query), and issue-dev-token mints a REAL RS256
JWT that a genuine pyjwt.decode() against the matching public key accepts -
and is REFUSED outright when ENV=production (H-safety)."""
from __future__ import annotations

import os
import uuid

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app import cli
from app.db import testsupport as db_testsupport


def _migration_dsn() -> str:
    return os.environ["CORE_MIGRATION_DATABASE_URL"]


@pytest.fixture()
def rsa_keyfile(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_path = tmp_path / "dev.pem"
    priv_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv_path, pub_pem


@pytest.mark.db
def test_create_tenant_inserts_a_real_row(capsys):
    platform_ref = f"cli-test-{uuid.uuid4()}"
    rc = cli.main([
        "create-tenant", "--platform-ref", platform_ref,
        "--name", "CLI Test Tenant", "--currency", "SAR",
    ])
    assert rc == 0
    printed_id = capsys.readouterr().out.strip()
    tenant_id = uuid.UUID(printed_id)  # must be a real, parseable UUID

    row = db_testsupport.fetch_tenant_row(_migration_dsn(), tenant_id)
    assert row == (platform_ref, "CLI Test Tenant", "SAR", "active")

    db_testsupport.delete_tenant(_migration_dsn(), tenant_id)


def test_issue_dev_token_mints_a_real_verifiable_rs256_jwt(rsa_keyfile, capsys):
    priv_path, pub_pem = rsa_keyfile
    rc = cli.main([
        "issue-dev-token",
        "--private-key-path", str(priv_path),
        "--issuer", "https://sso.sharwa.test",
        "--audience", "sharwa-ai-core",
        "--tenant", "some-platform-ref",
        "--role", "merchant_admin",
        "--kid", "dev-kid",
        "--ttl-s", "120",
    ])
    assert rc == 0
    token = capsys.readouterr().out.strip()

    # Real, independent verification - not a re-use of app.security.jwt.
    claims = pyjwt.decode(
        token, pub_pem, algorithms=["RS256"],
        issuer="https://sso.sharwa.test", audience="sharwa-ai-core",
    )
    assert claims["tenant"] == "some-platform-ref"
    assert claims["role"] == "merchant_admin"
    header = pyjwt.get_unverified_header(token)
    assert header["kid"] == "dev-kid"
    assert header["alg"] == "RS256"


def test_issue_dev_token_refused_when_env_production(rsa_keyfile, monkeypatch, capsys):
    priv_path, _ = rsa_keyfile
    monkeypatch.setenv("ENV", "production")
    rc = cli.main([
        "issue-dev-token",
        "--private-key-path", str(priv_path),
        "--issuer", "https://sso.sharwa.test",
        "--audience", "sharwa-ai-core",
        "--tenant", "some-platform-ref",
        "--role", "merchant_admin",
    ])
    assert rc == 1
    out = capsys.readouterr()
    assert "production" in out.err
    assert out.out.strip() == ""  # never prints a token when refusing
