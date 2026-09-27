"""core/app/channels/gateway_client.py

Thin httpx client to the gateway's own HTTP API, with the 3s timeout and a
simple circuit breaker the spec requires ("مهلة 3 ثوانٍ لاستدعاءات البوابة مع
قاطع دائرة بسيط"). Breaker: opens after N consecutive failures, refuses calls
(fast-fails with GatewayUnavailableError, no network attempt) for a cooldown
window, then allows one trial call (half-open) - the classic pattern, kept
deliberately small (H4: every such state machine here is bounded and simple).
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import GatewayConfig


class GatewayUnavailableError(Exception):
    """Raised when the gateway is unreachable, times out, or returns 5xx -
    also raised directly (no network attempt) while the circuit breaker is
    open."""


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
            raise GatewayUnavailableError("circuit breaker open")
        # half-open: allow exactly one trial call through
        self._opened_at = None

    def on_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None

    def on_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.fail_threshold and self._opened_at is None:
            self._opened_at = time.monotonic()


class GatewayClient:
    def __init__(self, cfg: GatewayConfig, *, api_key: str):
        self._cfg = cfg
        self._breaker = _Breaker(cfg.breaker_fail_threshold, cfg.breaker_reset_s)
        self._client = httpx.Client(
            base_url=cfg.base_url, timeout=cfg.timeout_s,
            headers={"X-API-Key": api_key},
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        self._breaker.before_call()
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            self._breaker.on_failure()
            raise GatewayUnavailableError(str(exc)) from exc
        if resp.status_code >= 500:
            self._breaker.on_failure()
            raise GatewayUnavailableError(f"gateway returned {resp.status_code}")
        self._breaker.on_success()
        return resp

    def healthz(self) -> bool:
        try:
            resp = self._request("GET", "/healthz")
            return resp.status_code == 200
        except GatewayUnavailableError:
            return False

    def create_session(
        self, *, session_id: str, tenant_id: str, channel_account_id: str, engine: str,
    ) -> httpx.Response:
        return self._request("POST", "/sessions", json={
            "session_id": session_id, "tenant_id": tenant_id,
            "channel_account_id": channel_account_id, "engine": engine,
        })

    def session_health(self, session_id: str) -> httpx.Response:
        return self._request("GET", f"/sessions/{session_id}/health")

    def session_qr(self, session_id: str) -> httpx.Response:
        return self._request("GET", f"/sessions/{session_id}/qr")

    def session_reconnect(self, session_id: str) -> httpx.Response:
        return self._request("POST", f"/sessions/{session_id}/reconnect")

    def session_stats(self, session_id: str) -> httpx.Response:
        return self._request("GET", f"/sessions/{session_id}/stats")

    def send(
        self,
        *,
        session_id: str,
        to: str,
        text: str,
        client_msg_id: str | None = None,
        kind: str = "interactive",
    ) -> httpx.Response:
        """POST /sessions/:id/send (gateway/src/index.js:180-237, §5.1).

        client_msg_id MUST be the outbox.idempotency_key itself - that is what
        makes a retried dispatch idempotent at the gateway (H22). kind is
        'marketing' for message_class='marketing', else 'interactive'."""
        body: dict[str, str] = {"to": to, "text": text, "kind": kind}
        if client_msg_id is not None:
            body["client_msg_id"] = client_msg_id
        return self._request("POST", f"/sessions/{session_id}/send", json=body)
