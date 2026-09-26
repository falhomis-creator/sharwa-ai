"""core/app/api/deps.py - FastAPI dependencies: authenticate the request,
resolve tenant, hand back a request-scoped tenant_tx() connection. This is
the ONLY place a route ever gets a tenant_id from - never a path/query/body
parameter (H2)."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, Header, Request

from app import db as core_db
from app.api.errors import ApiError
from app.config import Settings
from app.db import repos
from app.security.jwt import Principal, TokenError, verify_token


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


@dataclass(frozen=True)
class AuthContext:
    principal: Principal
    tenant_id: uuid.UUID


def authenticate(
    request: Request,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError("UNAUTHENTICATED")
    token = authorization[len("Bearer "):]
    try:
        principal = verify_token(token, settings.jwt)
    except TokenError as exc:
        raise ApiError(exc.code) from exc

    with core_db.system_tx() as conn:
        resolved = repos.resolve_tenant(conn, principal.platform_ref)
    if resolved is None:
        raise ApiError("NOT_FOUND")
    tenant_id, status = resolved
    if status == "suspended":
        raise ApiError("TENANT_SUSPENDED")

    request.state.tenant_id = str(tenant_id)
    request.state.principal_sub = principal.sub
    request.state.principal_role = principal.role
    return AuthContext(principal=principal, tenant_id=tenant_id)


def require_role(*allowed: str) -> Callable[[AuthContext], AuthContext]:
    def _check(ctx: AuthContext = Depends(authenticate)) -> AuthContext:
        if ctx.principal.role not in allowed:
            raise ApiError("FORBIDDEN_ROLE")
        return ctx
    return _check
