"""app/policy/types.py - frozen dataclasses for the send policy (H84)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

DROP = "drop"
DEFER = "defer"
OK = "ok"

# Closed reason codes - metric labels stay bounded (H20/H84).
DROP_REASONS = frozenset({
    "unknown_template", "expired", "kill_switch_off", "conversation_closed",
    "suppressed", "no_consent", "no_prior_interaction", "frequency_cap_skip",
    "marketing_not_enabled",   # P3.4 (H100): the tenant has not been enabled for marketing
})
DEFER_REASONS = frozenset({
    "channel_not_connected", "number_paused", "human_active", "active_chat",
    "quiet_hours", "frequency_cap_defer", "cap_reached", "spacing",
    "no_health_row", "policy_error", "invalid_timezone",
    "canary_cap_reached",      # P3.4 (H102): the tenant's per-day canary cap is spent
})
REASONS = DROP_REASONS | DEFER_REASONS | {"ok"}


@dataclass(frozen=True)
class TemplateMeta:
    template_id: str
    message_class: str          # utility | marketing (H77: derived, never passed)
    consent_scope: str          # back_in_stock | marketing | review_request
    capability: str             # the kill-switch capability
    quiet_hours: bool
    footer_required: bool


@dataclass(frozen=True)
class PolicyInput:
    """A pre-read snapshot; `now` is INJECTED (H84: the clock is an input)."""
    template: TemplateMeta | None
    now: datetime
    created_at: datetime
    ttl_hours: float
    kill_switch_state: str | None
    conversation_closed: bool
    suppressed: bool
    has_consent: bool
    has_prior_interaction: bool
    channel_connected: bool
    number_paused: bool
    human_active: bool
    active_chat: bool
    quiet_hours: bool
    marketing_24h: int
    marketing_7d: int
    utility_24h: int
    per_24h_marketing: int
    per_7d_marketing: int
    per_24h_utility: int
    # P3.4 (H100-H102). Defaults are FAIL-CLOSED: a caller that forgets to pass
    # them gets "marketing not enabled", never an accidental enablement.
    marketing_enabled: bool = False
    tenant_marketing_24h: int = 0
    canary_cap: int = 0


@dataclass(frozen=True)
class Verdict:
    action: str                 # drop | defer | ok
    reason: str                 # closed REASONS
    defer_until: datetime | None = None

    def __post_init__(self) -> None:
        if self.action not in (DROP, DEFER, OK):
            raise ValueError(f"action={self.action!r} outside closed set")
        if self.reason not in REASONS:
            raise ValueError(f"reason={self.reason!r} outside closed set")
        if self.action == OK and self.reason != "ok":
            raise ValueError("ok verdict must carry reason='ok'")
        if self.action == DROP and self.reason not in DROP_REASONS:
            raise ValueError(f"drop verdict with non-drop reason {self.reason!r}")
        if self.action == DEFER and self.reason not in DEFER_REASONS:
            raise ValueError(f"defer verdict with non-defer reason {self.reason!r}")
