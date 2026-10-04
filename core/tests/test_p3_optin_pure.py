"""core/tests/test_p3_optin_pure.py - §4.1 (PURE, no DB): the opt-in detection
table. H96: FULL-equality match after normalize only - no startswith, no
contains, no classifier; "نعم"/"ok"/"yes"/"موافق" are FORBIDDEN; and a STOP
phrase beats an opt-in word in the same batch/turn.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.workers import optout, turn
from app.workers.config import (
    DEFAULT_OPTIN_AR,
    DEFAULT_OPTIN_EN,
    DEFAULT_OPTOUT_AR,
    DEFAULT_OPTOUT_EN,
)
from app.workers.turn import last_consent_word

_AR = DEFAULT_OPTIN_AR
_EN = DEFAULT_OPTIN_EN


def _optin(msg: str) -> bool:
    return optout.detect_optin(msg, phrases_ar=_AR, phrases_en=_EN)


def _stop(msg: str) -> bool:
    return optout.detect(msg, phrases_ar=DEFAULT_OPTOUT_AR, phrases_en=DEFAULT_OPTOUT_EN) != ()


def _turn_settings() -> SimpleNamespace:
    return SimpleNamespace(
        core_optout_phrases_ar=DEFAULT_OPTOUT_AR,
        core_optout_phrases_en=DEFAULT_OPTOUT_EN,
        core_optin_phrases_ar=_AR,
        core_optin_phrases_en=_EN,
    )


@pytest.mark.parametrize(
    "msg",
    [
        "اشتراك",
        "اشترك",
        "اشتراك في الرسائل",
        "فعل الرسائل",
        "فعّل الرسائل",
        "Subscribe",
        "SUBSCRIBE",
        "Opt In",
        "optin",
        "start",
        "  اشتراك  ",   # trimmed by normalize
        "اِشْتِرَاك",     # diacritics stripped by normalize
        "إشتراك",       # hamza-on-alef unified by normalize
    ],
)
def test_full_equality_matches(msg):
    assert _optin(msg) is True


@pytest.mark.parametrize(
    "msg",
    [
        "اشتراك الباقة كم سعرها",  # a question, not a consent (spec, literal)
        "نعم",
        "موافق",
        "ok",
        "yes",
        "y",
        "اشتراك من فضلك",          # beginning-match is a STOP rule, never opt-in
        "اريد الاشتراك",
        "لا اريد الاشتراك",
        "",
        "   ",
    ],
)
def test_nothing_else_counts_as_optin(msg):
    assert _optin(msg) is False


def test_forbidden_words_are_not_in_the_default_lists():
    # H96, literal: adding any of these to the defaults is a policy violation.
    for banned in ("نعم", "موافق", "ok", "yes"):
        assert banned not in DEFAULT_OPTIN_AR
        assert banned not in DEFAULT_OPTIN_EN


def test_stop_phrase_is_stop_not_optin():
    # §4.1: «إيقاف اشتراك» => STOP, not an opt-in.
    msg = "إيقاف اشتراك"
    assert _stop(msg) is True
    assert _optin(msg) is False


def test_batch_with_stop_and_optin_last_word_wins():
    # §4.6 / F-P3-28 (H96 as amended): in the batch the LAST explicit word by
    # arrival order wins - here «إيقاف» follows «اشتراك», so the turn is a
    # STOP, and the ledger (written message-by-message) agrees.
    settings = _turn_settings()
    typed = [(1, "اشتراك", "text"), (2, "إيقاف", "text")]
    word = last_consent_word(typed, settings)
    assert word == "optout"
    d = turn.decide(
        kill_switch_state="on", consecutive_bot_replies=0, max_consecutive=8,
        consent_word=word,
        explicit_handoff=False,
    )
    assert d.decision == turn.Decision.OPTOUT_CONFIRM
    assert d.template_id == "optout_confirm"
    # And the other direction: stop then subscribe => the customer opted back
    # in, the ledger says granted, and so does the reply.
    word2 = last_consent_word([(1, "إيقاف", "text"), (2, "اشتراك", "text")], settings)
    assert word2 == "optin"
    d2 = turn.decide(
        kill_switch_state="on", consecutive_bot_replies=0, max_consecutive=8,
        consent_word=word2,
        explicit_handoff=False,
    )
    assert d2.decision == turn.Decision.OPTIN_CONFIRM


def test_marketing_stays_dark_in_the_catalog():
    """The formal dark rule (P3.2 §5.11, still binding through P3.3): no
    marketing template and no cart_reminder in PROACTIVE_TEMPLATES."""
    from app.workers.config import PROACTIVE_TEMPLATES

    for template_id, (meta, _text, _keys) in PROACTIVE_TEMPLATES.items():
        assert meta.message_class != "marketing", template_id
        assert template_id != "cart_reminder"
