"""core/app/api/errors.py - the machine-only error envelope (spec §"غلاف
الأخطاء"): {"error":{"code","request_id","retry_after_s"?}}. No free text, no
raw exception, no stack, ever, in an HTTP response body."""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

ERROR_CODES = (
    "UNAUTHENTICATED", "TOKEN_EXPIRED", "FORBIDDEN_ROLE", "TENANT_SUSPENDED",
    "NOT_FOUND", "CHANNEL_ALREADY_EXISTS", "GATEWAY_UNAVAILABLE",
    "CHANNEL_CONFLICT", "CHANNEL_LOGGED_OUT", "CHANNEL_BANNED",
    "RATE_LIMITED", "SWITCH_LOCKED_BY_ADMIN", "VALIDATION_FAILED", "INTERNAL",
)

_STATUS_BY_CODE: dict[str, int] = {
    "UNAUTHENTICATED": 401,
    "TOKEN_EXPIRED": 401,
    "FORBIDDEN_ROLE": 403,
    "TENANT_SUSPENDED": 403,
    "NOT_FOUND": 404,
    "CHANNEL_ALREADY_EXISTS": 409,
    "GATEWAY_UNAVAILABLE": 503,
    "CHANNEL_CONFLICT": 409,
    "CHANNEL_LOGGED_OUT": 409,
    "CHANNEL_BANNED": 403,
    "RATE_LIMITED": 429,
    "SWITCH_LOCKED_BY_ADMIN": 403,
    "VALIDATION_FAILED": 422,
    "INTERNAL": 500,
}


class ApiError(Exception):
    def __init__(self, code: str, *, retry_after_s: int | None = None):
        if code not in ERROR_CODES:
            raise ValueError(f"unknown error code {code!r} - add it to ERROR_CODES first")
        self.code = code
        self.retry_after_s = retry_after_s
        super().__init__(code)


def error_body(code: str, request_id: str, *, retry_after_s: int | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "request_id": request_id}}
    if retry_after_s is not None:
        body["error"]["retry_after_s"] = retry_after_s
    return body


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Registered via app.add_exception_handler(ApiError, api_error_handler) - the
    # framework only ever calls this with an ApiError instance, but Starlette's
    # own ExceptionHandler type is Callable[[Request, Exception], ...] (handlers
    # are looked up by class and the parameter is necessarily contravariant), so
    # the signature here has to accept the base Exception type too.
    if not isinstance(exc, ApiError):
        raise TypeError(f"api_error_handler registered for a non-ApiError exception: {exc!r}")
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=_STATUS_BY_CODE[exc.code],
        content=error_body(exc.code, request_id, retry_after_s=exc.retry_after_s),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """H3: nothing is ever swallowed silently or leaked raw to the client -
    every unhandled exception becomes INTERNAL with a request_id the operator
    can correlate against real logs, never the exception's own message/stack
    (U3: no technical jargon, ever, in what a merchant can see)."""
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=500,
        content=error_body("INTERNAL", request_id),
    )
