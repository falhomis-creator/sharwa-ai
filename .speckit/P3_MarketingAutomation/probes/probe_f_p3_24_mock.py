"""P3 Task 3 - probe for F-P3-24 (terminal cart event BEFORE the first cart.updated).

NOT part of the test suite (no pinning of current behaviour). Mock level: psycopg is
stubbed; repos_carts / repos_scheduler are replaced by an in-memory model whose
semantics are copied 1:1 from the SQL in app/db/repos_carts.py:
  finalize_cart  = UPDATE carts ... WHERE platform_cart_id=? AND status='open'  -> rowcount>0
  upsert_cart_open = INSERT ... ON CONFLICT DO UPDATE ... WHERE status='open' RETURNING
Run from core/:  python ../.speckit/P3_MarketingAutomation/probes/probe_f_p3_24_mock.py
"""
import sys, types, uuid
from datetime import datetime, timezone
from unittest import mock

for name in ("psycopg", "psycopg.types", "psycopg.types.json", "psycopg.rows", "psycopg_pool"):
    sys.modules.setdefault(name, mock.MagicMock(name=name))

from app import cart_events as carts  # noqa: E402

carts_db = {}          # platform_cart_id -> status
jobs = {}              # dedupe_key -> status

def finalize_cart(conn, *, tenant_id, platform_cart_id, status):
    if carts_db.get(platform_cart_id) == "open":
        carts_db[platform_cart_id] = status
        return True
    return False       # missing row => 0 rows updated, NOTHING persisted

def upsert_cart_open(conn, *, tenant_id, customer_id, platform_cart_id, occurred_at, snapshot):
    if platform_cart_id not in carts_db:
        carts_db[platform_cart_id] = "open"
    return occurred_at, carts_db[platform_cart_id]

def schedule(conn, *, tenant_id, kind, dedupe_key, run_at, payload, max_lateness_s):
    if dedupe_key in jobs:
        return False
    jobs[dedupe_key] = "pending"
    return True

def cancel(conn, *, tenant_id, dedupe_key, reason):
    if jobs.get(dedupe_key) == "pending":
        jobs[dedupe_key] = "cancelled"

T = uuid.uuid4()
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
kw = dict(tenant_id=T, delay_h=24, max_late_s=43200, title_max=60, default_country_code="967")

with mock.patch.object(carts.repos_carts, "finalize_cart", finalize_cart), \
     mock.patch.object(carts.repos_carts, "upsert_cart_open", upsert_cart_open), \
     mock.patch.object(carts.repos_carts, "customer_for_identity", lambda *a, **k: uuid.uuid4()), \
     mock.patch.object(carts.repos_carts, "build_snapshot", lambda **k: {}), \
     mock.patch.object(carts.repos_scheduler, "schedule", schedule), \
     mock.patch.object(carts.repos_scheduler, "cancel", cancel), \
     mock.patch.object(carts.repos_scheduler, "reschedule", lambda *a, **k: None):
    r1 = carts.apply_cart_event(None, event={"type": "cart.recovered", "cart_id": "C1",
                                             "occurred_at": NOW}, **kw)
    print("1) cart.recovered on unseen cart ->", r1, "| carts:", dict(carts_db), "| jobs:", dict(jobs))
    r2 = carts.apply_cart_event(None, event={"type": "cart.updated", "cart_id": "C1",
                                             "occurred_at": NOW, "customer": {"wa_id": "967700000000"},
                                             "item_count": 1, "total_minor": 100, "currency": "YER",
                                             "items": []}, **kw)
    print("2) late cart.updated               ->", r2, "| carts:", dict(carts_db), "| jobs:", dict(jobs))

bug = carts_db.get("C1") == "open" and jobs.get("cart:C1:stage1") == "pending"
print("F-P3-24 REPRODUCED (purchased cart reopened + reminder scheduled):", bug)
sys.exit(0 if bug else 1)
