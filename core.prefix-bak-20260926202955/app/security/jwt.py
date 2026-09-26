"""core/app/security/jwt.py

RS256-only JWT verification per spec: reject `none`/HS*, verify iss/aud/exp
(30s clock skew), resolve `kid` via JWKS with a cache and fallback to
JWT_PUBLIC_KEY_PEM. Never accepts tenant_id from anywhere except the token's
own `tenant` claim, resolved server-side via app.resolve_tenant (H2).
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jwt
from jwt import PyJWKClient

from app.config import JwtConfig

ALLOWED_ROLES = ("merchant_admin", "staff", "platform_admin")


class TokenError(Exception):
    """Raised with a stable `code` matching the API error catalog - the HTTP
    layer maps this directly to the error envelope, never a raw exception."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class Principal:
    sub: str
    # the token's `tenant` claim - resolved to tenants.id later, never trusted directly as an id
    platform_ref: str
    role: str


class _JwksCache:
    def __init__(self, jwks_url: str):
        self._client = PyJWKClient(jwks_url, cache_keys=True, lifespan=300)

    def signing_key_for(self, token: str) -> Any:
        return self._client.get_signing_key_from_jwt(token).key


def _build_key_resolver(cfg: JwtConfig) -> Callable[[str], Any]:
    jwks = _JwksCache(cfg.jwks_url) if cfg.jwks_url else None

    def resolve(token: str) -> Any:
        if jwks is not None:
            try:
                return jwks.signing_key_for(token)
            except Exception as exc:  # any JWKS failure falls back to the static PEM below, if configured
                if not cfg.public_key_pem:
                    raise TokenError(
                        "UNAUTHENTICATED",
                        "unable to resolve signing key (JWKS unreachable, no fallback configured)",
                    ) from exc
        if cfg.public_key_pem:
            return cfg.public_key_pem
        raise TokenError("UNAUTHENTICATED", "no signing key source configured")

    return resolve


def verify_token(token: str, cfg: JwtConfig, *, now: float | None = None) -> Principal:
    now = now if now is not None else time.time()

    # Reject alg=none / HS* before any key resolution - PyJWT's own algorithms=
    # allowlist below already refuses these, but decoding the unverified header
    # first lets us return a stable, specific error code rather than PyJWT's
    # generic InvalidAlgorithmError message leaking into a user-facing string.
    try:
        header = jwt.get_unverified_header(token)
    except Exception as exc:
        raise TokenError("UNAUTHENTICATED", "malformed token") from exc

    alg = header.get("alg", "")
    if alg != "RS256":
        raise TokenError("UNAUTHENTICATED", f"algorithm {alg!r} is not permitted (RS256 only)")

    resolve_key = _build_key_resolver(cfg)
    try:
        key = resolve_key(token)
    except TokenError:
        raise
    except Exception as exc:
        raise TokenError("UNAUTHENTICATED", "unable to resolve signing key") from exc

    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            issuer=cfg.issuer,
            audience=cfg.audience,
            leeway=cfg.clock_skew_s,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("TOKEN_EXPIRED", "token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("UNAUTHENTICATED", f"invalid token: {exc}") from exc

    sub = claims.get("sub")
    tenant = claims.get("tenant")
    role = claims.get("role")
    if not sub or not tenant or not role:
        raise TokenError("UNAUTHENTICATED", "token is missing required claims (sub/tenant/role)")
    if role not in ALLOWED_ROLES:
        raise TokenError("FORBIDDEN_ROLE", f"unknown role {role!r}")

    return Principal(sub=sub, platform_ref=tenant, role=role)
