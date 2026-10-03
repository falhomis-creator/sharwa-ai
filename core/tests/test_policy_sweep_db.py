"""core/tests/test_policy_sweep_db.py - F-P3-19 coverage: the sweeper (H81).

Deterministic: time is injected via `now`; health/state columns are seeded. The
warm-up ladder default is {0:20, 3:40, 7:80, 14:150, 30:250}.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import MISSING, fields
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import db as core_db
from app.db import repos_policy
from app.db import testsupport as db_testsupport
from app.workers import policy_sweep
from app.workers.config import WorkerSettings

pytestmark = pytest.mark.db

PNOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)


def _settings(**kw):
    d: dict = {}
    for f in fields(WorkerSettings):
        if f.name.startswith("send_policy_"):
            if f.default is not MISSING:
                d[f.name] = f.default
            elif f.default_factory is not MISSING:
                d[f.name] = f.default_factory()
    d.update(kw)
    return SimpleNamespace(**d)


@pytest.fixture()
def sweep_ctx():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    tid = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"sweep-{uuid.uuid4()}", name="Sweep Tenant",
    )
    chid = db_testsupport.insert_channel_account(
        dsn, tenant_id=tid, type_="whatsapp_baileys",
        session_id=f"sess-{uuid.uuid4()}", status="connected", engine="ai_core",
    )
    yield dsn, tid, chid
    db_testsupport.delete_tenant_full(dsn, tid)


def _sweep(dsn, tid, chid, now):
    return policy_sweep._sweep_channel(_settings(), tid, chid, now=now)


def _health(tid, chid):
    with core_db.tenant_tx(tid) as conn:
        return repos_policy.read_number_health(conn, channel_id=chid)


def test_sweep_creates_row_with_day0_cap(sweep_ctx):
    dsn, tid, chid = sweep_ctx
    state = _sweep(dsn, tid, chid, PNOW)
    assert state == "healthy"
    assert _health(tid, chid)["daily_cap"] == 20  # ladder day 0


def test_sweep_throttled_halves_cap(sweep_ctx):
    dsn, tid, chid = sweep_ctx
    db_testsupport.seed_number_health(
        dsn, tenant_id=tid, channel_id=chid, state="throttled",
        warmup_started_at="2026-10-03T00:00:00Z",
    )
    _sweep(dsn, tid, chid, PNOW)
    assert _health(tid, chid)["daily_cap"] == 10  # 20 * 0.5


def test_sweep_throttled_to_healthy_waits_cooldown(sweep_ctx):
    dsn, tid, chid = sweep_ctx
    db_testsupport.seed_number_health(
        dsn, tenant_id=tid, channel_id=chid, state="throttled",
        state_changed_at=PNOW.isoformat(),
    )
    # fresh state_changed_at: cooldown not elapsed -> stays throttled
    assert _sweep(dsn, tid, chid, PNOW) == "throttled"
    # 25h later: cooldown elapsed -> healthy
    later = PNOW + timedelta(hours=25)
    assert _sweep(dsn, tid, chid, later) == "healthy"


def test_sweep_paused_never_exits(sweep_ctx):
    dsn, tid, chid = sweep_ctx
    db_testsupport.seed_number_health(dsn, tenant_id=tid, channel_id=chid, state="paused")
    assert _sweep(dsn, tid, chid, PNOW) == "paused"


def test_sweep_idle_resets_warmup(sweep_ctx):
    dsn, tid, chid = sweep_ctx
    db_testsupport.seed_number_health(
        dsn, tenant_id=tid, channel_id=chid,
        warmup_started_at=(PNOW - timedelta(days=30)).isoformat(),
    )
    _sweep(dsn, tid, chid, PNOW)
    assert _health(tid, chid)["warmup_started_at"] is None
