"""P4 Task 14: gift baskets in the REAL conversation turn (process_turn) and the
platform's signed gift-cart lookup, on PostgreSQL.

Flag off => the turn is unchanged. Flag on => a gift request with a budget gets
up to 3 baskets of the merchant's own titles, each with a checkout link
https://sharwaah.com/checkout/gift/<cart_id> (owner decision for OQ-P4-06);
the basket is stored and the platform resolves the id through
POST /webhooks/platform/gift-cart. No price or total ever reaches the customer.
"""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import os
import re
import time
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import db as core_db
from app.db import testsupport as db_testsupport
from app.workers import turn, verify
from app.workers.config import WorkerSettings

pytestmark = pytest.mark.db

TENANT_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = uuid.UUID("22222222-2222-2222-2222-222222222222")
BASE = "https://sharwaah.com/checkout/gift/"

# platform_product_id -> (title, category, [(variant_id, price_minor, currency, stock_hint)])
_CATALOG = {
    "P-PERF": ("عطر عود فاخر", "beauty", [("V-PERF", 1_200_000, "YER", 5)]),
    "P-WATCH": ("ساعة يد رجالية فضية", "accessories", [("V-WATCH", 900_000, "YER", None)]),
    "P-SCARF": ("شال صوف نسائي", "women", [("V-SCARF", 500_000, "YER", 3)]),
    "P-MUG": ("كوب سيراميك مزخرف", "home", [("V-MUG", 300_000, "YER", 10)]),
    "P-OUT": ("حقيبة جلد فاخرة", "bags", [("V-OUT", 1_500_000, "YER", 0)]),   # out of stock
}
_PRICE_TEXTS = ("12000", "9000", "5000", "3000", "1200000", "900000", "500000", "300000")


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
    return dataclasses.replace(WorkerSettings(**values), gift_enabled=enabled)


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
    with psycopg.connect(dsn, autocommit=True) as conn:
        for ppid, (title, cat, variants) in _CATALOG.items():
            pid = conn.execute(
                "INSERT INTO catalog_products "
                "(tenant_id, platform_product_id, title, category, source_version) "
                "VALUES (%s, %s, %s, %s, 1) RETURNING id", (TENANT_A, ppid, title, cat),
            ).fetchone()[0]
            for vid, price, cur, stock in variants:
                conn.execute(
                    "INSERT INTO catalog_variants (tenant_id, product_id, platform_variant_id, "
                    "price_hint_minor, currency, stock_hint, source_version) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 1)",
                    (TENANT_A, pid, vid, price, cur, stock),
                )
    yield dsn, conv
    db_testsupport.delete_tenant_full(dsn, TENANT_A)
    db_testsupport.delete_tenant_full(dsn, TENANT_B)


def _turn(dsn: str, conv: uuid.UUID, body: str, *, enabled: bool) -> tuple[str, dict]:
    db_testsupport.seed_inbound_message(dsn, tenant_id=TENANT_A, conversation_id=conv, body=body)
    settings = _settings(enabled=enabled)
    outcome = turn.process_turn(
        settings=settings, conversation_id=conv, tenant_id=TENANT_A,
        rules=verify.build_rules(settings), router=None,
    )
    with core_db.tenant_tx(TENANT_A) as conn:
        locked = turn.repos_outbox.lock_conversation(conn, conversation_id=conv, tenant_id=TENANT_A)
    assert locked is not None
    row = db_testsupport.fetch_outbox_row_by_idempotency(dsn, TENANT_A, f"{conv}:{locked.last_inbound_seq}:1")
    assert row is not None
    return outcome, row["payload"]


def _carts(dsn: str) -> dict[str, list[str]]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute("SELECT id, items FROM gift_carts WHERE tenant_id = %s", (TENANT_A,)).fetchall()
    return {str(r[0]): sorted(i["platform_variant_id"] for i in r[1]) for r in rows}


def test_flag_off_keeps_todays_generic_handoff(ctx):
    dsn, conv = ctx
    outcome, payload = _turn(dsn, conv, "ابي هدية بحدود 20 الف", enabled=False)
    assert (outcome, payload["template"]) == ("handoff", "handoff_notice")
    assert _carts(dsn) == {}


