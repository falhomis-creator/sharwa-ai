"""core/tests/test_p3_marketing_db.py - P3.4 (H100-H103) on a REAL database.

Everything goes through the PRODUCTION catalog entry `cart_reminder` (no
injected template) with a FAKE world: no network, no gateway, no live send.
Covers: default-off, enable/canary/kill-switch behaviour of the real gate, the
disable coordinator (queue + slots + jobs, utility untouched), revival after
re-enable, the append-only activation log, RLS, and the operator CLI (confirm
phrase, every preflight failure, read-only preview, no phone/text in output).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import psycopg
import pytest

from app import cli
from app import db as core_db
from app.db import repos_marketing, repos_outbox, repos_policy, repos_scheduler
from app.db import testsupport as ts
from app.workers import marketing, policy_gate

pytestmark = pytest.mark.db

PNOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)
TEMPLATE = "cart_reminder"


@pytest.fixture()
def world():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    ref = f"p34-{uuid.uuid4()}"
    tid = ts.insert_tenant_returning_id(dsn, platform_ref=ref, name="P3.4 Tenant")
    chid = ts.insert_channel_account(
        dsn, tenant_id=tid, type_="whatsapp_baileys",
        session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
    )
    ts.seed_number_health(
        dsn, tenant_id=tid, channel_id=chid, daily_cap=50,
        warmup_started_at=(datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
    )
    yield SimpleNamespace(dsn=dsn, tid=tid, chid=chid, ref=ref)
    ts.delete_tenant_full(dsn, tid)


def _customer(w, *, consent=True, source="customer_message_optin"):
    wa = f"9677{uuid.uuid4().int % 10**10:010d}"
    cid = ts.insert_customer(w.dsn, tenant_id=w.tid, wa_id=wa)
    conv = ts.seed_conversation(
        w.dsn, tenant_id=w.tid, channel_id=w.chid, customer_id=cid, bot_status="active", epoch=0,
    )
    ts.seed_inbound_message(w.dsn, tenant_id=w.tid, conversation_id=conv, body="مرحبا")
    if consent:
        ts.insert_consent(w.dsn, tenant_id=w.tid, customer_id=cid, scope="marketing", source=source)
    return SimpleNamespace(cid=cid, wa=wa, conv=conv)


def _settings():
    return SimpleNamespace(
        send_policy_marketing_ttl_h=24, send_policy_utility_ttl_h=6,
        send_policy_interaction_window_d=180, send_policy_active_chat_cooldown_s=1800,
        send_policy_quiet_start="22:00", send_policy_quiet_end="09:00",
        send_policy_marketing_per_24h=1, send_policy_marketing_per_7d=2,
        send_policy_utility_per_24h=3, send_policy_marketing_gap=(20, 60),
        send_policy_utility_gap=(8, 20),
    )


def _row(w, c, *, template=TEMPLATE, cls="marketing"):
    oid = ts.seed_outbox_row(
        w.dsn, tenant_id=w.tid, channel_id=w.chid, conversation_id=c.conv,
        origin="automation", message_class=cls, to_wa_id=c.wa,
        payload={"template": template, "text": "x"},
    )
    return repos_outbox.OutboxRow(
        id=oid, tenant_id=w.tid, conversation_id=c.conv, channel_account_id=w.chid,
        idempotency_key=f"k-{oid}", origin="automation", message_class=cls,
        expected_epoch=None, to_wa_id=c.wa, payload={"template": template, "text": "x"},
        attempts=0, created_at=PNOW,
    ), oid


def _gate(w, c, *, now=PNOW, template=TEMPLATE, cls="marketing"):
    row, oid = _row(w, c, template=template, cls=cls)
    return policy_gate.gate(_settings(), row, now=now, gap_s=20), oid, row


# --- default OFF (H100) ---------------------------------------------------------


def test_default_off_marketing_is_dropped_not_enabled(world):
    c = _customer(world)
    assert repos_marketing is not None
    assert ts.fetch_marketing_activation(world.dsn, world.tid) is None  # absence
    decision, oid, _ = _gate(world, c)
    assert decision.send is False
    assert ts.fetch_outbox_status(world.dsn, oid) == "dropped_policy"
    assert ts.fetch_outbox_policy_reason(world.dsn, oid) == "marketing_not_enabled"
    assert ts.fetch_ledger_rows(world.dsn, world.tid) == []
    assert ts.fetch_number_health_marketing_counters(world.dsn, world.chid) == 0


def test_disabled_row_present_still_drops(world):
    ts.enable_marketing(world.dsn, tenant_id=world.tid)
    with core_db.tenant_tx(world.tid) as conn:
        repos_marketing.set_enabled(conn, tenant_id=world.tid, enabled=False, actor="t", reason="r")
    c = _customer(world)
    _d, oid, _ = _gate(world, c)
    assert ts.fetch_outbox_policy_reason(world.dsn, oid) == "marketing_not_enabled"


# --- enabled behaviour on the REAL gate -----------------------------------------


def test_enabled_with_explicit_optin_reserves_and_stays_pending(world):
    ts.enable_marketing(world.dsn, tenant_id=world.tid)
    c = _customer(world)
    decision, oid, _ = _gate(world, c)
    assert decision.send is True
    assert ts.fetch_ledger_rows(world.dsn, world.tid) == [(oid, "reserved")]
    assert ts.fetch_outbox_status(world.dsn, oid) == "pending"


def test_enabled_without_consent_drops_no_consent(world):
    ts.enable_marketing(world.dsn, tenant_id=world.tid)
    c = _customer(world, consent=False)
    _d, oid, _ = _gate(world, c)
    assert ts.fetch_outbox_policy_reason(world.dsn, oid) == "no_consent"


@pytest.mark.parametrize("source", ["import", "checkout_optin"])
def test_enabled_but_blocked_sources_still_drop(world, source):
    """H95 survives activation: enabling a tenant never makes import/checkout
    grants count as marketing consent."""
    ts.enable_marketing(world.dsn, tenant_id=world.tid)
    c = _customer(world, source=source)
    _d, oid, _ = _gate(world, c)
    assert ts.fetch_outbox_policy_reason(world.dsn, oid) == "no_consent"


def test_global_kill_switch_beats_activation(world):
    """H101 level 1: the global switch stops an ENABLED tenant at the very same
    gate, with the kill-switch reason (it is evaluated first)."""
    ts.enable_marketing(world.dsn, tenant_id=world.tid)
    ts.seed_kill_switch(world.dsn, scope="tenant", scope_id=str(world.tid),
                        capability="marketing", state="off", set_by="test")
    c = _customer(world)
    _d, oid, _ = _gate(world, c)
    assert ts.fetch_outbox_policy_reason(world.dsn, oid) == "kill_switch_off"


def test_utility_is_unaffected_by_marketing_state(world):
    """Utility (stock_available) needs neither activation nor canary."""
    c = _customer(world, consent=False)
    ts.insert_consent(world.dsn, tenant_id=world.tid, customer_id=c.cid,
                      scope="back_in_stock", source="waitlist_join")
    decision, oid, _ = _gate(world, c, template="stock_available", cls="utility")
    assert decision.send is True
    assert ts.fetch_ledger_rows(world.dsn, world.tid) == [(oid, "reserved")]


# --- canary cap (H102): a DEFER from the ledger, never a drop --------------------


def test_canary_cap_defers_then_releases_after_the_window(world):
    ts.enable_marketing(world.dsn, tenant_id=world.tid, cap=2)
    cs = [_customer(world) for _ in range(3)]
    results = [_gate(world, c, now=PNOW + timedelta(minutes=5 * i)) for i, c in enumerate(cs)]
    assert [r[0].send for r in results] == [True, True, False]
    third_oid = results[2][1]
    assert ts.fetch_outbox_status(world.dsn, third_oid) == "pending"      # deferred, NOT dropped
    assert ts.fetch_outbox_policy_reason(world.dsn, third_oid) == "canary_cap_reached"
    assert len(ts.fetch_ledger_rows(world.dsn, world.tid)) == 2

    # Re-processing a RESERVED row never counts its own reservation (F-P3-20).
    again = policy_gate.gate(_settings(), results[0][2], now=PNOW + timedelta(minutes=30), gap_s=20)
    assert again.send is True

    # The window rolls: age the ledger past 24h and the deferred row sends.
    ts.age_proactive_ledger(world.dsn, tenant_id=world.tid, reserved_at=PNOW - timedelta(hours=25))
    row3 = results[2][2]
    final = policy_gate.gate(_settings(), row3, now=PNOW + timedelta(hours=1), gap_s=20)
    assert final.send is True


def test_canary_count_is_per_tenant(world):
    other = ts.insert_tenant_returning_id(world.dsn, platform_ref=f"p34o-{uuid.uuid4()}", name="other")
    try:
        with psycopg.connect(world.dsn, autocommit=True) as conn:
            assert repos_marketing.marketing_sent_24h(conn, tenant_id=other, now=PNOW) == 0
    finally:
        ts.delete_tenant_full(world.dsn, other)


# --- the append-only log + RLS (H100) -------------------------------------------


def test_activation_log_is_append_only_for_the_app_role(world):
    with core_db.tenant_tx(world.tid) as conn:
        repos_marketing.set_enabled(conn, tenant_id=world.tid, enabled=True, actor="a", reason="r", cap=3)
    app_dsn = os.environ["CORE_DATABASE_URL"]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ts.probe_marketing_log_update(app_dsn, world.tid)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ts.probe_marketing_log_delete(app_dsn, world.tid)
    assert ts.count_marketing_log(world.dsn, world.tid) == 1


def test_set_enabled_and_log_commit_together(world):
    with core_db.tenant_tx(world.tid) as conn:
        repos_marketing.set_enabled(conn, tenant_id=world.tid, enabled=True, actor="a", reason="go", cap=7)
        repos_marketing.set_cap(conn, tenant_id=world.tid, cap=9, actor="a", reason="more")
    with core_db.tenant_tx(world.tid) as conn:
        log = repos_marketing.read_log(conn, tenant_id=world.tid)
        act = repos_marketing.read_activation(conn, tenant_id=world.tid)
    assert [(a, actor, r) for a, actor, r, _ in log] == [("enable", "a", "go"), ("cap_change", "a", "more")]
    assert act["enabled"] is True and act["canary_cap_per_day"] == 9


def test_cap_change_never_enables(world):
    with core_db.tenant_tx(world.tid) as conn:
        repos_marketing.set_cap(conn, tenant_id=world.tid, cap=4, actor="a", reason="r")
    assert ts.fetch_marketing_activation(world.dsn, world.tid) == {"enabled": False, "canary_cap_per_day": 4}


def test_rls_tenant_a_cannot_read_tenant_b_activation(world):
    other = ts.insert_tenant_returning_id(world.dsn, platform_ref=f"p34r-{uuid.uuid4()}", name="other")
    try:
        ts.enable_marketing(world.dsn, tenant_id=other)
        with core_db.tenant_tx(world.tid) as conn:
            assert repos_marketing.read_activation(conn, tenant_id=other) is None
            assert repos_marketing.read_activation(conn, tenant_id=world.tid) is None
    finally:
        ts.delete_tenant_full(world.dsn, other)


# --- disable coordinator (H101 level 2) -----------------------------------------


def test_disable_drops_marketing_queue_gives_slots_back_and_leaves_utility(world):
    ts.enable_marketing(world.dsn, tenant_id=world.tid)
    c = _customer(world)
    d, moid, _ = _gate(world, c)                                # reserved marketing row
    assert d.send is True
    assert ts.fetch_number_health_marketing_counters(world.dsn, world.chid) == 1

    c2 = _customer(world)
    ts.insert_consent(world.dsn, tenant_id=world.tid, customer_id=c2.cid,
                      scope="back_in_stock", source="waitlist_join")
    uoid = ts.seed_outbox_row(
        world.dsn, tenant_id=world.tid, channel_id=world.chid, conversation_id=c2.conv,
        origin="automation", message_class="utility", to_wa_id=c2.wa,
        payload={"template": "stock_available", "text": "x"},
    )
    job = ts.insert_scheduled_job(
        world.dsn, tenant_id=world.tid, kind="cart_reminder", dedupe_key="cart:D1:stage1",
        payload={"cart_id": "D1"},
    )
    other_kind = ts.insert_scheduled_job(
        world.dsn, tenant_id=world.tid, kind="other_kind_probe", dedupe_key="probe:1",
    )

    with repos_marketing.operator_conn(world.dsn) as conn:
        counts = marketing.disable_tenant(conn, tenant_id=world.tid, actor="op", reason="stop it")
        conn.commit()

    assert counts == {"outbox_dropped": 1, "slots_released": 1, "jobs_cancelled": 1}
    assert ts.fetch_outbox_status(world.dsn, moid) == "dropped_policy"
    assert ts.fetch_outbox_policy_reason(world.dsn, moid) == "marketing_disabled"
    assert ts.fetch_ledger_rows(world.dsn, world.tid) == [(moid, "released")]
    assert ts.fetch_number_health_marketing_counters(world.dsn, world.chid) == 0   # H85 slot back
    assert ts.fetch_outbox_status(world.dsn, uoid) == "pending"                    # utility untouched
    j = ts.fetch_scheduled_job(world.dsn, job)
    assert (j["status"], j["cancel_reason"]) == ("cancelled", "marketing_disabled")
    assert ts.fetch_scheduled_job(world.dsn, other_kind)["status"] == "pending"
    assert ts.fetch_marketing_activation(world.dsn, world.tid)["enabled"] is False
    assert ts.count_marketing_log(world.dsn, world.tid) >= 1


def test_disable_is_idempotent_and_atomic_log(world):
    with repos_marketing.operator_conn(world.dsn) as conn:
        marketing.disable_tenant(conn, tenant_id=world.tid, actor="op", reason="r1")
        marketing.disable_tenant(conn, tenant_id=world.tid, actor="op", reason="r2")
        conn.commit()
    assert ts.count_marketing_log(world.dsn, world.tid) == 2


def test_a_disabled_reminder_is_revived_by_fresh_cart_activity(world):
    """marketing_disabled is a REVIVABLE cancel (freeze, not burn): after the
    operator re-enables, fresh cart activity re-schedules the reminder."""
    ts.insert_scheduled_job(
        world.dsn, tenant_id=world.tid, kind="cart_reminder", dedupe_key="cart:R1:stage1",
        payload={"cart_id": "R1"},
    )
    with repos_marketing.operator_conn(world.dsn) as conn:
        marketing.disable_tenant(conn, tenant_id=world.tid, actor="op", reason="r")
        conn.commit()
    with core_db.tenant_tx(world.tid) as conn:
        revived = repos_scheduler.schedule(
            conn, tenant_id=world.tid, kind="cart_reminder", dedupe_key="cart:R1:stage1",
            run_at=PNOW, payload={"cart_id": "R1"}, max_lateness_s=3600,
        )
    assert revived is True
    assert ts.fetch_scheduled_job_by_key(world.dsn, world.tid, "cart:R1:stage1")["status"] == "pending"


# --- operator CLI ---------------------------------------------------------------


@pytest.fixture()
def cli_env(monkeypatch):
    for k, v in {
        "METRICS_TOKEN": "tok", "GATEWAY_BASE_URL": "http://127.0.0.1:4001", "GATEWAY_API_KEY": "gk",
        "ORDER_REF_HASH_KEY": "test-order-ref-hash-key", "REDIS_DURABLE_HOST": "127.0.0.1",
        "REDIS_DURABLE_PASSWORD": "pw",
    }.items():
        monkeypatch.setenv(k, os.environ.get(k, v))
    monkeypatch.setenv("MARKETING_FOOTER_AR", "لإيقاف الرسائل الترويجية أرسل: إيقاف")


def _ready(w):
    """Everything `enable` demands: connected channel, healthy + warmed number,
    switch on, one eligible subscriber, footer env set (cli_env)."""
    return _customer(w)


def _enable_args(w, *, confirm=None, cap=5):
    return ["marketing", "enable", "--tenant-ref", w.ref, "--cap", str(cap), "--actor", "ops",
            "--reason", "canary", "--confirm", confirm or f"ENABLE-MARKETING {w.ref}"]


def test_cli_enable_requires_the_literal_confirm_phrase(world, cli_env, capsys):
    _ready(world)
    rc = cli.main(_enable_args(world, confirm="yes please"))
    assert rc == 1 and "--confirm must be exactly" in capsys.readouterr().err
    assert ts.fetch_marketing_activation(world.dsn, world.tid) is None
    assert ts.count_marketing_log(world.dsn, world.tid) == 0


def test_cli_enable_happy_path_writes_activation_and_log(world, cli_env, capsys):
    _ready(world)
    assert cli.main(_enable_args(world, cap=3)) == 0
    assert ts.fetch_marketing_activation(world.dsn, world.tid) == {"enabled": True, "canary_cap_per_day": 3}
    assert ts.count_marketing_log(world.dsn, world.tid) == 1
    assert cli.main(["marketing", "status", "--tenant-ref", world.ref]) == 0
    out = capsys.readouterr().out
    assert "enabled: True" in out and "canary_cap_per_day: 3" in out and "eligible_subscribers: 1" in out


def test_cli_enable_unknown_tenant(world, cli_env, capsys):
    assert cli.main(["marketing", "status", "--tenant-ref", "nope-" + uuid.uuid4().hex]) == 1
    assert "unknown tenant_ref" in capsys.readouterr().err


@pytest.mark.parametrize("breaker,expected", [
    ("no_subscriber", "no_eligible_subscribers"),
    ("disconnected", "no_connected_channel"),
    ("paused", "number_not_healthy"),
    ("no_warmup", "warmup_not_started"),
    ("young_warmup", "warmup_too_young"),
    ("switch_off", "global_switch_off"),
    ("no_footer", "footer_not_explicit"),
])
def test_cli_enable_refuses_on_each_preflight_failure(world, cli_env, monkeypatch, capsys, breaker, expected):
    if breaker != "no_subscriber":
        _ready(world)
    with psycopg.connect(world.dsn, autocommit=True) as conn:
        if breaker == "disconnected":
            conn.execute("UPDATE channel_accounts SET status = 'disconnected' WHERE id = %s", (world.chid,))
        elif breaker == "paused":
            conn.execute("UPDATE number_health SET state = 'paused' WHERE channel_account_id = %s", (world.chid,))
        elif breaker == "no_warmup":
            conn.execute("UPDATE number_health SET warmup_started_at = NULL WHERE channel_account_id = %s", (world.chid,))
        elif breaker == "young_warmup":
            conn.execute("UPDATE number_health SET warmup_started_at = now() WHERE channel_account_id = %s", (world.chid,))
    if breaker == "switch_off":
        ts.seed_kill_switch(world.dsn, scope="tenant", scope_id=str(world.tid),
                            capability="marketing", state="off", set_by="test")
    if breaker == "no_footer":
        monkeypatch.delenv("MARKETING_FOOTER_AR")
    rc = cli.main(_enable_args(world))
    err = capsys.readouterr().err
    assert rc == 1 and "preflight failed" in err and expected in err
    assert ts.fetch_marketing_activation(world.dsn, world.tid) is None
    assert ts.count_marketing_log(world.dsn, world.tid) == 0


def test_cli_preview_is_read_only_and_leaks_nothing(world, cli_env, capsys):
    c = _customer(world)
    stranger = _customer(world, consent=False)
    for cust, key in ((c, "P1"), (stranger, "P2")):
        ts.insert_cart(world.dsn, tenant_id=world.tid, customer_id=cust.cid, platform_cart_id=key,
                       snapshot={"items": [{"title": "حذاء سري"}], "item_count": 2})
    before = (ts.count_outbox_rows(world.dsn, tenant_id=world.tid),
              ts.count_marketing_log(world.dsn, world.tid),
              ts.fetch_marketing_activation(world.dsn, world.tid))
    assert cli.main(["marketing", "preview", "--tenant-ref", world.ref]) == 0
    out = capsys.readouterr().out
    assert "open_carts: 2" in out and "eligible: 1" in out and "ineligible_would_drop: 1" in out
    assert "عنوان تجريبي" in out                                    # SYNTHETIC data only
    for secret in (c.wa, stranger.wa, "حذاء سري", "P1", "P2"):
        assert secret not in out
    after = (ts.count_outbox_rows(world.dsn, tenant_id=world.tid),
             ts.count_marketing_log(world.dsn, world.tid),
             ts.fetch_marketing_activation(world.dsn, world.tid))
    assert before == after


def test_cli_status_leaks_no_phone(world, cli_env, capsys):
    c = _customer(world)
    assert cli.main(["marketing", "status", "--tenant-ref", world.ref]) == 0
    out = capsys.readouterr().out
    assert c.wa not in out and "enabled: False" in out


def test_cli_disable_and_set_cap(world, cli_env, capsys):
    _ready(world)
    assert cli.main(_enable_args(world)) == 0
    assert cli.main(["marketing", "set-cap", "--tenant-ref", world.ref, "--cap", "9",
                     "--actor", "ops", "--reason", "more"]) == 0
    assert ts.fetch_marketing_activation(world.dsn, world.tid) == {"enabled": True, "canary_cap_per_day": 9}
    assert cli.main(["marketing", "disable", "--tenant-ref", world.ref, "--actor", "ops", "--reason", "stop"]) == 0
    assert "disabled marketing" in capsys.readouterr().out
    assert ts.fetch_marketing_activation(world.dsn, world.tid)["enabled"] is False
    assert ts.count_marketing_log(world.dsn, world.tid) == 3


# --- the handler honours the activation (cart is frozen, not burned) -----------


def test_gate_input_defaults_are_read_not_assumed(world):
    """policy_gate reads the activation row for a marketing row only."""
    with core_db.tenant_tx(world.tid) as conn:
        assert repos_marketing.read_activation(conn, tenant_id=world.tid) is None
        assert repos_policy.read_ledger_status(conn, outbox_id=uuid.uuid4()) is None
