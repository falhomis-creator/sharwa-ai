"""P4 Task 18b-2b: the size path of the turn - pure tests (code-rule detection,
the coordinator's product/chart handling, the reply/hand-off mapping and the
SizeContext hand-over to the Verifier)."""
from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.db import repos_size
from app.fit.size_advisor import SizeAdvice, SizeChart, SizeRow
from app.workers import size, turn, verify_rules
from app.workers.turn import _Action, _TurnPlan

D = Decimal


# ---- the code rule ----------------------------------------------------------------

# F-P4-10 (owner decision 2026-10-09): only a PLAUSIBLE body measurement makes a
# size question; the size word alone no longer does (it hijacked product search).
@pytest.mark.parametrize("text", [
    "طولي 175 ووزني 80", "صدري 100 سم", "1 متر و75", "وزني 70", "طولي 170 ايش مقاسي",
])
def test_size_questions_are_detected(text):
    assert size.is_size_question((text,))


@pytest.mark.parametrize("text", [
    "مرحبا", "كم سعر القميص", "ابي اطلب", "رقم طلبي 12345",
    "كم مقاسي؟", "ايش المقاس المناسب", "what size",          # size word, no measurement
    "منتج قميص مقاس L", "ابي منتج قميص مقاسي 42",            # product searches naming a size
    "وزني 2 كيلو", "طولي 50",                                 # implausible measurements
])
def test_other_messages_are_not_size_questions(text):
    assert not size.is_size_question((text,))


# ---- the coordinator ----------------------------------------------------------------

CHART = SizeChart(rows=(
    SizeRow("S", 1, height_cm=(D("150"), D("165")), weight_kg=(D("45"), D("60"))),
    SizeRow("M", 2, height_cm=(D("165"), D("175")), weight_kg=(D("60"), D("75"))),
))


def _patch(monkeypatch, shown: list[str], chart=CHART, error: bool = False) -> list[str]:
    asked: list[str] = []
    monkeypatch.setattr(size.repos_stock, "read_conversation_slots",
                        lambda conn, **kw: {"last_shown_product_ids": shown})

    def read(conn, *, platform_product_id):
        asked.append(platform_product_id)
        if error:
            raise repos_size.SizeChartDataError("bad range")
        return chart

    monkeypatch.setattr(size.repos_size, "read_size_chart", read)
    return asked


def _advise(bodies=("طولي 170 ووزني 65",)):
    return size.advise_for_turn(None, tenant_id=uuid.uuid4(), conversation_id=uuid.uuid4(), bodies=bodies)


@pytest.mark.parametrize("shown", [[], ["P1", "P2"]])
def test_no_single_shown_product_gives_no_advice(monkeypatch, shown):
    asked = _patch(monkeypatch, shown)
    assert _advise() == size.SizeTurn(None, ())
    assert asked == []  # never reads a chart for a guessed product


def test_single_product_is_advised_with_its_chart_labels(monkeypatch):
    asked = _patch(monkeypatch, ["P1"])
    result = _advise()
    assert asked == ["P1"]
    assert result.advice is not None and result.advice.size == "M"
    assert result.labels == ("S", "M")


def test_malformed_chart_fails_closed_as_invalid_chart(monkeypatch):
    _patch(monkeypatch, ["P1"], error=True)
    result = _advise()
    assert result.advice is not None
    assert (result.advice.size, result.advice.reasons) == (None, ("invalid_chart",))


def test_missing_chart_has_no_labels(monkeypatch):
    _patch(monkeypatch, ["P1"], chart=None)
    result = _advise()
    assert result.advice is not None and result.advice.reasons == ("no_chart",)
    assert result.labels == ()


# ---- the reply / hand-off mapping in the turn ---------------------------------------

def _plan() -> _TurnPlan:
    return _TurnPlan(conversation_id=uuid.uuid4(), tenant_id=uuid.uuid4(), channel_id=uuid.uuid4(),
                     decision=None, bodies=("x",), should_route=False, size_turn=True)


def _action(monkeypatch, result: size.SizeTurn) -> _Action:
    monkeypatch.setattr(turn.size, "advise_for_turn", lambda conn, **kw: result)
    return turn._resolve_action(None, None, _plan(), None)


