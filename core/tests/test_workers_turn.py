"""Pure deterministic turn-decision + template tests (P1.2, H25/H23).

No DB/Redis: only decide()'s fixed input->output table and the approved
template texts are under test.
"""
from __future__ import annotations

from app.workers import templates
from app.workers.turn import Decision, decide


def _decide(**kw: object) -> object:
    base = dict(
        kill_switch_state="on", consecutive_bot_replies=0,
        max_consecutive=8, optout_detected=False, explicit_handoff=False,
    )
    base.update(kw)
    return decide(**base)  # type: ignore[arg-type]


def test_default_decision_is_handoff_not_silence():
    # OQ-P1-11 / D-P1-15: the default is HANDOFF (bot_cannot_answer), never silence.
    d = _decide()
    assert d.decision == Decision.HANDOFF
    assert d.template_id == "handoff_notice"
    assert d.handoff is True
    assert d.handoff_reason == "bot_cannot_answer"


def test_optout_wins_and_does_not_handoff():
    d = _decide(optout_detected=True)
    assert d.decision == Decision.OPTOUT_CONFIRM
    assert d.template_id == "optout_confirm"
    assert d.handoff is False


def test_killswitch_off_gives_safe_ack_and_handoff():
    d = _decide(kill_switch_state="off")
    assert d.decision == Decision.SAFE_ACK
    assert d.template_id == "safe_ack"
    assert d.handoff is True
    assert d.handoff_reason == "kill_switch_off"


def test_explicit_handoff_is_customer_requested():
    d = _decide(explicit_handoff=True)
    assert d.decision == Decision.HANDOFF
    assert d.handoff_reason == "customer_requested"


def test_consecutive_reply_cap_triggers_handoff():
    d = _decide(consecutive_bot_replies=8)
    assert d.decision == Decision.HANDOFF
    assert d.handoff_reason == "bot_reply_cap"


def test_templates_are_owner_approved_and_clean():
    assert "بوت" not in templates.template_text("handoff_notice")
    assert "ذكاء" not in templates.template_text("handoff_notice")
    assert templates.template_text("optout_confirm").startswith("تم إيقاف الرسائل")
    assert templates.template_text("safe_ack").startswith("وصلتنا رسالتك")


def test_policy_exempt_templates_are_closed_set():
    # F-P1-06 fix (PROMPT_P1_03 §0): the ai_reply-exempt set is `safe_ack` ALONE
    # (a closed list - adding to it is a policy decision, not programming).
    assert templates.POLICY_EXEMPT_TEMPLATES == frozenset({"safe_ack"})

