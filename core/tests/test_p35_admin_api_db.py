"""core/tests/test_p35_admin_api_db.py - P3.5: the marketing dashboard API over REAL
HTTP (TestClient on the assembled app) against the real database, running as the
real runtime roles (sharwa_app under RLS, sharwa_system for the tenant list).

Authentication is the ONLY thing overridden (the RS256 path has its own tests):
`authenticate` returns a chosen principal, so every authorization decision below
(require_role, tenant scoping, RLS) is the production code path.

Proves: role gating (platform_admin only for admin routes), the confirm phrase and
preflight gates on enable (H100), same-transaction audit + append-only log, disable
cancelling the queue under the APP role (not the migration role), RLS isolation of
the tenant routes, metric correctness, and that no phone ever leaves the API (H20).
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.api.deps import AuthContext, authenticate
from app.db import testsupport as ts
from app.marketing_ops import MarketingEnv
from app.security.jwt import Principal

pytestmark = pytest.mark.db

ENV_OK = MarketingEnv(
    template_registered=True, footer_explicit=True, min_warmup_days=3,
    footer_text="لإيقاف الرسائل الترويجية أرسل: إيقاف", sample_text="نص تجريبي\nتذييل",
)


@pytest.fixture()
def api(monkeypatch):
    import app.main as main_module
    monkeypatch.setattr(main_module.core_db, "init_pool", lambda *a, **kw: None)
    monkeypatch.setattr(main_module.core_db, "close_pool", lambda: None)
    app = main_module.create_app()
    with TestClient(app) as client:
        app.state.marketing_env = ENV_OK

        def login(role: str, tenant_id: uuid.UUID, ref: str, sub: str = "u-1") -> None:
            app.dependency_overrides[authenticate] = lambda: AuthContext(
                principal=Principal(sub=sub, platform_ref=ref, role=role), tenant_id=tenant_id,
            )

        client.login = login  # type: ignore[attr-defined]
        client.app_ = app  # type: ignore[attr-defined]
        yield client
        app.dependency_overrides.clear()


def _make_tenant(dsn, label, *, with_channel=True, subscribers=1):
    ref = f"p35-{label}-{uuid.uuid4()}"
    tid = ts.insert_tenant_returning_id(dsn, platform_ref=ref, name=f"Tenant {label}")
    chid = None
    wa_ids = []
    if with_channel:
        chid = ts.insert_channel_account(
            dsn, tenant_id=tid, type_="whatsapp_baileys",
            session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
        )
        ts.seed_number_health(
            dsn, tenant_id=tid, channel_id=chid,
            warmup_started_at=(datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
        )
    for _ in range(subscribers):
        wa = f"9677{uuid.uuid4().int % 10**10:010d}"
        cid = ts.insert_customer(dsn, tenant_id=tid, wa_id=wa)
        ts.insert_consent(dsn, tenant_id=tid, customer_id=cid, scope="marketing")
        wa_ids.append((wa, cid))
    return SimpleNamespace(ref=ref, tid=tid, chid=chid, wa=wa_ids)


@pytest.fixture()
def world():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    admin = _make_tenant(dsn, "admin", with_channel=False, subscribers=0)
    a = _make_tenant(dsn, "a", subscribers=2)
    b = _make_tenant(dsn, "b", subscribers=3)
    yield SimpleNamespace(dsn=dsn, admin=admin, a=a, b=b)
    for t in (a, b, admin):
        ts.delete_tenant_full(dsn, t.tid)


def _conv(w, t):
    return ts.seed_conversation(w.dsn, tenant_id=t.tid, channel_id=t.chid, customer_id=t.wa[0][1],
                                bot_status="active", epoch=0)


def _enabled_by(w):
    return _row(w.dsn, "SELECT enabled_by FROM marketing_activation WHERE tenant_id = %s", [w.a.tid])[0][0]


def _enable_body(ref, cap=5, **kw):
    return {"cap": cap, "reason": "canary", "confirm": f"ENABLE-MARKETING {ref}", **kw}


def _as_admin(api, w):
    api.login("platform_admin", w.admin.tid, w.admin.ref, sub="owner-1")


def _row(dsn, sql, params):
    with psycopg.connect(dsn) as conn:
        return conn.execute(sql, params).fetchall()


# --- authorization ------------------------------------------------------------


@pytest.mark.parametrize("method,path", [
    ("get", "/v1/admin/marketing/tenants"),
    ("get", "/v1/admin/marketing/tenants/x"),
    ("get", "/v1/admin/marketing/metrics"),
    ("post", "/v1/admin/marketing/tenants/x/enable"),
    ("post", "/v1/admin/marketing/tenants/x/disable"),
    ("post", "/v1/admin/marketing/tenants/x/set-cap"),
])
def test_admin_routes_are_platform_admin_only(api, world, method, path):
    for role in ("merchant_admin", "staff"):
        api.login(role, world.a.tid, world.a.ref)
        r = getattr(api, method)(path, json={"cap": 5, "reason": "x", "confirm": "x"}) if method == "post" \
            else getattr(api, method)(path)
        assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN_ROLE"


def test_unauthenticated_is_401(api):
    r = api.get("/v1/admin/marketing/tenants")
    assert r.status_code == 401


def test_staff_cannot_read_tenant_marketing_overview(api, world):
    api.login("staff", world.a.tid, world.a.ref)
    assert api.get("/v1/marketing/overview").status_code == 403


# --- superadmin reads ----------------------------------------------------------


def test_list_shows_every_tenant_default_off_without_phones(api, world):
    _as_admin(api, world)
    r = api.get("/v1/admin/marketing/tenants")
    assert r.status_code == 200
    rows = {t["ref"]: t for t in r.json()["tenants"]}
    for t, subs in ((world.a, 2), (world.b, 3)):
        row = rows[t.ref]
        assert row["activation"]["enabled"] is False
        assert row["activation"]["configured"] is False
        assert row["eligible_subscribers"] == subs
        assert row["channels"] == {"total": 1, "connected": 1}
    for wa, _ in world.a.wa + world.b.wa:
        assert wa not in r.text  # H20: no phone leaves the API
    assert r.json()["global_marketing_switch"] in ("on", "degraded", "off")


def test_detail_reports_preflight_and_confirm_phrase(api, world):
    _as_admin(api, world)
    d = api.get(f"/v1/admin/marketing/tenants/{world.a.ref}").json()
    assert d["preflight_failed"] == []
    assert d["confirm_phrase"] == f"ENABLE-MARKETING {world.a.ref}"
    assert d["sample_text"] == ENV_OK.sample_text
    # a tenant with no channel: the reason the enable button would be refused
    d2 = api.get(f"/v1/admin/marketing/tenants/{world.admin.ref}").json()
    assert "no_connected_channel" in d2["preflight_failed"]
    assert "no_eligible_subscribers" in d2["preflight_failed"]


def test_unknown_or_malformed_ref(api, world):
    _as_admin(api, world)
    assert api.get("/v1/admin/marketing/tenants/nope-" + str(uuid.uuid4())).status_code == 404
    assert api.get("/v1/admin/marketing/tenants/bad ref!").status_code in (404, 422)


# --- enable: the H100 gates ----------------------------------------------------


def test_enable_wrong_confirm_writes_nothing(api, world):
    _as_admin(api, world)
    r = api.post(f"/v1/admin/marketing/tenants/{world.a.ref}/enable",
                 json=_enable_body(world.a.ref, confirm="yes"))
    assert r.status_code == 422 and r.json()["error"]["details"] == {"field": "confirm"}
    assert ts.fetch_marketing_activation(world.dsn, world.a.tid) is None
    assert ts.count_marketing_log(world.dsn, world.a.tid) == 0


def test_enable_confirm_phrase_for_another_tenant_is_refused(api, world):
    _as_admin(api, world)
    r = api.post(f"/v1/admin/marketing/tenants/{world.a.ref}/enable",
                 json=_enable_body(world.b.ref))
    assert r.status_code == 422
    assert ts.fetch_marketing_activation(world.dsn, world.a.tid) is None


def test_enable_refused_by_preflight_is_audited_not_applied(api, world):
    _as_admin(api, world)
    api.app_.state.marketing_env = MarketingEnv(
        template_registered=True, footer_explicit=False, min_warmup_days=3,
        footer_text="x", sample_text="",
    )
    r = api.post(f"/v1/admin/marketing/tenants/{world.a.ref}/enable", json=_enable_body(world.a.ref))
    assert r.status_code == 412
    assert r.json()["error"]["code"] == "PRECONDITION_FAILED"
    assert r.json()["error"]["details"]["failed"] == ["footer_not_explicit"]
    assert ts.fetch_marketing_activation(world.dsn, world.a.tid) is None
    audit = _row(world.dsn, "SELECT action FROM audit_log WHERE tenant_id = %s", [world.a.tid])
    assert [a[0] for a in audit] == ["marketing.enable_refused"]


def test_enable_success_records_actor_log_and_audit(api, world):
    _as_admin(api, world)
    r = api.post(f"/v1/admin/marketing/tenants/{world.a.ref}/enable", json=_enable_body(world.a.ref, cap=7))
    assert r.status_code == 200, r.text
    assert r.json() == {"ref": world.a.ref, "enabled": True, "canary_cap_per_day": 7}
    act = ts.fetch_marketing_activation(world.dsn, world.a.tid)
    assert act["enabled"] is True and act["canary_cap_per_day"] == 7
    assert _enabled_by(world) == "dashboard:owner-1"  # from the token, never from the body
    assert ts.count_marketing_log(world.dsn, world.a.tid) == 1
    audit = _row(world.dsn, "SELECT action, actor_sub, actor_role FROM audit_log WHERE tenant_id = %s",
                 [world.a.tid])
    assert audit == [("marketing.enable", "owner-1", "platform_admin")]
    # tenant B is untouched (no cross-tenant write)
    assert ts.fetch_marketing_activation(world.dsn, world.b.tid) is None
    d = api.get(f"/v1/admin/marketing/tenants/{world.a.ref}").json()
    assert d["activation"]["enabled"] is True and d["history"][0]["action"] == "enable"


@pytest.mark.parametrize("cap", [0, -1, 501, "5"])
def test_enable_cap_bounds_are_validated(api, world, cap):
    _as_admin(api, world)
    r = api.post(f"/v1/admin/marketing/tenants/{world.a.ref}/enable",
                 json=_enable_body(world.a.ref, cap=cap))
    assert r.status_code == 422
    assert ts.fetch_marketing_activation(world.dsn, world.a.tid) is None


def test_enable_requires_reason_and_rejects_actor_in_body(api, world):
    _as_admin(api, world)
    body = _enable_body(world.a.ref)
    body["reason"] = ""
    assert api.post(f"/v1/admin/marketing/tenants/{world.a.ref}/enable", json=body).status_code == 422
    body = _enable_body(world.a.ref, actor="someone-else")
    assert api.post(f"/v1/admin/marketing/tenants/{world.a.ref}/enable", json=body).status_code == 200
    assert _enabled_by(world) == "dashboard:owner-1"


def test_enable_suspended_tenant_refused(api, world):
    _as_admin(api, world)
    with psycopg.connect(world.dsn, autocommit=True) as conn:
        conn.execute("UPDATE tenants SET status = 'suspended' WHERE id = %s", [world.a.tid])
    r = api.post(f"/v1/admin/marketing/tenants/{world.a.ref}/enable", json=_enable_body(world.a.ref))
    assert r.status_code == 403 and r.json()["error"]["code"] == "TENANT_SUSPENDED"


# --- disable (H101 level 2) under the APP role ----------------------------------


def test_disable_cancels_queue_but_not_utility(api, world):
    _as_admin(api, world)
    assert api.post(f"/v1/admin/marketing/tenants/{world.a.ref}/enable",
                    json=_enable_body(world.a.ref)).status_code == 200
    conv = _conv(world, world.a)
    mk = ts.seed_outbox_row(world.dsn, tenant_id=world.a.tid, channel_id=world.a.chid,
                            conversation_id=conv, origin="automation", message_class="marketing",
                            to_wa_id=world.a.wa[0][0])
    ut = ts.seed_outbox_row(world.dsn, tenant_id=world.a.tid, channel_id=world.a.chid,
                            conversation_id=conv, origin="automation", message_class="utility",
                            to_wa_id=world.a.wa[0][0])
    r = api.post(f"/v1/admin/marketing/tenants/{world.a.ref}/disable", json={"reason": "stop"})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False and r.json()["outbox_dropped"] == 1
    assert ts.fetch_outbox_status(world.dsn, mk) == "dropped_policy"
    assert ts.fetch_outbox_status(world.dsn, ut) == "pending"
    assert ts.fetch_marketing_activation(world.dsn, world.a.tid)["enabled"] is False
    assert ts.count_marketing_log(world.dsn, world.a.tid) == 2
    actions = [a[0] for a in _row(world.dsn, "SELECT action FROM audit_log WHERE tenant_id = %s ORDER BY id",
                                  [world.a.tid])]
    assert actions == ["marketing.enable", "marketing.disable"]


def test_disable_requires_reason(api, world):
    _as_admin(api, world)
    assert api.post(f"/v1/admin/marketing/tenants/{world.a.ref}/disable", json={}).status_code == 422


# --- set-cap -------------------------------------------------------------------


def test_set_cap_never_enables_and_validates(api, world):
    _as_admin(api, world)
    for bad in (0, 501):
        assert api.post(f"/v1/admin/marketing/tenants/{world.a.ref}/set-cap",
                        json={"cap": bad, "reason": "x"}).status_code == 422
    r = api.post(f"/v1/admin/marketing/tenants/{world.a.ref}/set-cap", json={"cap": 12, "reason": "raise"})
    assert r.status_code == 200 and r.json()["canary_cap_per_day"] == 12
    act = ts.fetch_marketing_activation(world.dsn, world.a.tid)
    assert act["enabled"] is False and act["canary_cap_per_day"] == 12  # H100: a cap change enables no one


# --- tenant routes: RLS isolation + metrics -------------------------------------


def _seed_activity(w):
    """Tenant A: 2 sent marketing today, 1 sent utility, 1 recovered + 1 expired + 1 open cart,
    2 distinct opt-outs (one repeated STOP)."""
    dsn = w.dsn
    t = w.a
    conv = _conv(w, t)
    for _ in range(2):
        oid = ts.seed_outbox_row(dsn, tenant_id=t.tid, channel_id=t.chid, conversation_id=conv,
                                 origin="automation", message_class="marketing", to_wa_id=t.wa[0][0],
                                 status="sent")
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("UPDATE outbox SET sent_at = clock_timestamp() WHERE id = %s", [oid])
    oid = ts.seed_outbox_row(dsn, tenant_id=t.tid, channel_id=t.chid, conversation_id=conv,
                             origin="automation", message_class="utility", to_wa_id=t.wa[0][0],
                             status="sent")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("UPDATE outbox SET sent_at = clock_timestamp() WHERE id = %s", [oid])
    ts.insert_cart(dsn, tenant_id=t.tid, customer_id=t.wa[0][1], platform_cart_id="c1", status="recovered")
    ts.insert_cart(dsn, tenant_id=t.tid, customer_id=t.wa[0][1], platform_cart_id="c2", status="expired")
    ts.insert_cart(dsn, tenant_id=t.tid, customer_id=t.wa[1][1], platform_cart_id="c3", status="open")
    for cid in (t.wa[0][1], t.wa[0][1], t.wa[1][1]):
        ts.insert_consent(dsn, tenant_id=t.tid, customer_id=cid, scope="marketing", granted=False,
                          source="customer_message_optout")
    # tenant B: loud, different numbers - must NEVER show in A's view
    for i in range(5):
        ts.insert_cart(dsn, tenant_id=w.b.tid, customer_id=w.b.wa[0][1], platform_cart_id=f"b{i}",
                       status="recovered")


def test_tenant_overview_and_metrics_are_rls_scoped(api, world):
    _seed_activity(world)
    api.login("merchant_admin", world.a.tid, world.a.ref)
    ov = api.get("/v1/marketing/overview").json()
    assert ov["open_carts"] == 1 and ov["channels"]["connected"] == 1
    m = api.get("/v1/marketing/metrics?days=7").json()
    assert m["totals"]["sent_marketing"] == 2 and m["totals"]["sent_utility"] == 1
    assert m["totals"]["optouts"] == 2  # distinct customers per day: the repeated STOP counts once
    assert m["carts"]["recovered"] == 1 and m["carts"]["expired"] == 1 and m["carts"]["open"] == 1
    assert m["carts"]["recovery_rate_pct"] == 50.0
    assert len(m["series"]) == 7 and m["series"][-1]["marketing"] == 2
    # tenant B's five recovered carts are invisible to A (RLS), and vice-versa
    api.login("merchant_admin", world.b.tid, world.b.ref)
    mb = api.get("/v1/marketing/metrics?days=7").json()
    assert mb["carts"]["recovered"] == 5 and mb["totals"]["sent_marketing"] == 0


def test_tenant_routes_cannot_mutate(api, world):
    api.login("merchant_admin", world.a.tid, world.a.ref)
    for path in ("/v1/marketing/overview", "/v1/marketing/metrics"):
        assert api.post(path, json={}).status_code == 405


def test_metrics_days_validated(api, world):
    api.login("merchant_admin", world.a.tid, world.a.ref)
    assert api.get("/v1/marketing/metrics?days=0").status_code == 422
    assert api.get("/v1/marketing/metrics?days=91").status_code == 422


def test_admin_metrics_aggregates_and_lists_tenants(api, world):
    _seed_activity(world)
    _as_admin(api, world)
    m = api.get("/v1/admin/marketing/metrics?days=7").json()
    per = {t["ref"]: t for t in m["tenants"]}
    assert per[world.a.ref]["carts"]["recovered"] == 1
    assert per[world.b.ref]["carts"]["recovered"] == 5
    assert m["carts"]["recovered"] >= 6  # at least these two (other tenants may exist)
    for wa, _ in world.a.wa + world.b.wa:
        assert wa not in json.dumps(m)


# --- the static console ---------------------------------------------------------


def test_console_is_not_served_unless_configured(api):
    assert api.get("/console/").status_code == 404


def test_console_served_with_strict_headers(monkeypatch, tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>x</title>", encoding="utf-8")
    monkeypatch.setenv("CONSOLE_STATIC_DIR", str(tmp_path))
    import app.main as main_module
    monkeypatch.setattr(main_module.core_db, "init_pool", lambda *a, **kw: None)
    monkeypatch.setattr(main_module.core_db, "close_pool", lambda: None)
    with TestClient(main_module.create_app()) as client:
        r = client.get("/console/")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp and "frame-ancestors 'none'" in csp
    assert r.headers["cache-control"] == "no-store" and r.headers["x-frame-options"] == "DENY"
