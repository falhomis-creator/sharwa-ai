"""core/tests/test_routes_carts_db.py - P3.2 Stage D: the cart webhook over
REAL HTTP (TestClient against the assembled app.main, the conftest session DB
pools), with the platform HMAC contract signed exactly as the store will.

§5.10's full matrix + §5.2 (ordering/duplication) + §5.3's webhook half. The
router's mounting is proven by every request reaching it (S18 guards removal
statically); no item title or phone ever appears in the captured logs (H91).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import testsupport as db_testsupport

pytestmark = pytest.mark.db

NOW = datetime.now(timezone.utc)


def _sign(raw: bytes, ts: str) -> str:
    secret = os.environ["PLATFORM_WEBHOOK_SECRET"].encode()
    return hmac.new(secret, f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()


@pytest.fixture()
def carts_client(monkeypatch):
    import app.main as main_module
    monkeypatch.setattr(main_module.core_db, "init_pool", lambda *a, **kw: None)
    monkeypatch.setattr(main_module.core_db, "close_pool", lambda: None)
    from app.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def carts_tenant():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    platform_ref = f"carts-{uuid.uuid4()}"
    tid = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=platform_ref, name="Carts Tenant",
    )
    yield dsn, platform_ref, tid
    db_testsupport.delete_tenant_full(dsn, tid)


def _post(c, payload, *, ts: str | None = None, signature: str | None = None,
          raw: bytes | None = None):
    body = raw if raw is not None else json.dumps(payload).encode()
    timestamp = ts if ts is not None else str(int(time.time()))
    sig = signature if signature is not None else _sign(body, timestamp)
    return c.post(
        "/webhooks/platform/cart",
        content=body,
        headers={"X-Platform-Signature": sig, "X-Platform-Timestamp": timestamp},
    )


def _updated(cart_id: str, wa_id: str | None = None, phone: str | None = None,
             occurred_at: datetime | None = None, title: str = "قميص صيفي",
             item_count: int = 2):
    customer: dict = {}
    if wa_id is not None:
        customer["wa_id"] = wa_id
    if phone is not None:
        customer["phone_e164"] = phone
    return {
        "type": "cart.updated", "cart_id": cart_id,
        "occurred_at": (occurred_at or NOW).isoformat(),
        "customer": customer, "item_count": item_count, "total_minor": 5000,
        "currency": "SAR",
        "items": [{"title": title}, {"title": "بنطال"}, {"title": "حزام"}, {"title": "زائد"}][:3],
    }


# --- §5.10: the HTTP contract matrix -----------------------------------------


def test_bad_signature_is_401(carts_client, carts_tenant):
    _dsn, platform_ref, _tid = carts_tenant
    body = json.dumps({"tenant_ref": platform_ref, "events": []}).encode()
    resp = carts_client.post(
        "/webhooks/platform/cart", content=body,
        headers={"X-Platform-Signature": "0" * 64, "X-Platform-Timestamp": str(int(time.time()))},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "PLATFORM_SIGNATURE_INVALID"


def test_stale_timestamp_is_401(carts_client, carts_tenant):
    _dsn, platform_ref, _tid = carts_tenant
    stale = str(int(time.time()) - 3600)
    resp = _post(carts_client, {"tenant_ref": platform_ref, "events": []}, ts=stale)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "PLATFORM_TIMESTAMP_SKEW"


def test_oversized_body_is_413(carts_client, carts_tenant):
    _dsn, platform_ref, _tid = carts_tenant
    big = b"x" * (262_144 + 1)
    resp = _post(carts_client, None, raw=big)
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "PLATFORM_PAYLOAD_TOO_LARGE"


def test_invalid_json_is_422(carts_client, carts_tenant):
    resp = _post(carts_client, None, raw=b"{not-json")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


@pytest.mark.parametrize("event", [
    {"type": "cart.updated", "cart_id": "", "occurred_at": NOW.isoformat(),
     "customer": {"wa_id": "96777000001"}, "item_count": 1, "total_minor": 1,
     "currency": "SAR", "items": []},
    {"type": "cart.updated", "cart_id": "C1", "occurred_at": "not-a-date",
     "customer": {"wa_id": "96777000001"}, "item_count": 1, "total_minor": 1,
     "currency": "SAR", "items": []},
    {"type": "cart.updated", "cart_id": "C1", "occurred_at": NOW.isoformat(),
     "customer": {}, "item_count": 1, "total_minor": 1, "currency": "SAR", "items": []},
    {"type": "cart.updated", "cart_id": "C1", "occurred_at": NOW.isoformat(),
     "customer": {"wa_id": "96777000001"}, "item_count": -1, "total_minor": 1,
     "currency": "SAR", "items": []},
    {"type": "cart.updated", "cart_id": "C1", "occurred_at": NOW.isoformat(),
     "customer": {"wa_id": "96777000001"}, "item_count": 1, "total_minor": 1,
     "currency": "SA", "items": []},
    {"type": "cart.updated", "cart_id": "C1", "occurred_at": NOW.isoformat(),
     "customer": {"wa_id": "96777000001"}, "item_count": 1, "total_minor": 1,
     "currency": "SAR", "items": [{"title": "a"}, {"title": "b"}, {"title": "c"}, {"title": "d"}]},
])
def test_bad_event_schema_is_422(carts_client, carts_tenant, event):
    _dsn, platform_ref, _tid = carts_tenant
    resp = _post(carts_client, {"tenant_ref": platform_ref, "events": [event]})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


def test_unknown_tenant_is_200_ignored(carts_client):
    resp = _post(carts_client, {"tenant_ref": "no-such-tenant", "events": []})
    assert resp.status_code == 200
    assert resp.json()["status"] == "unknown_tenant_ignored"


def test_no_customer_match_ignores_event_and_stores_nothing(carts_client, carts_tenant):
    dsn, platform_ref, tid = carts_tenant
    resp = _post(carts_client, {
        "tenant_ref": platform_ref,
        "events": [_updated("CART-NOMATCH", wa_id="967799999999")],
    })
    assert resp.status_code == 200
    assert resp.json()["counts"] == {"ignored_no_customer": 1}
    assert db_testsupport.fetch_cart(dsn, tid, "CART-NOMATCH") is None
    assert db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "cart:CART-NOMATCH:stage1") is None


def test_unknown_event_type_counted_and_never_fails(carts_client, carts_tenant):
    _dsn, platform_ref, _tid = carts_tenant
    resp = _post(carts_client, {
        "tenant_ref": platform_ref,
        "events": [
            {"type": "cart.exploded", "cart_id": "C1", "occurred_at": NOW.isoformat()},
            {"type": "cart.exploded", "cart_id": "C2", "occurred_at": NOW.isoformat()},
        ],
    })
    assert resp.status_code == 200
    assert resp.json()["counts"] == {"unknown_type": 2}


# --- §5.2/§5.3: application semantics -----------------------------------------


def _seed_customer_with_cart_channel(dsn, tid):
    chid = db_testsupport.insert_channel_account(
        dsn, tenant_id=tid, type_="whatsapp_baileys",
        session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
    )
    cid = db_testsupport.insert_customer(dsn, tenant_id=tid, wa_id="967733000001")
    return chid, cid


def test_updated_creates_open_cart_and_schedules_reminder(carts_client, carts_tenant, caplog):
    dsn, platform_ref, tid = carts_tenant
    _chid, _cid = _seed_customer_with_cart_channel(dsn, tid)
    occurred = NOW.replace(microsecond=0)
    with caplog.at_level("INFO"):
        resp = _post(carts_client, {
            "tenant_ref": platform_ref,
            "events": [_updated("CART-OK", wa_id="967733000001", occurred_at=occurred,
                                title="سر-عنوان-لا-يظهر-في-السجل")],
        })
    assert resp.status_code == 200
    assert resp.json()["counts"] == {"applied": 1}

    cart = db_testsupport.fetch_cart(dsn, tid, "CART-OK")
    assert cart is not None and cart["status"] == "open"
    assert cart["snapshot"]["item_count"] == 2
    assert len(cart["snapshot"]["items"]) == 3  # H91: at most 3 titles stored

    job = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "cart:CART-OK:stage1")
    assert job is not None and job["kind"] == "cart_reminder"
    assert job["status"] == "pending" and job["attempts"] == 0
    assert abs((job["run_at"] - (occurred + timedelta(hours=24))).total_seconds()) < 1

    # H91: neither the item title nor the phone ever reaches a log line.
    assert "سر-عنوان-لا-يظهر-في-السجل" not in caplog.text
    assert "967733000001" not in caplog.text


def test_duplicate_event_is_a_noop(carts_client, carts_tenant):
    dsn, platform_ref, tid = carts_tenant
    _seed_customer_with_cart_channel(dsn, tid)
    occurred = NOW.replace(microsecond=0)
    payload = {"tenant_ref": platform_ref,
               "events": [_updated("CART-DUP", wa_id="967733000001", occurred_at=occurred)]}
    first = _post(carts_client, payload)
    assert first.json()["counts"] == {"applied": 1}
    before = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "cart:CART-DUP:stage1")

    second = _post(carts_client, payload)  # the SAME payload twice
    assert second.status_code == 200
    after = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "cart:CART-DUP:stage1")
    assert after["run_at"] == before["run_at"], "a duplicate must not move time"
    assert after["attempts"] == 0


def test_newer_event_pushes_job_older_event_does_not_rewind(carts_client, carts_tenant):
    dsn, platform_ref, tid = carts_tenant
    _seed_customer_with_cart_channel(dsn, tid)
    t0 = NOW.replace(microsecond=0)
    _post(carts_client, {"tenant_ref": platform_ref,
                         "events": [_updated("CART-ORD", wa_id="967733000001", occurred_at=t0)]})
    base = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "cart:CART-ORD:stage1")

    older = t0 - timedelta(hours=2)  # out-of-order arrival
    _post(carts_client, {"tenant_ref": platform_ref,
                         "events": [_updated("CART-ORD", wa_id="967733000001", occurred_at=older)]})
    same = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "cart:CART-ORD:stage1")
    assert same["run_at"] == base["run_at"], "an older event must not rewind the clock"

    newer = t0 + timedelta(hours=3)  # fresh activity pushes the reminder out
    _post(carts_client, {"tenant_ref": platform_ref,
                         "events": [_updated("CART-ORD", wa_id="967733000001", occurred_at=newer)]})
    moved = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "cart:CART-ORD:stage1")
    assert abs((moved["run_at"] - (newer + timedelta(hours=24))).total_seconds()) < 1


def test_recovered_finalizes_cart_and_cancels_job_same_tx(carts_client, carts_tenant):
    dsn, platform_ref, tid = carts_tenant
    _seed_customer_with_cart_channel(dsn, tid)
    _post(carts_client, {"tenant_ref": platform_ref,
                         "events": [_updated("CART-REC", wa_id="967733000001")]})
    job = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "cart:CART-REC:stage1")
    assert job["status"] == "pending"

    resp = _post(carts_client, {"tenant_ref": platform_ref, "events": [{
        "type": "cart.recovered", "cart_id": "CART-REC",
        "occurred_at": NOW.isoformat(),
    }]})
    assert resp.json()["counts"] == {"applied": 1}
    cart = db_testsupport.fetch_cart(dsn, tid, "CART-REC")
    assert cart["status"] == "recovered"
    job = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "cart:CART-REC:stage1")
    assert job["status"] == "cancelled" and job["cancel_reason"] == "cart_recovered"


def test_final_cart_is_never_reopened_and_gets_no_new_job(carts_client, carts_tenant):
    dsn, platform_ref, tid = carts_tenant
    _seed_customer_with_cart_channel(dsn, tid)
    _post(carts_client, {"tenant_ref": platform_ref,
                         "events": [_updated("CART-FIN", wa_id="967733000001")]})
    _post(carts_client, {"tenant_ref": platform_ref, "events": [{
        "type": "cart.cleared", "cart_id": "CART-FIN", "occurred_at": NOW.isoformat(),
    }]})
    resp = _post(carts_client, {"tenant_ref": platform_ref,
                                "events": [_updated("CART-FIN", wa_id="967733000001")]})
    assert resp.json()["counts"] == {"already_final": 1}
    cart = db_testsupport.fetch_cart(dsn, tid, "CART-FIN")
    assert cart["status"] == "cleared", "a final cart must never reopen"
    job = db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "cart:CART-FIN:stage1")
    assert job["status"] == "cancelled", "no new job for a final cart"


def test_reminded_cart_gets_no_new_job(carts_client, carts_tenant):
    dsn, platform_ref, tid = carts_tenant
    chid, cid = _seed_customer_with_cart_channel(dsn, tid)
    db_testsupport.insert_cart(
        dsn, tenant_id=tid, customer_id=cid, platform_cart_id="CART-RMD", status="reminded",
    )
    resp = _post(carts_client, {"tenant_ref": platform_ref,
                                "events": [_updated("CART-RMD", wa_id="967733000001")]})
    assert resp.json()["counts"] == {"already_reminded": 1}
    assert db_testsupport.fetch_scheduled_job_by_key(dsn, tid, "cart:CART-RMD:stage1") is None



