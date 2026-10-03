"""app/policy/decision.py - the pure, deterministic, reasoned send decision (H84).

Evaluation order (tested as a table): every permanent DROP before any DEFER - a
row doomed to drop must never be parked. Order:
  unknown_template <- expired <- kill_switch_off <- conversation_closed <-
  suppressed <- no_consent <- no_prior_interaction <- frequency_cap_skip (marketing)
  then defers:
  channel_not_connected <- number_paused (marketing) <- human_active <-
  active_chat <- quiet_hours <- frequency_cap_defer (utility) <- ok
STOP beats everything: `suppressed` precedes `no_consent` (H78).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.policy.types import DEFER, DROP, OK, PolicyInput, Verdict


def decide(inp: PolicyInput) -> Verdict:
    t = inp.template
    if t is None:
        return Verdict(DROP, "unknown_template")

    # TTL: a stale proactive row is dropped (marketing 24h, utility 6h).
    if inp.now > inp.created_at + timedelta(hours=inp.ttl_hours):
        return Verdict(DROP, "expired")

    if inp.kill_switch_state == "off":
        return Verdict(DROP, "kill_switch_off")

    if inp.conversation_closed:
        return Verdict(DROP, "conversation_closed")

    if inp.suppressed:
        return Verdict(DROP, "suppressed")

    if not inp.has_consent:
        return Verdict(DROP, "no_consent")

    if not inp.has_prior_interaction:
        return Verdict(DROP, "no_prior_interaction")

    if t.message_class == "marketing":
        if inp.marketing_24h >= inp.per_24h_marketing or inp.marketing_7d >= inp.per_7d_marketing:
            return Verdict(DROP, "frequency_cap_skip")
    else:  # utility
        if inp.utility_24h >= inp.per_24h_utility:
            return Verdict(DEFER, "frequency_cap_defer")

    if not inp.channel_connected:
        return Verdict(DEFER, "channel_not_connected")

    if t.message_class == "marketing" and inp.number_paused:
        return Verdict(DEFER, "number_paused")

    if inp.human_active:
        return Verdict(DEFER, "human_active")

    if inp.active_chat:
        return Verdict(DEFER, "active_chat")

    if t.quiet_hours and inp.quiet_hours:
        return Verdict(DEFER, "quiet_hours")

    return Verdict(OK, "ok")
