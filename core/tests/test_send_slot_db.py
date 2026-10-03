"""core/tests/test_send_slot_db.py - F-P3-10: app.reserve_send_slot on a real DB.

Re-runs the architect's probes as role sharwa_app through tenant_tx. No sleep;
the clock is injected via p_now (H84). Columns are read by name (H86).
"""
from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timezone

import pytest

from app import db as core_db
from app.db import repos_policy
from app.db import testsupport as db_testsupport

pytestmark = pytest.mark.db

PNOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)
# Asia/Riyadh (UTC+3): next local midnight after PNOW is 2026-10-04 00:00 +03.
NEXT_LOCAL_MIDNIGHT = datetime(2026, 10, 3, 21, 0, 0, tzinfo=timezone.utc)
DAY = "2026-10-03"


@pytest.fixture()
def health_ctx():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    tenant_id = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"slot-{uuid.uuid4()}", name="Slot Tenant",
    )
    channel_id = db_testsupport.insert_channel_account(
        dsn, tenant_id=tenant_id, type_="whatsapp_baileys",
        session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
    )
    yield dsn, tenant_id, channel_id
    db_testsupport.delete_tenant_full(dsn, tenant_id)


def _reserve(conn, channel_id, cls, *, gap_s=0, p_now=PNOW):
    return repos_policy.reserve_send_slot(
        conn, channel_id=channel_id, message_class=cls, gap_s=gap_s, p_now=p_now,
    )


def _seed(dsn, tid, chid, **kw):
    db_testsupport.seed_number_health(
        dsn, tenant_id=tid, channel_id=chid, day=DAY, **kw,
    )


def test_first_reservation_reserved(health_ctx):
    dsn, tid, chid = health_ctx
    _seed(dsn, tid, chid, daily_cap=3, sent_today=0)
    with core_db.tenant_tx(tid) as conn:
        v = _reserve(conn, chid, "marketing")
    assert v["verdict"] == "reserved"
    assert v["sent_today"] == 1


def test_second_reservation_spacing(health_ctx):
    dsn, tid, chid = health_ctx
    _seed(dsn, tid, chid, daily_cap=3, sent_today=0)
    with core_db.tenant_tx(tid) as conn:
        first = _reserve(conn, chid, "marketing", gap_s=60)
        second = _reserve(conn, chid, "marketing", gap_s=60)
    assert first["verdict"] == "reserved"
    assert second["verdict"] == "spacing"
    assert second["defer_until"] > PNOW


def test_cap_reached_defers_to_next_local_midnight(health_ctx):
    dsn, tid, chid = health_ctx
    _seed(dsn, tid, chid, daily_cap=3, sent_today=3)
    with core_db.tenant_tx(tid) as conn:
        v = _reserve(conn, chid, "marketing")
    assert v["verdict"] == "cap_reached"
    assert v["defer_until"] == NEXT_LOCAL_MIDNIGHT


def test_utility_counter_independent_of_marketing(health_ctx):
    dsn, tid, chid = health_ctx
    _seed(dsn, tid, chid, daily_cap=3, sent_today=3, utility_daily_cap=100, utility_sent_today=0)
    with core_db.tenant_tx(tid) as conn:
        marketing = _reserve(conn, chid, "marketing")
        utility = _reserve(conn, chid, "utility")
    assert marketing["verdict"] == "cap_reached"
    assert utility["verdict"] == "reserved"
    assert utility["sent_today"] == 1


def test_paused_blocks_marketing_only(health_ctx):
    dsn, tid, chid = health_ctx
    _seed(dsn, tid, chid, daily_cap=3, sent_today=0, state="paused")
    with core_db.tenant_tx(tid) as conn:
        marketing = _reserve(conn, chid, "marketing")
        utility = _reserve(conn, chid, "utility")
    assert marketing["verdict"] == "paused"
    assert utility["verdict"] == "reserved"


def test_release_slot_decrements_own_class_only_and_never_below_zero(health_ctx):
    dsn, tid, chid = health_ctx
    _seed(dsn, tid, chid, daily_cap=3, sent_today=1, utility_sent_today=1)
    with core_db.tenant_tx(tid) as conn:
        repos_policy.release_send_slot(conn, channel_id=chid, message_class="marketing")
    with core_db.tenant_tx(tid) as conn:
        row = repos_policy.read_number_health(conn, channel_id=chid)
    assert row["sent_today"] == 0
    assert row["utility_sent_today"] == 1  # untouched by a marketing release
    with core_db.tenant_tx(tid) as conn:
        repos_policy.release_send_slot(conn, channel_id=chid, message_class="marketing")
    with core_db.tenant_tx(tid) as conn:
        row = repos_policy.read_number_health(conn, channel_id=chid)
    assert row["sent_today"] == 0  # never below zero


def test_day_rollover_resets(health_ctx):
    dsn, tid, chid = health_ctx
    _seed(dsn, tid, chid, daily_cap=3, sent_today=5)
    with core_db.tenant_tx(tid) as conn:
        conn.execute(
            "UPDATE number_health SET day = %s::date WHERE channel_account_id = %s",
            ("2026-10-02", chid),
        )
    with core_db.tenant_tx(tid) as conn:
        v = _reserve(conn, chid, "marketing")
    assert v["verdict"] == "reserved"
    assert v["sent_today"] == 1  # the stale day was reset before reserving


def test_other_tenant_sees_no_health_row(health_ctx):
    dsn, tid, chid = health_ctx
    _seed(dsn, tid, chid, daily_cap=3, sent_today=0)
    other_tid = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"slot-other-{uuid.uuid4()}", name="Other Tenant",
    )
    try:
        with core_db.tenant_tx(other_tid) as conn:
            v = _reserve(conn, chid, "marketing")
        assert v["verdict"] == "no_health_row"  # RLS: another tenant's row is invisible
    finally:
        db_testsupport.delete_tenant_full(dsn, other_tid)


def test_system_role_permission_denied(health_ctx):
    dsn, tid, chid = health_ctx
    _seed(dsn, tid, chid, daily_cap=3, sent_today=0)
    with core_db.system_tx() as conn:
        with pytest.raises(Exception):
            _reserve(conn, chid, "marketing")


def _race_reserve(tid, chid, gap_s: int) -> list[dict]:
    results: list[dict] = []
    lock = threading.Lock()

    def worker():
        with core_db.tenant_tx(tid) as conn:
            v = _reserve(conn, chid, "marketing", gap_s=gap_s)
        with lock:
            results.append(v)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_race_20_threads_cap_7_gap_0_exactly_seven_reserved(health_ctx):
    dsn, tid, chid = health_ctx
    _seed(dsn, tid, chid, daily_cap=7, sent_today=0)
    results = _race_reserve(tid, chid, gap_s=0)
    assert len(results) == 20
    assert sum(1 for r in results if r["verdict"] == "reserved") == 7


def test_race_20_threads_gap_60_exactly_one_reserved(health_ctx):
    dsn, tid, chid = health_ctx
    _seed(dsn, tid, chid, daily_cap=20, sent_today=0)
    results = _race_reserve(tid, chid, gap_s=60)
    assert sum(1 for r in results if r["verdict"] == "reserved") == 1

