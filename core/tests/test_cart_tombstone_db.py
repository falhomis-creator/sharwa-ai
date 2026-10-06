"""core/tests/test_cart_tombstone_db.py - F-P3-24 (0019, owner decision OQ-P3-19=A).

A terminal cart event that arrives BEFORE the first cart.updated must never let
a late cart.updated open the cart or schedule a reminder; and a tombstone that
commits concurrently must stop the reminder at send time (H89). Real HTTP for
the webhook (same harness as test_routes_carts_db), real scheduler for the
reminder (same harness as test_cart_reminder_db).
"""
from __future__ import annotations

import uuid

import pytest

from app.db import testsupport as db_testsupport
from app.workers import scheduler as engine

from tests.test_cart_reminder_db import NOW as REM_NOW, _settings, handler_ctx  # noqa: F401
from tests.test_routes_carts_db import (  # noqa: F401
    NOW, _post, _seed_customer_with_cart_channel, _updated, carts_client, carts_tenant,
)

pytestmark = pytest.mark.db


@pytest.mark.parametrize("etype,final", [("cart.recovered", "recovered"), ("cart.cleared", "cleared")])
def test_terminal_before_first_update_never_opens_the_cart(carts_client, carts_tenant, etype, final):
    dsn, platform_ref, tid = carts_tenant
    _seed_customer_with_cart_channel(dsn, tid)
    first = _post(carts_client, {"tenant_ref": platform_ref, "events": [{
        "type": etype, "cart_id": "CART-TOMB", "occurred_at": NOW.isoformat(),
    }]})
    assert first.status_code == 200
    assert first.json()["counts"] == {"already_final": 1}  # closed vocabulary unchanged
    assert db_testsupport.fetch_cart_tombstone(dsn, tid, "CART-TOMB") == final

    late = _post(carts_client, {"tenant_ref": platform_ref,
                                "events": [_updated("CART-TOMB", wa_id="967733000001")]})
    assert late.json()["counts"] == {"already_final": 1}
    assert db_testsupport.fetch_cart(dsn, tid, "CART-TOMB") is None, "a tombstoned cart never opens"
    assert db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "cart:CART-TOMB:stage1") is None


def test_terminal_for_an_existing_cart_writes_no_tombstone(carts_client, carts_tenant):
    dsn, platform_ref, tid = carts_tenant
    _seed_customer_with_cart_channel(dsn, tid)
    _post(carts_client, {"tenant_ref": platform_ref,
                         "events": [_updated("CART-SEEN", wa_id="967733000001")]})
    resp = _post(carts_client, {"tenant_ref": platform_ref, "events": [{
        "type": "cart.recovered", "cart_id": "CART-SEEN", "occurred_at": NOW.isoformat(),
    }]})
    assert resp.json()["counts"] == {"applied": 1}
    assert db_testsupport.fetch_cart_tombstone(dsn, tid, "CART-SEEN") is None


def test_repeated_terminal_events_keep_one_tombstone(carts_client, carts_tenant):
    dsn, platform_ref, tid = carts_tenant
    for etype in ("cart.recovered", "cart.cleared", "cart.recovered"):
        resp = _post(carts_client, {"tenant_ref": platform_ref, "events": [{
            "type": etype, "cart_id": "CART-REP", "occurred_at": NOW.isoformat(),
        }]})
        assert resp.status_code == 200
    assert db_testsupport.fetch_cart_tombstone(dsn, tid, "CART-REP") == "recovered", "first terminal wins"


def test_concurrent_tombstone_stops_the_reminder_at_send_time(handler_ctx):
    """The race the webhook guard cannot see: the cart opened, then a tombstone
    committed. The reminder re-reads it under the cart lock: cancel, no outbox,
    and the cart is closed with the tombstoned status."""
    dsn, tid, _chid, _cid, _conv, job_id = handler_ctx
    db_testsupport.insert_cart_tombstone(dsn, tenant_id=tid, platform_cart_id="CART-H",
                                         status="recovered")
    engine.run_due(_settings(), now=REM_NOW)
    job = db_testsupport.fetch_scheduled_job(dsn, job_id)
    assert job["status"] == "cancelled" and job["cancel_reason"] == "cart_closed"
    assert db_testsupport.count_outbox_by_idempotency_key(dsn, tid, "cart:CART-H:stage1") == 0
    assert db_testsupport.fetch_cart(dsn, tid, "CART-H")["status"] == "recovered"


def test_tombstones_are_tenant_isolated(carts_client, carts_tenant):
    """A tombstone in tenant A must not stop the same cart_id in tenant B (RLS)."""
    dsn, platform_ref, _tid = carts_tenant
    other_ref = f"carts-{uuid.uuid4()}"
    other = db_testsupport.insert_tenant_returning_id(dsn, platform_ref=other_ref, name="Other Tenant")
    try:
        _seed_customer_with_cart_channel(dsn, other)
        _post(carts_client, {"tenant_ref": platform_ref, "events": [{
            "type": "cart.recovered", "cart_id": "CART-X", "occurred_at": NOW.isoformat(),
        }]})
        resp = _post(carts_client, {"tenant_ref": other_ref,
                                    "events": [_updated("CART-X", wa_id="967733000001")]})
        assert resp.json()["counts"] == {"applied": 1}, "another tenant's tombstone must not leak"
        assert db_testsupport.fetch_cart(dsn, other, "CART-X")["status"] == "open"
    finally:
        db_testsupport.delete_tenant_full(dsn, other)
