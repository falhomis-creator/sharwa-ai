"""F-P4-09: a REAL conversation turn that goes to the LLM router, end to end on
PostgreSQL (process_turn + the deterministic fake provider + the real Verifier
rules). Before the fix every routed turn crashed in turn._account
(RouteResult.usage was an LlmJsonResult read as LlmUsage) - the write
transaction aborted and the customer got no reply at all."""
from __future__ import annotations

import dataclasses
import os
import uuid

import pytest

from app import db as core_db
from app.db import repos_stock
from app.db import testsupport as db_testsupport
from app.llm.breaker import CircuitBreaker
from app.llm.port import LlmUsage
from app.llm.router import run_router
from app.workers import turn, verify
from app.workers.config import WorkerSettings
from tests.fake_llm import FakeLlmProvider

pytestmark = pytest.mark.db

TENANT_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _settings(**overrides: object) -> WorkerSettings:
    required = {
        f.name for f in dataclasses.fields(WorkerSettings)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    }
    values: dict[str, object] = dict.fromkeys(required)
    values.update(
        core_max_consecutive_bot_replies=5,
        core_optout_phrases_ar=("ايقاف",), core_optout_phrases_en=("stop",),
        core_optin_phrases_ar=("اشتراك",), core_optin_phrases_en=("subscribe",),
        core_handoff_phrases_ar=("موظف",), core_handoff_phrases_en=("agent",),
        env="test",
    )
    return dataclasses.replace(WorkerSettings(**values), **overrides)


@pytest.fixture()
def ctx():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.delete_tenant_full(dsn, TENANT_A)
    db_testsupport.delete_tenant_full(dsn, TENANT_B)
    db_testsupport.seed_two_tenants(dsn, TENANT_A, TENANT_B)
    chid = db_testsupport.insert_channel_account(
        dsn, tenant_id=TENANT_A, type_="whatsapp_baileys", session_id=f"sess-{uuid.uuid4()}",
    )
    wa = f"9677{uuid.uuid4().int % 10**10:010d}"
    cid = db_testsupport.insert_customer(dsn, tenant_id=TENANT_A, wa_id=wa)
    conv = db_testsupport.seed_conversation(dsn, tenant_id=TENANT_A, channel_id=chid, customer_id=cid)
    yield dsn, conv
    db_testsupport.delete_tenant_full(dsn, TENANT_A)
    db_testsupport.delete_tenant_full(dsn, TENANT_B)


def _routed_turn(dsn: str, conv: uuid.UUID, body: str, **overrides: object) -> tuple[str, dict, list[dict]]:
    db_testsupport.seed_inbound_message(dsn, tenant_id=TENANT_A, conversation_id=conv, body=body)
    settings = _settings(**overrides)
    router = turn.build_router(settings)
    outcome = turn.process_turn(
        settings=settings, conversation_id=conv, tenant_id=TENANT_A,
        rules=verify.build_rules(settings), router=router,
    )
    with core_db.tenant_tx(TENANT_A) as conn:
        locked = turn.repos_outbox.lock_conversation(conn, conversation_id=conv, tenant_id=TENANT_A)
    assert locked is not None
    row = db_testsupport.fetch_outbox_row_by_idempotency(dsn, TENANT_A, f"{conv}:{locked.last_inbound_seq}:1")
    assert row is not None, "the routed turn wrote no reply"
    return outcome, row["payload"], router.provider.calls


def test_routed_turn_replies_and_accounts_the_call(ctx):
    dsn, conv = ctx
    outcome, payload, calls = _routed_turn(dsn, conv, "مرحبا")
    assert len(calls) == 1                       # the model was consulted once
    assert outcome == "handoff"                  # 'other' => the safe default hand-off
    assert payload["template"] == "handoff_notice"
    assert db_testsupport.fetch_llm_calls(dsn, TENANT_A) == [("router", "fake", "ok")]


def test_routed_product_search_shows_cards_and_remembers_them(ctx):
    dsn, conv = ctx
    db_testsupport.seed_catalog_product(
        dsn, tenant_id=TENANT_A, platform_product_id="P-SHIRT", category=None, title="منتج قميص قطني",
    )
    outcome, payload, calls = _routed_turn(dsn, conv, "منتج قميص")
    assert len(calls) == 1
    assert outcome == "product_list"
    assert payload["template"] == "product_list"
    assert "منتج قميص قطني" in payload["text"]
    with core_db.tenant_tx(TENANT_A) as conn:      # F-P4-07 works end to end now
        slots = repos_stock.read_conversation_slots(conn, conversation_id=conv)
    assert slots["last_shown_product_ids"] == ["P-SHIRT"]
    assert db_testsupport.fetch_llm_calls(dsn, TENANT_A) == [("router", "fake", "ok")]


def test_a_non_size_message_still_routes_when_size_advice_is_on(ctx):
    # The half of test_size_turn_db.test_size_turn_never_calls_the_model that
    # F-P4-09 blocked: with SIZE_ADVICE_ENABLED on, a non-size message still goes
    # to the router exactly as before.
    dsn, conv = ctx
    outcome, payload, calls = _routed_turn(dsn, conv, "مرحبا", size_advice_enabled=True)
    assert len(calls) == 1
    assert (outcome, payload["template"]) == ("handoff", "handoff_notice")


def test_run_router_hands_over_the_usage_itself():
    # The contract _account relies on: RouteResult.usage is an LlmUsage.
    result = run_router(
        FakeLlmProvider(), CircuitBreaker(5, 60.0), provider_name="fake",
        messages=[{"role": "user", "content": "مرحبا"}],
        min_confidence=0.6, max_output_tokens=64, timeout_s=8.0,
    )
    assert isinstance(result.usage, LlmUsage)
    assert (result.usage.provider, result.usage.model) == ("fake", "fake-router")