def test_gift_with_budget_answers_with_basket_links(ctx):
    dsn, conv = ctx
    outcome, payload = _turn(dsn, conv, "ابي هدية لأمي بحدود 20 الف ريال", enabled=True)
    assert (outcome, payload["template"]) == ("gift_baskets", "gift_baskets")
    text = payload["text"]
    ids = re.findall(re.escape(BASE) + r"([0-9a-f-]{36})", text)
    carts = _carts(dsn)
    assert 1 <= len(ids) <= 3 and set(ids) == set(carts)          # every link is a stored basket
    assert all("V-OUT" not in v for v in carts.values())           # out of stock never offered
    assert not any(p in text for p in _PRICE_TEXTS)                # no price, no total (§5.2)
    assert "عطر عود فاخر" in text or "ساعة يد رجالية فضية" in text


def test_gift_without_budget_asks_for_it(ctx):
    dsn, conv = ctx
    outcome, payload = _turn(dsn, conv, "ابي هدية لزوجتي", enabled=True)
    assert (outcome, payload["template"]) == ("gift_need_budget", "gift_need_budget")
    assert _carts(dsn) == {}


def test_budget_below_every_item_hands_off(ctx):
    dsn, conv = ctx
    outcome, payload = _turn(dsn, conv, "هدية بـ 1000 ريال", enabled=True)
    assert (outcome, payload["template"]) == ("gift_no_basket", "gift_no_basket")


def test_other_currency_hands_off(ctx):
    dsn, conv = ctx
    outcome, payload = _turn(dsn, conv, "gift for my mother under 100 $", enabled=True)
    assert (outcome, payload["template"]) == ("gift_currency", "gift_currency")
    assert _carts(dsn) == {}


# ---- the platform's signed lookup -------------------------------------------------

@pytest.fixture()
def api(monkeypatch):
    import app.main as main_module
    monkeypatch.setattr(main_module.core_db, "init_pool", lambda *a, **kw: None)
    monkeypatch.setattr(main_module.core_db, "close_pool", lambda: None)
    with TestClient(main_module.create_app()) as c:
        yield c


def _post(c, payload: dict, *, secret: str | None = None, ts: int | None = None):
    body = json.dumps(payload).encode()
    stamp = str(ts if ts is not None else int(time.time()))
    sig = hmac.new((secret or os.environ["PLATFORM_WEBHOOK_SECRET"]).encode(),
                   f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return c.post("/webhooks/platform/gift-cart", content=body,
                  headers={"X-Platform-Signature": sig, "X-Platform-Timestamp": stamp})


def _ref(dsn: str, tenant: uuid.UUID) -> str:
    with psycopg.connect(dsn) as conn:
        return str(conn.execute("SELECT platform_ref FROM tenants WHERE id = %s", (tenant,)).fetchone()[0])


def test_platform_resolves_a_basket_link(ctx, api):
    dsn, conv = ctx
    _turn(dsn, conv, "ابي هدية بحدود 20 الف", enabled=True)
    cart_id, variants = next(iter(_carts(dsn).items()))
    r = _post(api, {"tenant_ref": _ref(dsn, TENANT_A), "cart_id": cart_id})
    assert r.status_code == 200
    cart = r.json()["cart"]
    assert cart["cart_id"] == cart_id and cart["currency"] == "YER"
    assert sorted(i["platform_variant_id"] for i in cart["items"]) == variants
    assert all(set(i) == {"platform_product_id", "platform_variant_id", "qty"} for i in cart["items"])
    assert "price" not in r.text and "total" not in r.text       # identifiers only


def test_platform_lookup_refusals(ctx, api):
    dsn, conv = ctx
    _turn(dsn, conv, "ابي هدية بحدود 20 الف", enabled=True)
    cart_id = next(iter(_carts(dsn)))
    ref_a, ref_b = _ref(dsn, TENANT_A), _ref(dsn, TENANT_B)
    assert _post(api, {"tenant_ref": ref_a, "cart_id": cart_id}, secret="wrong").status_code == 401
    stale = int(time.time()) - 86_400
    assert _post(api, {"tenant_ref": ref_a, "cart_id": cart_id}, ts=stale).status_code == 401
    assert _post(api, {"tenant_ref": ref_b, "cart_id": cart_id}).status_code == 404   # another store's link
    assert _post(api, {"tenant_ref": ref_a, "cart_id": str(uuid.uuid4())}).status_code == 404
    assert _post(api, {"tenant_ref": ref_a, "cart_id": "not-a-uuid"}).status_code == 422
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE gift_carts SET expires_at = now() - interval '1 minute' WHERE id = %s", (cart_id,),
        )
    assert _post(api, {"tenant_ref": ref_a, "cart_id": cart_id}).status_code == 404       # expired link
