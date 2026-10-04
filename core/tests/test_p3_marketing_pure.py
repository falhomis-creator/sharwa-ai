"""P3.4 pure tests (H100-H103): the two new policy verdicts and their place in
the decision order, fail-closed defaults, the cart_reminder merge value, the
confirmation phrase, and the rendered production template through the verifier."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.policy import decision
from app.policy.types import DEFER, DROP, OK, DEFER_REASONS, DROP_REASONS, PolicyInput, TemplateMeta, Verdict
from app.workers import cart_reminder, config, marketing, proactive, verify_rules

TZ = timezone(timedelta(hours=3))
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=TZ)


def _meta(cls: str = "marketing") -> TemplateMeta:
    return TemplateMeta(
        template_id="cart_reminder" if cls == "marketing" else "stock_available",
        message_class=cls,
        consent_scope="marketing" if cls == "marketing" else "back_in_stock",
        capability="marketing" if cls == "marketing" else "back_in_stock",
        quiet_hours=False, footer_required=(cls == "marketing"),
    )


def _inp(**kw) -> PolicyInput:
    base = dict(
        template=_meta(), now=NOW, created_at=NOW - timedelta(hours=1), ttl_hours=24.0,
        kill_switch_state=None, conversation_closed=False, suppressed=False,
        has_consent=True, has_prior_interaction=True, channel_connected=True,
        number_paused=False, human_active=False, active_chat=False, quiet_hours=False,
        marketing_24h=0, marketing_7d=0, utility_24h=0,
        per_24h_marketing=1, per_7d_marketing=2, per_24h_utility=3,
        marketing_enabled=True, tenant_marketing_24h=0, canary_cap=5,
    )
    base.update(kw)
    return PolicyInput(**base)


# --- the two new verdicts ------------------------------------------------------


def test_reasons_are_in_the_closed_sets():
    assert "marketing_not_enabled" in DROP_REASONS
    assert "canary_cap_reached" in DEFER_REASONS


def test_enabled_marketing_with_consent_is_ok():
    assert decision.decide(_inp()) == Verdict(OK, "ok")


def test_marketing_not_enabled_drops():
    v = decision.decide(_inp(marketing_enabled=False))
    assert (v.action, v.reason) == (DROP, "marketing_not_enabled")


def test_policy_input_defaults_are_fail_closed():
    """A caller that forgets the P3.4 fields gets 'not enabled', never an
    accidental enablement (H100)."""
    kw = dict(
        template=_meta(), now=NOW, created_at=NOW - timedelta(hours=1), ttl_hours=24.0,
        kill_switch_state=None, conversation_closed=False, suppressed=False,
        has_consent=True, has_prior_interaction=True, channel_connected=True,
        number_paused=False, human_active=False, active_chat=False, quiet_hours=False,
        marketing_24h=0, marketing_7d=0, utility_24h=0,
        per_24h_marketing=1, per_7d_marketing=2, per_24h_utility=3,
    )
    assert decision.decide(PolicyInput(**kw)).reason == "marketing_not_enabled"


def test_utility_is_never_affected_by_activation_or_canary():
    for kw in (dict(marketing_enabled=False), dict(canary_cap=0, tenant_marketing_24h=99)):
        assert decision.decide(_inp(template=_meta("utility"), **kw)) == Verdict(OK, "ok")


def test_canary_cap_defers_at_the_cap_not_below():
    below = decision.decide(_inp(tenant_marketing_24h=4, canary_cap=5))
    assert below == Verdict(OK, "ok")
    at = decision.decide(_inp(tenant_marketing_24h=5, canary_cap=5))
    assert (at.action, at.reason) == (DEFER, "canary_cap_reached")  # a DEFER, never a drop


# --- order (H101: the global switch first; permanent drops before the defer) -----


def test_global_kill_switch_beats_not_enabled():
    v = decision.decide(_inp(kill_switch_state="off", marketing_enabled=False))
    assert v.reason == "kill_switch_off"


def test_not_enabled_beats_suppressed_and_no_consent():
    v = decision.decide(_inp(marketing_enabled=False, suppressed=True, has_consent=False))
    assert v.reason == "marketing_not_enabled"


def test_expired_beats_not_enabled():
    v = decision.decide(_inp(marketing_enabled=False, created_at=NOW - timedelta(hours=30)))
    assert v.reason == "expired"


def test_suppression_beats_canary_and_no_consent_beats_canary():
    assert decision.decide(_inp(suppressed=True, tenant_marketing_24h=9)).reason == "suppressed"
    assert decision.decide(_inp(has_consent=False, tenant_marketing_24h=9)).reason == "no_consent"


def test_customer_frequency_drop_beats_canary_defer():
    v = decision.decide(_inp(marketing_24h=1, tenant_marketing_24h=9))
    assert (v.action, v.reason) == (DROP, "frequency_cap_skip")


def test_canary_defer_precedes_the_other_defers():
    v = decision.decide(_inp(tenant_marketing_24h=9, channel_connected=False))
    assert v.reason == "canary_cap_reached"


def test_decide_is_deterministic():
    inp = _inp(tenant_marketing_24h=5)
    assert decision.decide(inp) == decision.decide(inp)


# --- items_phrase (OQ-P3-08, pure) ------------------------------------------------


@pytest.mark.parametrize("title,count,expected", [
    ("حذاء", 1, "«حذاء»"),
    ("حذاء", 0, "«حذاء»"),
    ("حذاء", 2, "«حذاء» ومنتج آخر"),
    ("حذاء", 3, "«حذاء» ومنتجان آخران"),
    ("حذاء", 4, "«حذاء» و3 منتجات أخرى"),
    ("حذاء", 11, "«حذاء» و10 منتجات أخرى"),
    ("حذاء", 12, "«حذاء» و11 منتجاً آخر"),
    ("", 5, "منتجات"),
    ("   ", 1, "منتجات"),
])
def test_items_phrase_table(title, count, expected):
    assert cart_reminder.items_phrase(title, count) == expected


def test_items_phrase_cannot_close_the_quote():
    assert cart_reminder.items_phrase("حذاء» وعد «", 1) == "«حذاء وعد»"


def test_merge_fields_exposes_only_what_the_template_whitelists():
    class S:
        cart_item_title_max = 60

    merge = cart_reminder._merge_fields(S(), {"items": [{"title": "حذاء"}], "item_count": 3})
    _meta_, _text, keys = config.proactive_template("cart_reminder")
    assert keys == ("items_phrase",)
    assert {k: v for k, v in merge.items() if k in keys} == {"items_phrase": "«حذاء» ومنتجان آخران"}


# --- the production template renders clean through the verifier (H83) -------------


def test_production_template_renders_with_footer_and_passes_the_verifier():
    phrase = cart_reminder.items_phrase("حذاء رياضي", 3)
    text, meta = proactive.render_text("cart_reminder", {"items_phrase": phrase})
    assert "«items_phrase»" not in text and "حذاء رياضي" in text
    assert meta.message_class == "marketing" and meta.footer_required
    full = f"{text}\n{config.DEFAULT_MARKETING_FOOTER_AR}"
    rules = verify_rules.build_rules(profanity=("كلب",), competitor=(), disclosure=())
    assert verify_rules.check_text(full, rules=rules, max_chars=4000).ok


def test_unknown_merge_key_is_refused():
    with pytest.raises(ValueError):
        proactive.render_text("cart_reminder", {"title": "x"})


def test_confirm_phrase_is_literal():
    assert marketing.confirm_phrase("acme") == "ENABLE-MARKETING acme"
    assert marketing.confirm_phrase("acme") != marketing.confirm_phrase("acme2")


def test_config_default_footer_is_non_empty():
    assert config.DEFAULT_MARKETING_FOOTER_AR.strip()
