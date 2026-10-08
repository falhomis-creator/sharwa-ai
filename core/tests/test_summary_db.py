"""F-P4-08: repos_summary.update_summary on real PostgreSQL. The function passed
three parameters for four placeholders (and in the wrong order), so every
summary write raised ProgrammingError - conversation summaries were never
stored. No db test covered it; these do."""
from __future__ import annotations

import os
import uuid

import pytest

from app import db as core_db
from app.db import repos_summary
from app.db import testsupport as db_testsupport

pytestmark = pytest.mark.db

TENANT_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture()
def conv():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    db_testsupport.delete_tenant_full(dsn, TENANT_A)
    db_testsupport.delete_tenant_full(dsn, TENANT_B)
    db_testsupport.seed_two_tenants(dsn, TENANT_A, TENANT_B)
    chid = db_testsupport.insert_channel_account(
        dsn, tenant_id=TENANT_A, type_="whatsapp_baileys", session_id=f"sess-{uuid.uuid4()}",
    )
    wa = f"9677{uuid.uuid4().int % 10**10:010d}"
    cid = db_testsupport.insert_customer(dsn, tenant_id=TENANT_A, wa_id=wa)
    yield db_testsupport.seed_conversation(dsn, tenant_id=TENANT_A, channel_id=chid, customer_id=cid)
    db_testsupport.delete_tenant_full(dsn, TENANT_A)
    db_testsupport.delete_tenant_full(dsn, TENANT_B)


def _state(tenant: uuid.UUID, conversation_id: uuid.UUID):
    with core_db.tenant_tx(tenant) as conn:
        return repos_summary.read_summary_state(conn, tenant_id=tenant, conversation_id=conversation_id)


def test_update_summary_stores_the_text_and_merges_slots(conv):
    with core_db.tenant_tx(TENANT_A) as conn:
        repos_summary.record_shown_products(
            conn, tenant_id=TENANT_A, conversation_id=conv, platform_product_ids=["P1"],
        )
    with core_db.tenant_tx(TENANT_A) as conn:
        repos_summary.update_summary(
            conn, tenant_id=TENANT_A, conversation_id=conv,
            summary="العميل يسأل عن قميص", slots={"summary_seq": 12},
        )
    state = _state(TENANT_A, conv)
    assert state is not None
    summary, slots, _last_inbound_seq, _message_seq = state
    assert summary == "العميل يسأل عن قميص"
    # merged: the new key is written, the existing product memory is preserved
    assert slots == {"last_shown_product_ids": ["P1"], "summary_seq": 12}


def test_a_second_summary_replaces_the_text_and_the_seq(conv):
    for text, seq in (("الأول", 5), ("الثاني", 9)):
        with core_db.tenant_tx(TENANT_A) as conn:
            repos_summary.update_summary(
                conn, tenant_id=TENANT_A, conversation_id=conv, summary=text, slots={"summary_seq": seq},
            )
    state = _state(TENANT_A, conv)
    assert state is not None
    assert (state[0], state[1]["summary_seq"]) == ("الثاني", 9)


def test_another_tenant_cannot_write_the_summary(conv):
    with core_db.tenant_tx(TENANT_B) as conn:  # RLS: B sees no such conversation
        repos_summary.update_summary(
            conn, tenant_id=TENANT_B, conversation_id=conv, summary="EVIL", slots={"summary_seq": 1},
        )
    state = _state(TENANT_A, conv)
    assert state is not None
    assert state[0] is None
    assert "summary_seq" not in state[1]


def test_unknown_slot_key_is_rejected_before_any_write(conv):
    with core_db.tenant_tx(TENANT_A) as conn, pytest.raises(ValueError):
        repos_summary.update_summary(
            conn, tenant_id=TENANT_A, conversation_id=conv, summary="x", slots={"model_text": "x"},
        )
    state = _state(TENANT_A, conv)
    assert state is not None
    assert state[0] is None
