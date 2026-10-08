"""P4 Task 18b-2b: size advice in the REAL conversation turn (process_turn) on
PostgreSQL - the first end-to-end turn test. Flag off => the turn is unchanged
(the generic hand-off); flag on => a deterministic, verified size reply."""
from __future__ import annotations

import dataclasses
import os
import uuid

import pytest

from app import db as core_db
from app.db import repos_summary
from app.db import testsupport as db_testsupport
from app.workers import turn, verify
from app.workers.config import WorkerSettings

pytestmark = pytest.mark.db

TENANT_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = uuid.UUID("22222222-2222-2222-2222-222222222222")

_SML = [
    {"size_label": "S", "sort_order": 1, "height_cm": "[150,165]", "weight_kg": "[45,60]"},
    {"size_label": "M", "sort_order": 2, "height_cm": "[165,175]", "weight_kg": "[60,75]"},
    {"size_label": "L", "sort_order": 3, "height_cm": "[175,190]", "weight_kg": "[75,95]"},
]


def _settings(*, enabled: bool) -> WorkerSettings:
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
    return dataclasses.replace(WorkerSettings(**values), size_advice_enabled=enabled)


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
    db_testsupport.seed_size_chart(
        dsn, tenant_id=TENANT_A, scope_type="product", scope_ref="P-1", rows=_SML,
    )
    yield dsn, conv
    db_testsupport.delete_tenant_full(dsn, TENANT_A)
    db_testsupport.delete_tenant_full(dsn, TENANT_B)


def _show(conv: uuid.UUID, products: list[str]) -> None:
    with core_db.tenant_tx(TENANT_A) as conn:
        repos_summary.record_shown_products(
            conn, tenant_id=TENANT_A, conversation_id=conv, platform_product_ids=products,
        )


def _turn(
    dsn: str, conv: uuid.UUID, body: str, *, enabled: bool, router: object = None,
) -> tuple[str, dict]:
    db_testsupport.seed_inbound_message(dsn, tenant_id=TENANT_A, conversation_id=conv, body=body)
    settings = _settings(enabled=enabled)
    outcome = turn.process_turn(  # the real boot-time Verifier rules (the worker's path)
        settings=settings, conversation_id=conv, tenant_id=TENANT_A,
        rules=verify.build_rules(settings), router=router,
    )
    with core_db.tenant_tx(TENANT_A) as conn:
        locked = turn.repos_outbox.lock_conversation(conn, conversation_id=conv, tenant_id=TENANT_A)
    assert locked is not None
    seq = locked.last_inbound_seq
    row = db_testsupport.fetch_outbox_row_by_idempotency(dsn, TENANT_A, f"{conv}:{seq}:1")
    assert row is not None
    return outcome, row["payload"]


def test_flag_off_keeps_todays_generic_handoff(ctx):
    dsn, conv = ctx
    _show(conv, ["P-1"])
    outcome, payload = _turn(dsn, conv, "طولي 170 ووزني 65 ايش مقاسي", enabled=False)
    assert outcome == "handoff"
    assert payload["template"] == "handoff_notice"


def test_flag_on_answers_with_the_verified_size(ctx):
    dsn, conv = ctx
    _show(conv, ["P-1"])
    outcome, payload = _turn(dsn, conv, "طولي 170 ووزني 65 ايش مقاسي", enabled=True)
    assert outcome == "size_recommend"
    assert payload["template"] == "size_recommend"
    assert payload["text"] == "المقاس المناسب لك: مقاس M ✅"


def test_flag_on_missing_inputs_asks_without_handoff(ctx):
    dsn, conv = ctx
    _show(conv, ["P-1"])
    outcome, payload = _turn(dsn, conv, "كم مقاسي؟", enabled=True)
    assert outcome == "size_need_inputs"
    assert payload["template"] == "size_need_inputs"


def test_flag_on_several_shown_products_hands_off(ctx):
    dsn, conv = ctx
    _show(conv, ["P-1", "P-2"])
    outcome, payload = _turn(dsn, conv, "طولي 170 ووزني 65 ايش مقاسي", enabled=True)
    assert outcome == "size_no_product"
    assert payload["template"] == "handoff_notice"


def test_flag_on_product_without_chart_hands_off(ctx):
    dsn, conv = ctx
    _show(conv, ["P-NO-CHART"])
    outcome, payload = _turn(dsn, conv, "طولي 170 ووزني 65 ايش مقاسي", enabled=True)
    assert outcome == "size_no_chart"
    assert payload["template"] == "size_no_chart"


def test_flag_on_non_size_message_is_unchanged(ctx):
    dsn, conv = ctx
    _show(conv, ["P-1"])
    outcome, payload = _turn(dsn, conv, "مرحبا", enabled=True)
    assert outcome == "handoff"
    assert payload["template"] == "handoff_notice"


def test_size_turn_never_calls_the_model(ctx):
    # The size path is deterministic (owner decision: code rule, no router): even
    # with a live router handle the model is not consulted for a size question.
    dsn, conv = ctx
    _show(conv, ["P-1"])
    router = turn.build_router(_settings(enabled=True))
    outcome, _payload = _turn(dsn, conv, "طولي 170 ووزني 65 ايش مقاسي", enabled=True, router=router)
    assert outcome == "size_recommend"
    assert router.provider.calls == []
    # NOTE (F-P4-09, open): a ROUTED turn currently crashes in turn._account
    # (RouteResult.usage is an LlmJsonResult, read as LlmUsage) - reproduced on
    # 50bdae0 without this task. The "non-size still routes" half of this test
    # belongs to the F-P4-09 fix, not here.

