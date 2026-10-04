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
    # P1.3 inbox (PROMPT §5.6)
    "STAFF_NOT_PROVISIONED", "FORBIDDEN_PERMISSION", "CONVERSATION_CLOSED",
    "CONFLICT_VERSION", "IDEMPOTENCY_KEY_REQUIRED", "IDEMPOTENCY_KEY_REUSED",
    # P1.3b WebSocket (PROMPT §4.7): ticket issuance failed server-side.
    "TICKET_ISSUE_FAILED",
    # P1.4 catalog webhook (PROMPT §7).
    "PLATFORM_SIGNATURE_INVALID", "PLATFORM_TIMESTAMP_SKEW", "PLATFORM_PAYLOAD_TOO_LARGE",
    # P3.5 admin dashboard (H104): a precondition the operator can fix (the machine
    # list of failed preflight checks travels in error.details.failed).
    "PRECONDITION_FAILED",
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
    "STAFF_NOT_PROVISIONED": 403,
    "FORBIDDEN_PERMISSION": 403,
    "CONVERSATION_CLOSED": 409,
    "CONFLICT_VERSION": 409,
    "IDEMPOTENCY_KEY_REQUIRED": 400,
    "IDEMPOTENCY_KEY_REUSED": 409,
    "TICKET_ISSUE_FAILED": 503,
    "PLATFORM_SIGNATURE_INVALID": 401,
    "PLATFORM_TIMESTAMP_SKEW": 401,
    "PLATFORM_PAYLOAD_TOO_LARGE": 413,
    "PRECONDITION_FAILED": 412,
}


class ApiError(Exception):
    def __init__(
        self, code: str, *, retry_after_s: int | None = None,
        details: dict[str, Any] | None = None,
    ):
        if code not in ERROR_CODES:
            raise ValueError(f"unknown error code {code!r} - add it to ERROR_CODES first")
        self.code = code
        self.retry_after_s = retry_after_s
        # Machine-only closed-vocabulary data (e.g. {"failed": ["warmup_too_young"]}) -
        # never free text, never an exception message.
        self.details = details
        super().__init__(code)


def error_body(
    code: str, request_id: str, *, retry_after_s: int | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "request_id": request_id}}
    if retry_after_s is not None:
        body["error"]["retry_after_s"] = retry_after_s
    if details is not None:
        body["error"]["details"] = details
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
        content=error_body(exc.code, request_id, retry_after_s=exc.retry_after_s, details=exc.details),
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
