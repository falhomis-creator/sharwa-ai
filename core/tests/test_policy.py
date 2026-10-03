"""Pure send-policy tests (P3.1 §5): the decide table, warm-up, quiet hours, health
classification and determinism (H84)."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from app.policy import decision, health, quiet_hours, warmup
from app.policy.types import DEFER, DROP, OK, PolicyInput, TemplateMeta, Verdict

TZ = timezone(timedelta(hours=3))  # Asia/Aden


def _meta(cls: str = "utility", quiet: bool = False) -> TemplateMeta:
    return TemplateMeta(
        template_id="stock_available", message_class=cls, consent_scope="back_in_stock",
        capability="back_in_stock", quiet_hours=quiet, footer_required=(cls == "marketing"),
    )


def _inp(**kw) -> PolicyInput:
    base = dict(
        template=_meta(), now=datetime(2026, 10, 3, 12, 0, tzinfo=TZ),
        created_at=datetime(2026, 10, 3, 11, 0, tzinfo=TZ), ttl_hours=6.0,
        kill_switch_state=None, conversation_closed=False, suppressed=False,
        has_consent=True, has_prior_interaction=True, channel_connected=True,
        number_paused=False, human_active=False, active_chat=False, quiet_hours=False,
        marketing_24h=0, marketing_7d=0, utility_24h=0,
        per_24h_marketing=1, per_7d_marketing=2, per_24h_utility=3,
    )
    base.update(kw)
    return PolicyInput(**base)


def test_ok():
    assert decision.decide(_inp()) == Verdict(OK, "ok")


def test_unknown_template():
    assert decision.decide(_inp(template=None)).reason == "unknown_template"


def test_expired():
    d = decision.decide(_inp(created_at=datetime(2026, 10, 3, 0, 0, tzinfo=TZ), ttl_hours=6.0))
    assert d.reason == "expired"


def test_kill_switch_off():
    assert decision.decide(_inp(kill_switch_state="off")).reason == "kill_switch_off"


def test_conversation_closed():
    assert decision.decide(_inp(conversation_closed=True)).reason == "conversation_closed"


def test_suppressed_beats_no_consent():
    # H78: STOP beats consent - suppressed precedes no_consent.
    d = decision.decide(_inp(suppressed=True, has_consent=False))
    assert d.reason == "suppressed"


def test_no_consent():
    assert decision.decide(_inp(has_consent=False)).reason == "no_consent"


def test_no_prior_interaction():
    assert decision.decide(_inp(has_prior_interaction=False)).reason == "no_prior_interaction"


def test_marketing_frequency_cap_skip():
    d = decision.decide(_inp(template=_meta("marketing", quiet=True), marketing_24h=1))
    assert d.action == DROP and d.reason == "frequency_cap_skip"


def test_utility_frequency_cap_defer():
    d = decision.decide(_inp(utility_24h=3))
    assert d.action == DEFER and d.reason == "frequency_cap_defer"


def test_channel_not_connected():
    assert decision.decide(_inp(channel_connected=False)).reason == "channel_not_connected"


def test_number_paused_marketing_only():
    d = decision.decide(_inp(template=_meta("marketing", quiet=True), number_paused=True))
    assert d.reason == "number_paused"
    assert decision.decide(_inp(number_paused=True)).reason == "ok"  # H82: utility not deferred


def test_human_active():
    assert decision.decide(_inp(human_active=True)).reason == "human_active"


def test_active_chat():
    assert decision.decide(_inp(active_chat=True)).reason == "active_chat"


def test_quiet_hours():
    d = decision.decide(_inp(template=_meta(quiet=True), quiet_hours=True))
    assert d.reason == "quiet_hours"


def test_determinism_100_calls():
    first = decision.decide(_inp())
    for _ in range(100):
        assert decision.decide(_inp()) == first


# --- warm-up ladder (H81) ----------------------------------------------------

def test_cap_for_monotonic():
    ladder = {0: 20, 3: 40, 7: 80, 14: 150, 30: 250}
    caps = [warmup.cap_for(d, ladder) for d in range(40)]
    assert caps == sorted(caps)
    assert warmup.cap_for(0, ladder) == 20
    assert warmup.cap_for(29, ladder) == 150
    assert warmup.cap_for(30, ladder) == 250


def test_parse_ladder():
    assert warmup.parse_ladder("0:20,3:40") == {0: 20, 3: 40}


def test_day_index_whole_local_days():
    start = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)
    assert warmup.day_index(start, now, TZ) == 3


# --- quiet hours (H76) -------------------------------------------------------

def test_quiet_hours_wraps_midnight():
    start, end = time(22, 0), time(9, 0)
    assert quiet_hours.in_quiet_hours(datetime(2026, 10, 3, 23, 0, tzinfo=TZ), TZ, start, end)
    assert quiet_hours.in_quiet_hours(datetime(2026, 10, 3, 3, 0, tzinfo=TZ), TZ, start, end)
    assert not quiet_hours.in_quiet_hours(datetime(2026, 10, 3, 12, 0, tzinfo=TZ), TZ, start, end)


def test_next_allowed_never_inside_window():
    start, end = time(22, 0), time(9, 0)
    for h in range(24):
        now = datetime(2026, 10, 3, h, 0, tzinfo=TZ)
        nxt = quiet_hours.next_allowed_at(now, TZ, start, end)
        assert nxt >= now
        assert not quiet_hours.in_quiet_hours(nxt, TZ, start, end)


# --- health classification (H81, sample-floored) -----------------------------

def test_classify_sample_floor():
    # 1 optout of 3 sends is below the sample floor - must not pause/throttle.
    s = health.HealthSignals(3, 0, 1, 0, None)
    assert health.classify(s, health.HealthConfig()).state == "healthy"


def test_classify_pause_on_optout_rate():
    s = health.HealthSignals(100, 0, 6, 0, None)  # 6% optout
    assert health.classify(s, health.HealthConfig()).state == "paused"


def test_classify_throttle_on_failure_rate():
    s = health.HealthSignals(100, 15, 0, 0, None)  # 15% failure
    assert health.classify(s, health.HealthConfig()).state == "throttled"


def test_classify_channel_banned_pauses_immediately():
    s = health.HealthSignals(0, 0, 0, 0, "banned")
    assert health.classify(s, health.HealthConfig()).state == "paused"


def test_verdict_rejects_unknown_reason():
    import pytest
    with pytest.raises(ValueError):
        Verdict(DROP, "not_a_reason")

