"""core/app/workers/turn.py - the turn engine with the P1.5 router (H38/H40).

The model CLASSIFIES, the code WRITES (H38): decide() stays deterministic, and
the router is consulted ONLY when decide() reaches "bot_cannot_answer" (no
deterministic path took over). Nothing the model emits ever reaches a customer;
every outbound text is composed in app/workers/compose.py from a template or the
merchant's own text.

H40: the provider call runs OUTSIDE any tenant_tx - the turn reads + decides in
one short transaction, calls the router, then writes + accounts in a second
short transaction (re-locking with SKIP LOCKED to stay single-writer).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app import db as core_db
from app import ws_publish
from app.db import repos_catalog
from app.db import repos_inbox
from app.db import repos_outbox
from app.obs import metrics
from app.workers import address
from app.workers import compose
from app.workers import optout
from app.workers import orders
from app.workers import stock
from app.workers import templates
from app.workers import verify
from app.workers.config import WorkerSettings
from app.workers.stream import TransientError


class Decision(str, Enum):
    OPTOUT_CONFIRM = "optout_confirm"
    SAFE_ACK = "safe_ack"
    HANDOFF = "handoff"


@dataclass(frozen=True)
class TurnDecision:
    decision: Decision
    template_id: str
    handoff: bool
    handoff_reason: str | None


def decide(
    *,
    kill_switch_state: str | None,
    consecutive_bot_replies: int,
    max_consecutive: int,
    optout_detected: bool,
    explicit_handoff: bool,
) -> TurnDecision:
    """Deterministic decision (H25). Order matters (any gate that fails takes an
    explicit safe path, never silence)."""
    if optout_detected:
        return TurnDecision(Decision.OPTOUT_CONFIRM, "optout_confirm", False, None)
    if kill_switch_state == "off":
        return TurnDecision(Decision.SAFE_ACK, "safe_ack", True, "kill_switch_off")
    if explicit_handoff:
        return TurnDecision(Decision.HANDOFF, "handoff_notice", True, "customer_requested")
    if consecutive_bot_replies >= max_consecutive:
        return TurnDecision(Decision.HANDOFF, "handoff_notice", True, "bot_reply_cap")
    # OQ-P1-11 / D-P1-15: default HANDOFF (bot_cannot_answer), not silence.
    return TurnDecision(Decision.HANDOFF, "handoff_notice", True, "bot_cannot_answer")


def detect_optout(bodies: list[tuple[int, str | None]], settings: WorkerSettings) -> bool:
    for _seq, body in bodies:
        if body and optout.detect(
            body,
            phrases_ar=settings.core_optout_phrases_ar,
            phrases_en=settings.core_optout_phrases_en,
        ):
            return True
    return False


def detect_handoff(bodies: list[tuple[int, str | None]], settings: WorkerSettings) -> bool:
    for _seq, body in bodies:
        if body and optout.detect_handoff(
            body,
            phrases_ar=settings.core_handoff_phrases_ar,
            phrases_en=settings.core_handoff_phrases_en,
        ):
            return True
    return False


@dataclass(frozen=True)
class LlmRouterHandle:
    """provider + breaker built from settings (S8: only turn.py imports app.llm)."""
    provider: Any
    breaker: Any
    provider_name: str
    model_name: str


@dataclass(frozen=True)
class _TurnPlan:
    conversation_id: uuid.UUID
    tenant_id: uuid.UUID
    channel_id: uuid.UUID
    decision: TurnDecision
    bodies: tuple[str, ...]
    should_route: bool


@dataclass(frozen=True)
class _Action:
    template_id: str
    text: str
    handoff: bool
    handoff_reason: str | None
    kind: str | None
    outcome: str


def build_router(settings: WorkerSettings) -> LlmRouterHandle:
    """Build the provider + breaker from settings. Only turn.py may import
    app.llm (S8 rule 1: the single consumption point)."""
    from app.llm import registry
    from app.llm.breaker import CircuitBreaker
    provider = registry.build_provider(settings.llm_provider)
    breaker = CircuitBreaker(settings.llm_breaker_fail_threshold, settings.llm_breaker_reset_s)
    return LlmRouterHandle(
        provider=provider, breaker=breaker,
        provider_name=settings.llm_provider, model_name="fake-router",
    )


def _route(settings: WorkerSettings, router: LlmRouterHandle, bodies: tuple[str, ...]) -> Any:
    from app.llm import router as llm_router
    messages = llm_router.build_router_messages(list(bodies), settings.llm_router_max_input_chars)
    return llm_router.run_router(
        router.provider, router.breaker, provider_name=router.provider_name,
        messages=messages, min_confidence=settings.llm_router_min_confidence,
        max_output_tokens=settings.llm_router_max_output_tokens,
        timeout_s=settings.llm_timeout_s,
    )


def _deterministic_action(decision: TurnDecision) -> _Action:
    return _Action(
        template_id=decision.template_id, text=templates.template_text(decision.template_id),
        handoff=decision.handoff, handoff_reason=decision.handoff_reason,
        kind="template", outcome=decision.decision.value,
    )


def _order_action(settings: WorkerSettings, order_lookup: Any) -> _Action:
    """Compose the reply for an order-tracking result. order_unverified and
    order_blocked hand off to a human (single unified failure templates - H53)."""
    kind = order_lookup.kind
    if kind == "card":
        text = compose.compose_order_status(order_lookup.card, labels=compose.ORDER_STATUS_LABELS)
        if text is None:
            return _Action("order_status_unknown", templates.template_text("order_status_unknown"), False, None, "template", "order_status_unknown")
        return _Action("order_status", text[:settings.order_status_max_chars], False, None, "template", "order_status")
    if kind == "unverified":
        return _Action("order_unverified", templates.template_text("order_unverified"), True, "order_unverified", "template", "order_unverified")
    if kind == "blocked":
        return _Action("order_blocked", templates.template_text("order_blocked"), True, "order_blocked", "template", "order_blocked")
    if kind == "need_order_ref":
        return _Action("order_need_ref", templates.template_text("order_need_ref"), False, None, "template", "order_need_ref")
    if kind == "need_phone":
        return _Action("order_need_phone", templates.template_text("order_need_phone"), False, None, "template", "order_need_phone")
    return _Action("order_unavailable", templates.template_text("order_unavailable"), False, None, "template", "order_unavailable")


def _stock_action(decision: Any) -> _Action:
    """Compose the reply for a join-waitlist result (P2.3 §5.4). No handoff for a
    successful join; a customer already waiting gets the explicit template."""
    kind = decision.kind
    if kind == "joined":
        return _Action("stock_joined", templates.template_text("stock_joined"), False, None, "template", "stock_joined")
    if kind == "already_waiting":
        return _Action("stock_already_waiting", templates.template_text("stock_already_waiting"), False, None, "template", "stock_already_waiting")
    return _Action("stock_unavailable", templates.template_text("stock_unavailable"), False, None, "template", "stock_unavailable")


def _address_action(settings: WorkerSettings, decision: Any) -> _Action:
    """Compose the reply for an address decision. Confirmed back to the customer
    as a place NAME, never as coordinates (H67). coord_from_model => reject +
    handoff; pin_outside_coverage => ask for another location."""
    d = decision.decision
    if d == "rejected":
        if decision.reason_code == "pin_outside_coverage":
            return _Action("address_out_of_coverage", templates.template_text("address_out_of_coverage"), False, None, "template", "address_out_of_coverage")
        return _Action("address_rejected", templates.template_text("address_rejected"), True, "coord_from_model", "template", "address_rejected")
    if d == "ask_for_pin":
        return _Action("address_need_pin", templates.template_text("address_need_pin"), False, None, "template", "address_need_pin")
    if d == "confirm_with_customer":
        name = decision.candidates[0] if decision.candidates else ""
        return _Action("address_confirm", compose.compose_address_options([name], template="address_confirm"), False, None, "template", "address_confirm")
    if d == "disambiguate":
        return _Action("address_disambiguate", compose.compose_address_options(list(decision.candidates), template="address_disambiguate"), False, None, "template", "address_disambiguate")
    return _Action("handoff_notice", templates.template_text("handoff_notice"), True, "address_accepted", "template", "handoff_notice")


def _resolve_action(
    conn: Any, settings: WorkerSettings, plan: _TurnPlan, router_result: Any,
    query_vector: list[float] | None = None,
    order_lookup: Any = None,
) -> _Action:
    if order_lookup is not None:
        return _order_action(settings, order_lookup)
    intent = router_result.decision.intent if router_result is not None else "other"
    if intent == "product_search":
        query = router_result.decision.query if router_result is not None else ""
        cards = repos_catalog.search_products(
            conn, tenant_id=plan.tenant_id, query=query, query_vector=query_vector,
        ) if query else []
        if cards:
            return _Action("product_list", compose.compose_product_list(cards), False, None, "product_list", "product_list")
        return _deterministic_action(plan.decision)
    if intent == "policy_question":
        question = router_result.decision.query if router_result is not None else ""
        chunks = repos_catalog.kb_search(conn, tenant_id=plan.tenant_id, question=question) if question else []
        if chunks:
            return _Action("policy_answer", compose.compose_policy_answer(chunks[0]["content"]), False, None, "policy_answer", "policy_answer")
        return _deterministic_action(plan.decision)
    if intent == "delivery_address":
        query = router_result.decision.query if router_result is not None else ""
        decision = address.resolve_and_persist(
            conn, settings, tenant_id=plan.tenant_id, conversation_id=plan.conversation_id, query=query,
        )
        return _address_action(settings, decision)
    if intent == "stock_waitlist":
        decision = stock.join_waitlist(
            conn, settings, tenant_id=plan.tenant_id,
            conversation_id=plan.conversation_id, bodies=plan.bodies,
        )
        return _stock_action(decision)
    return _deterministic_action(plan.decision)


def _account(conn: Any, settings: WorkerSettings, plan: _TurnPlan, router: LlmRouterHandle, router_result: Any) -> None:
    from app.llm import budget
    usage = router_result.usage if router_result is not None else None
    if usage is not None:
        provider, model = usage.provider, usage.model
        input_tokens, output_tokens = usage.input_tokens, usage.output_tokens
        cost = budget.compute_cost_micro_usd(input_tokens, output_tokens, settings.llm_price_table, provider, model)
        latency_ms = usage.latency_ms
        metrics.llm_tokens_total.labels("input").inc(input_tokens)
        metrics.llm_tokens_total.labels("output").inc(output_tokens)
        metrics.llm_cost_micro_usd_total.inc(cost)
        metrics.llm_latency_seconds.labels("router").observe(latency_ms / 1000.0)
    else:
        provider, model = router.provider_name, router.model_name
        input_tokens = output_tokens = cost = 0
        latency_ms = None
    status = router_result.status if router_result is not None else "error"
    metrics.llm_calls_total.labels("router", provider, status).inc()
    budget.account(
        conn, tenant_id=plan.tenant_id, month=budget.current_month(),
        conversation_id=plan.conversation_id, purpose="router",
        provider=provider, model=model, input_tokens=input_tokens,
        output_tokens=output_tokens, cost_micro_usd=cost,
        latency_ms=latency_ms, status=status,
    )


def process_turn(
    *, settings: WorkerSettings, conversation_id: uuid.UUID, tenant_id: uuid.UUID,
    router: LlmRouterHandle | None = None, embed: Any = None, cache_client: Any = None,
    rules: Any = None, commerce: Any = None,
) -> str:
    """Run one turn. The provider call (if any) runs OUTSIDE any transaction
    (H40): read + decide in one short tx, route, then write + account in a
    second short tx (re-locking SKIP LOCKED to stay single-writer)."""
    started = time.monotonic()

    # ---- Phase 1: read + decide (short transaction) ----
    with core_db.tenant_tx(tenant_id) as conn:
        conv = repos_outbox.lock_conversation(conn, conversation_id=conversation_id, tenant_id=tenant_id)
        if conv is None:
            metrics.turn_skipped_total.labels("locked").inc()
            return "noop"
        if conv.bot_status != "active":
            repos_outbox.mark_turn_processed(conn, conversation_id=conversation_id, last_processed_seq=conv.last_inbound_seq)
            metrics.turn_skipped_total.labels("not_active").inc()
            return "skipped"

        channel_id = repos_outbox.resolve_channel_for_conversation(conn, conversation_id)
        kill_switch = repos_outbox.effective_switch(
            conn, tenant_id=tenant_id, channel_account_id=channel_id, capability="ai_reply",
        )
        consecutive = repos_outbox.count_consecutive_bot_replies(conn, conversation_id)
        bodies = repos_outbox.latest_inbound_texts(conn, conversation_id, conv.last_processed_seq)
        decision = decide(
            kill_switch_state=kill_switch,
            consecutive_bot_replies=consecutive,
            max_consecutive=settings.core_max_consecutive_bot_replies,
            optout_detected=detect_optout(bodies, settings),
            explicit_handoff=detect_handoff(bodies, settings),
        )

        # The router runs ONLY when no deterministic path took over.
        route = decision.decision == Decision.HANDOFF and decision.handoff_reason == "bot_cannot_answer"
        if route and router is not None:
            from app.llm import budget
            budget_state = budget.ensure_and_check(
                conn, tenant_id=tenant_id, month=budget.current_month(),
                limit_micro_usd=settings.tenant_monthly_budget_micro_usd,
            )
            if budget_state == "degraded":
                metrics.turn_llm_skipped_total.labels("budget_degraded").inc()
                route = False

        plan = _TurnPlan(
            conversation_id=conversation_id, tenant_id=tenant_id,
            channel_id=channel_id, decision=decision,
            bodies=tuple(b for _, b in bodies if b), should_route=route,
        )

    # ---- Phase 2: router call + query embedding, OUTSIDE any transaction (H40) ----
    router_result = None
    if plan.should_route and router is not None:
        router_result = _route(settings, router, plan.bodies)
        if router_result.status == "breaker_open":
            metrics.turn_llm_skipped_total.labels("breaker_open").inc()

    # H42: the query vector is an OPTIMIZATION. Only product-search turns need it,
    # and any failure to obtain it (breaker/budget/timeout/dim) yields None so the
    # search falls back to two lexical lists - never an exception to the caller.
    query_vector: list[float] | None = None
    if (
        router_result is not None
        and router_result.decision.intent == "product_search"
        and router_result.decision.query
        and embed is not None
        and cache_client is not None
    ):
        from app.workers import embed as embed_module
        query_vector = embed_module.query_vector(
            settings=settings, handle=embed, query=router_result.decision.query,
            tenant_id=tenant_id, cache_client=cache_client,
        )

    # P1.7 (H40): the order lookup runs its own short read-tx + platform call +
    # short write-tx; the platform call must stay OUTSIDE any transaction.
    order_lookup = None
    if (
        router_result is not None
        and router_result.decision.intent == "order_status"
        and settings.tools_enabled
        and commerce is not None
    ):
        # N4 (P1.7 audit): the previous `if "track_order" in TOOLS: ... else:
        # tool_unknown_total.inc()` had an unreachable else - "track_order" is a
        # closed-literal member of TOOLS (enforced by S11-c), so the alert could
        # never fire. The dead metric/alert are removed; the 1:1 intent->tool
        # mapping means the order lookup is a direct, honest call.
        order_lookup = orders.run_order_lookup(
            settings, tenant_id=tenant_id, conversation_id=conversation_id,
            commerce=commerce, bodies=plan.bodies,
        )

    # ---- Phase 3: write + account (second short transaction) ----
    with core_db.tenant_tx(tenant_id) as conn:
        outcome = _write_phase(conn, settings, plan, router, router_result, query_vector=query_vector, rules=rules, order_lookup=order_lookup)

    metrics.turn_duration_seconds.observe(time.monotonic() - started)
    return outcome



def _write_phase(
    conn: Any, settings: WorkerSettings, plan: _TurnPlan,
    router: LlmRouterHandle | None, router_result: Any,
    query_vector: list[float] | None = None,
    rules: Any = None,
    order_lookup: Any = None,
) -> str:
    conv = repos_outbox.lock_conversation(conn, conversation_id=plan.conversation_id, tenant_id=plan.tenant_id)
    if conv is None:
        return "noop"
    if conv.bot_status != "active" or conv.last_processed_seq >= conv.last_inbound_seq:
        repos_outbox.mark_turn_processed(conn, conversation_id=plan.conversation_id, last_processed_seq=conv.last_inbound_seq)
        metrics.turn_skipped_total.labels("not_active").inc()
        return "skipped"

    if router_result is not None and router is not None:
        _account(conn, settings, plan, router, router_result)

    action = _resolve_action(conn, settings, plan, router_result, query_vector=query_vector, order_lookup=order_lookup)

    wa_id = repos_outbox.wa_id_for_conversation(conn, plan.conversation_id)
    expected_epoch = conv.epoch

    if action.handoff:
        reason = action.handoff_reason or "bot_cannot_answer"
        new_epoch = repos_outbox.set_bot_status(
            conn, conversation_id=conv.id, expected_version=conv.version,
            new_status="paused_human", reason=reason,
        )
        if new_epoch is None:
            raise TransientError("stale version while pausing bot")
        expected_epoch = new_epoch
        metrics.handoff_transitions_total.labels(reason).inc()
        handoff_payload = {"conversation_id": str(conv.id), "reason": reason}
        handoff_seq = repos_inbox.write_inbox_event(
            conn, tenant_id=plan.tenant_id, event_type="handoff.requested", payload=handoff_payload,
        )
        ws_publish.queue_publish(
            tenant_id=plan.tenant_id, seq=handoff_seq, event_type="handoff.requested",
            payload=handoff_payload,
        )
        metrics.inbox_events_written_total.labels("handoff.requested").inc()
        updated_payload = {
            "conversation_id": str(conv.id), "bot_status": "paused_human",
            "epoch": new_epoch, "version": conv.version + 1, "handoff_reason": reason,
        }
        updated_seq = repos_inbox.write_inbox_event(
            conn, tenant_id=plan.tenant_id, event_type="conversation.updated",
            payload=updated_payload,
        )
        ws_publish.queue_publish(
            tenant_id=plan.tenant_id, seq=updated_seq, event_type="conversation.updated",
            payload=updated_payload,
        )
        metrics.inbox_events_written_total.labels("conversation.updated").inc()

    turn_seq = conv.last_inbound_seq
    outcome = verify.insert_verified_outbox(
        conn, settings=settings, rules=rules,
        tenant_id=plan.tenant_id, conversation_id=conv.id,
        channel_account_id=plan.channel_id,
        idempotency_key=f"{conv.id}:{turn_seq}:1",
        message_class="service", expected_epoch=expected_epoch,
        to_wa_id=wa_id, template_id=action.template_id, text=action.text,
    )
    repos_outbox.mark_turn_processed(
        conn, conversation_id=conv.id, last_processed_seq=conv.last_inbound_seq,
    )
    metrics.turn_processed_total.labels(action.outcome).inc()
    if outcome.ok:
        metrics.outbox_written_total.labels("service").inc()
        if action.kind is not None:
            metrics.compose_replies_total.labels(action.kind).inc()
    return action.outcome

