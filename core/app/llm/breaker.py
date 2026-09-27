"""core/app/llm/breaker.py - per-provider circuit breaker (L5).

closed -> open -> half_open, in-process state. open => no call => the turn walks
the deterministic templates path (never silence, H39). Thresholds are written:
LLM_BREAKER_FAIL_THRESHOLD (default 5), LLM_BREAKER_RESET_S (default 60). State
is in-process memory - acceptable and declared for a single worker process
(recorded in P1_OPEN_QUESTIONS.md).
"""
from __future__ import annotations

import time
from enum import Enum


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, fail_threshold: int, reset_s: float) -> None:
        self.fail_threshold = fail_threshold
        self.reset_s = reset_s
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    def allow(self) -> bool:
        """True when a call may proceed (closed, or a half-open trial call)."""
        if self._opened_at is None:
            return True
        if time.monotonic() - self._opened_at >= self.reset_s:
            # half-open: allow exactly one trial call.
            self._opened_at = None
            return True
        return False

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.fail_threshold and self._opened_at is None:
            self._opened_at = time.monotonic()

    def state(self) -> BreakerState:
        if self._opened_at is None:
            return BreakerState.CLOSED
        if time.monotonic() - self._opened_at < self.reset_s:
            return BreakerState.OPEN
        return BreakerState.HALF_OPEN
