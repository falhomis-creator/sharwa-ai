"""core/app/channels/commerce_client.py - the single outbound HTTP client to the
sharwa_saas Commerce API (C3/C7).

Follows gateway_client.py's pattern exactly (H24: only channels may reach the
network): an explicit timeout, a simple circuit breaker, and error
classification. 5xx / timeout / connection errors raise CommerceUnavailableError
(transient - the breaker opens and the reconcile loop backs off); a non-2xx that
is NOT a 5xx raises CommerceClientError (permanent - no retry makes sense). The
adapter in app/commerce/ uses this client; nothing else in core/ imports httpx.
"""
from __future__ import annotations

import time
from typing import Any

import httpx


class CommerceUnavailableError(Exception):
    """Platform Commerce API unreachable, timed out, or returned 5xx (transient)."""


class CommerceClientError(Exception):
    """Platform Commerce API returned a permanent (non-5xx) error."""


class _Breaker:
    def __init__(self, fail_threshold: int, reset_s: float):
        self.fail_threshold = fail_threshold
        self.reset_s = reset_s
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    def before_call(self) -> None:
        if self._opened_at is None:
            return
        if time.monotonic() - self._opened_at < self.reset_s:
            raise CommerceUnavailableError("circuit breaker open")
        self._opened_at = None

    def on_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None

    def on_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.fail_threshold and self._opened_at is None:
            self._opened_at = time.monotonic()


class CommerceClient:
    def __init__(self, base_url: str, *, timeout_s: float = 3.0):
        self._breaker = _Breaker(fail_threshold=5, reset_s=30.0)
        self._client = httpx.Client(base_url=base_url, timeout=timeout_s)

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        self._breaker.before_call()
        try:
            resp = self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            self._breaker.on_failure()
            raise CommerceUnavailableError(str(exc)) from exc
        if resp.status_code >= 500:
            self._breaker.on_failure()
            raise CommerceUnavailableError(f"commerce API returned {resp.status_code}")
        self._breaker.on_success()
        if resp.status_code != 200:
            raise CommerceClientError(f"commerce API returned {resp.status_code}")
        return resp.json()

    def get_changes(self, tenant_ref: str, since: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {"tenant_ref": tenant_ref}
        if since is not None:
            params["since"] = since
        return self._get("/changes", params)

    def get_snapshot(self, tenant_ref: str, page: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {"tenant_ref": tenant_ref}
        if page is not None:
            params["page"] = page
        return self._get("/snapshot", params)
