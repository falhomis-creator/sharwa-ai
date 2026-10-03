"""app/policy/health.py - number-health classification (H81: down fast, up slow).

Honesty note (OQ-P3-01): the gateway does not produce delivered/read receipts, so
the REAL delivery rate is unmeasured. Health here is derived ONLY from (gateway
delivery failures · post-marketing opt-outs · complaint words · channel state)
and claims nothing more. `paused` is never exited by classify - only a human via
`policy reinstate` (H81).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HealthSignals:
    marketing_sends: int
    marketing_failures: int
    optouts_after_marketing: int
    complaints: int
    channel_status: str | None  # banned | logged_out | conflict | None


@dataclass(frozen=True)
class HealthConfig:
    throttle_optout_rate: float = 0.02
    throttle_failure_rate: float = 0.10
    throttle_complaints: int = 1
    pause_optout_rate: float = 0.05
    pause_failure_rate: float = 0.30
    pause_complaints: int = 3
    optout_sample_min: int = 30
    failure_sample_min: int = 20


@dataclass(frozen=True)
class HealthVerdict:
    state: str  # healthy | throttled | paused
    reason: str


def classify(signals: HealthSignals, cfg: HealthConfig) -> HealthVerdict:
    """Down fast (pause before throttle), sample-floored, deterministic."""
    if signals.channel_status in ("banned", "logged_out", "conflict"):
        return HealthVerdict("paused", f"channel_{signals.channel_status}")

    optout_rate = _rate(signals.optouts_after_marketing, signals.marketing_sends)
    failure_rate = _rate(signals.marketing_failures, signals.marketing_sends)

    if signals.marketing_sends >= cfg.optout_sample_min and optout_rate >= cfg.pause_optout_rate:
        return HealthVerdict("paused", "optout_rate")
    if signals.marketing_sends >= cfg.failure_sample_min and failure_rate >= cfg.pause_failure_rate:
        return HealthVerdict("paused", "failure_rate")
    if signals.complaints >= cfg.pause_complaints:
        return HealthVerdict("paused", "complaints")

    if signals.marketing_sends >= cfg.optout_sample_min and optout_rate >= cfg.throttle_optout_rate:
        return HealthVerdict("throttled", "optout_rate")
    if signals.marketing_sends >= cfg.failure_sample_min and failure_rate >= cfg.throttle_failure_rate:
        return HealthVerdict("throttled", "failure_rate")
    if signals.complaints >= cfg.throttle_complaints:
        return HealthVerdict("throttled", "complaints")

    return HealthVerdict("healthy", "")


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator > 0 else 0.0
