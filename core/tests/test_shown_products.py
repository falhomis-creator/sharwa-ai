"""P4 Task 18b-2a (F-P4-07): product memory - pure tests. The slot
last_shown_product_ids carries PRODUCT ids written by code after a product_list
reply; the waitlist coordinator resolves the product's single variant."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.tools import join_waitlist as join_waitlist_tool
from app.tools.registry import ToolContext
from app.workers import stock, turn
from app.workers.turn import _Action, _TurnPlan


def _ctx(ids: tuple[str, ...]) -> ToolContext:
    return ToolContext(
        tenant_id=uuid.uuid4(), conversation_id=uuid.uuid4(), tenant_ref="",
        channel_phone_e164=None, message_texts=(), commerce=None, settings=None,
        order_ref="", phone_candidates=(), path="other_number", last_shown_product_ids=ids,
    )


# ---- the pure tool picks a PRODUCT, never a variant ---------------------------

def test_tool_picks_the_last_shown_product():
    d = join_waitlist_tool.run(_ctx(("P1", "P2", "P3")))
    assert (d.kind, d.platform_product_id, d.platform_variant_id) == ("joined", "P3", None)


def test_tool_without_shown_products_is_no_variant():
    assert join_waitlist_tool.run(_ctx(())).kind == "no_variant"


# ---- the coordinator joins only on exactly one variant --------------------------

def _patch_join(monkeypatch, variants: list[str], inserts: list[dict]) -> None:
    monkeypatch.setattr(stock.repos_outbox, "customer_id_for_conversation", lambda conn, cid: uuid.uuid4())
    monkeypatch.setattr(stock.repos_stock, "read_conversation_slots",
                        lambda conn, **kw: {"last_shown_product_ids": ["PROD-1"]})
    monkeypatch.setattr(stock.repos_stock, "variant_ids_for_product", lambda conn, **kw: list(variants))
    monkeypatch.setattr(stock.repos_stock, "has_active_waitlist", lambda conn, **kw: False)
    monkeypatch.setattr(stock.repos_stock, "count_active_waitlists", lambda conn, **kw: 0)
    monkeypatch.setattr(stock.repos_stock, "insert_waitlist_entry",
                        lambda conn, **kw: (inserts.append(kw) or uuid.uuid4()))
    monkeypatch.setattr(stock.repos_consent, "record_waitlist_join", lambda conn, **kw: "granted")


def _join():
    settings = SimpleNamespace(stock_max_waitlist_per_customer=10)
    return stock.join_waitlist(None, settings, tenant_id=uuid.uuid4(),
                               conversation_id=uuid.uuid4(), bodies=("سجّلني",))


def test_single_variant_product_joins_with_that_variant(monkeypatch):
    inserts: list[dict] = []
    _patch_join(monkeypatch, ["VAR-1"], inserts)
    d = _join()
    assert (d.kind, d.platform_variant_id, d.platform_product_id) == ("joined", "VAR-1", "PROD-1")
    assert [i["platform_variant_id"] for i in inserts] == ["VAR-1"]


def test_multi_variant_product_never_guesses(monkeypatch):
    inserts: list[dict] = []
    _patch_join(monkeypatch, ["VAR-S", "VAR-M"], inserts)
    assert _join().kind == "no_variant"
    assert inserts == []


def test_product_without_variants_is_no_variant(monkeypatch):
    inserts: list[dict] = []
    _patch_join(monkeypatch, [], inserts)
    assert _join().kind == "no_variant"
    assert inserts == []


# ---- the turn: which cards count as "shown" ------------------------------------

def _plan() -> _TurnPlan:
    return _TurnPlan(conversation_id=uuid.uuid4(), tenant_id=uuid.uuid4(), channel_id=uuid.uuid4(),
                     decision=None, bodies=(), should_route=True)


def _card(i: int) -> dict:
    return {"product_id": str(uuid.uuid4()), "platform_product_id": f"PP-{i}", "title": f"منتج {i}",
            "category": None, "options_summary": {}}


def test_product_list_action_carries_the_three_shown_product_ids(monkeypatch):
    monkeypatch.setattr(turn.repos_catalog, "search_products",
                        lambda conn, **kw: [_card(i) for i in range(4)])
    router_result = SimpleNamespace(decision=SimpleNamespace(intent="product_search", query="قميص"))
    action = turn._resolve_action(None, None, _plan(), router_result)
    assert action.template_id == "product_list"
    assert action.shown_product_ids == ("PP-0", "PP-1", "PP-2")


def test_other_actions_carry_no_shown_products():
    assert turn._deterministic_action(turn.decide(
        kill_switch_state=None, consecutive_bot_replies=0, max_consecutive=5,
        consent_word=None, explicit_handoff=False,
    )).shown_product_ids == ()


class _Counter:
    def inc(self, *a):
        return None

    def labels(self, *a):
        return self


def _write(monkeypatch, action: _Action, ok: bool) -> list[dict]:
    conv = SimpleNamespace(id=uuid.uuid4(), bot_status="active", epoch=1,
                           version=1, last_inbound_seq=5, last_processed_seq=0)
    recorded: list[dict] = []
    monkeypatch.setattr(turn.repos_outbox, "lock_conversation",
                        lambda conn, conversation_id, tenant_id: conv)
    monkeypatch.setattr(turn.repos_outbox, "wa_id_for_conversation", lambda conn, cid: "wa-1")
    monkeypatch.setattr(turn.repos_outbox, "mark_turn_processed",
                        lambda conn, conversation_id, last_processed_seq: None)
    monkeypatch.setattr(turn, "_resolve_action",
                        lambda conn, settings, plan, router_result, query_vector=None,
                        order_lookup=None: action)
    for name in ("turn_processed_total", "turn_skipped_total",
                 "outbox_written_total", "compose_replies_total"):
        monkeypatch.setattr(turn.metrics, name, _Counter())
    monkeypatch.setattr(turn.verify, "insert_verified_outbox", lambda conn, **kw: SimpleNamespace(ok=ok))
    monkeypatch.setattr(turn.repos_summary, "record_shown_products", lambda conn, **kw: recorded.append(kw))
    turn._write_phase(None, None, _plan(), None, None, rules=None)
    return recorded


def test_shown_products_are_recorded_only_after_the_reply_is_accepted(monkeypatch):
    shown = _Action("product_list", "نص", False, None, "product_list", "product_list",
                    shown_product_ids=("PP-0", "PP-1"))
    recorded = _write(monkeypatch, shown, ok=True)
    assert [r["platform_product_ids"] for r in recorded] == [["PP-0", "PP-1"]]
    assert _write(monkeypatch, shown, ok=False) == []  # verifier rejected => nothing was shown


def test_non_product_reply_records_nothing(monkeypatch):
    plain = _Action("safe_ack", "نص", False, None, "template", "safe_ack")
    assert _write(monkeypatch, plain, ok=True) == []
