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


class _Counter:
    def __init__(self):
        self.value = 0

    def inc(self, *a):
        self.value += 1

    def labels(self, *a):
        return self


def test_write_phase_delegates_to_verifier_and_skips_metrics_on_violation(monkeypatch):
    # P1.6 §9: turn.py now writes through verify.insert_verified_outbox, and
    # outbox_written_total must NOT rise when the verifier rejects (ok=False).
    from types import SimpleNamespace
    import uuid as _uuid

    from app.workers import turn
    from app.workers.turn import _Action, _TurnPlan

    conv = SimpleNamespace(id=_uuid.uuid4(), bot_status="active", epoch=1,
                           version=1, last_inbound_seq=5, last_processed_seq=0)
    plan = _TurnPlan(conversation_id=conv.id, tenant_id=_uuid.uuid4(),
                     channel_id=_uuid.uuid4(), decision=None, bodies=(), should_route=False)
    action = _Action("handoff_notice", "نص", False, None, "template", "handoff")

    calls: list[dict] = []
    monkeypatch.setattr(turn.repos_outbox, "lock_conversation",
                        lambda conn, conversation_id, tenant_id: conv)
    monkeypatch.setattr(turn.repos_outbox, "wa_id_for_conversation", lambda conn, cid: "wa-1")
    monkeypatch.setattr(turn.repos_outbox, "mark_turn_processed",
                        lambda conn, conversation_id, last_processed_seq: None)
    monkeypatch.setattr(turn, "_resolve_action",
                        lambda conn, settings, plan, router_result, query_vector=None: action)
    monkeypatch.setattr(turn.metrics, "turn_processed_total", _Counter())
    monkeypatch.setattr(turn.metrics, "turn_skipped_total", _Counter())
    monkeypatch.setattr(turn.metrics, "outbox_written_total", _Counter())
    monkeypatch.setattr(turn.metrics, "compose_replies_total", _Counter())

    class FakeOutcome:
        ok = False

    monkeypatch.setattr(turn.verify, "insert_verified_outbox",
                        lambda conn, **kw: calls.append(kw) or FakeOutcome())

    result = turn._write_phase(None, None, plan, None, None, rules=None)
    assert result == "handoff"
    assert len(calls) == 1
    assert calls[0]["text"] == "نص"
    assert calls[0]["template_id"] == "handoff_notice"
    # ok=False => the composed reply did NOT go out, so it must not be counted.
    assert turn.metrics.outbox_written_total.value == 0
    assert turn.metrics.compose_replies_total.value == 0