@pytest.mark.parametrize(("advice", "template_id", "handoff", "reason"), [
    (SizeAdvice("M", None, "medium", ("height_weight_match",), False), "size_recommend", False, None),
    (SizeAdvice("M", "L", "medium", ("multiple_matches_fit_pref",), False),
     "size_recommend_alt", False, None),
    (SizeAdvice("S", None, "low", ("nearest_out_of_range",), True), "size_nearest_out_of_range", False, None),
    (SizeAdvice(None, None, "low", ("missing_height_weight",), False), "size_need_inputs", False, None),
    (SizeAdvice(None, None, "low", ("no_chart",), False), "size_no_chart", True, "size_no_chart"),
    (SizeAdvice(None, None, "low", ("heavier_than_chart",), True), "size_no_fit", True, "size_no_fit"),
    (SizeAdvice(None, None, "low", ("something_new",), False), "handoff_notice", True, "size_unknown"),
])
def test_each_advice_maps_to_its_template_and_handoff(monkeypatch, advice, template_id, handoff, reason):
    action = _action(monkeypatch, size.SizeTurn(advice, ("S", "M", "L")))
    assert (action.template_id, action.handoff, action.handoff_reason) == (template_id, handoff, reason)
    assert action.size_context == verify_rules.SizeContext(advice.size, advice.alt_size, ("S", "M", "L"))


def test_no_product_hands_off_with_its_own_reason(monkeypatch):
    action = _action(monkeypatch, size.SizeTurn(None, ()))
    assert (action.template_id, action.handoff, action.handoff_reason) == (
        "handoff_notice", True, "size_no_product",
    )
    assert action.size_context is None


def test_non_size_turn_never_reaches_the_size_path(monkeypatch):
    def boom(conn, **kw):
        raise AssertionError("size path must not run")

    monkeypatch.setattr(turn.size, "advise_for_turn", boom)
    plan = _TurnPlan(conversation_id=uuid.uuid4(), tenant_id=uuid.uuid4(), channel_id=uuid.uuid4(),
                     decision=turn.decide(kill_switch_state=None, consecutive_bot_replies=0,
                                          max_consecutive=5, consent_word=None, explicit_handoff=False),
                     bodies=("مقاسي",), should_route=False)
    assert turn._resolve_action(None, None, plan, None).template_id == "handoff_notice"


# ---- the Verifier receives the advice -------------------------------------------------

class _Counter:
    def inc(self, *a):
        return None

    def labels(self, *a):
        return self


def test_write_phase_hands_the_size_context_to_the_verifier(monkeypatch):
    conv = SimpleNamespace(id=uuid.uuid4(), bot_status="active", epoch=1,
                           version=1, last_inbound_seq=5, last_processed_seq=0)
    sc = verify_rules.SizeContext("M", None, ("S", "M"))
    action = _Action("size_recommend", "المقاس المناسب لك: مقاس M ✅", False, None, "template",
                     "size_recommend", size_context=sc)
    calls: list[dict] = []
    monkeypatch.setattr(turn.repos_outbox, "lock_conversation", lambda conn, conversation_id, tenant_id: conv)
    monkeypatch.setattr(turn.repos_outbox, "wa_id_for_conversation", lambda conn, cid: "wa-1")
    monkeypatch.setattr(turn.repos_outbox, "mark_turn_processed",
                        lambda conn, conversation_id, last_processed_seq: None)
    monkeypatch.setattr(turn, "_resolve_action",
                        lambda conn, settings, plan, router_result, query_vector=None,
                        order_lookup=None: action)
    for name in ("turn_processed_total", "turn_skipped_total",
                 "outbox_written_total", "compose_replies_total"):
        monkeypatch.setattr(turn.metrics, name, _Counter())
    monkeypatch.setattr(turn.verify, "insert_verified_outbox",
                        lambda conn, **kw: calls.append(kw) or SimpleNamespace(ok=True))
    turn._write_phase(None, None, _plan(), None, None, rules=None)
    assert calls[0]["size_context"] is sc


def test_flag_defaults_to_on():
    # Owner decision 2026-10-09: the size advisor is ON by default.
    from app.workers.config import WorkerSettings
    assert WorkerSettings.__dataclass_fields__["size_advice_enabled"].default is True
