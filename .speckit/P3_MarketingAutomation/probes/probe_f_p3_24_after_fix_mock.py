"""P3 Task 5 - same scenario as probe_f_p3_24_mock.py, AFTER the 0019 fix.
In-memory model of carts + cart_tombstones copied from the SQL in repos_carts.py.
Also replays the reminder-time race (tombstone committed after the cart opened).
Run from core/:  PYTHONPATH=. python ../.speckit/P3_MarketingAutomation/probes/probe_f_p3_24_after_fix_mock.py
"""
import sys, uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

for name in ("psycopg", "psycopg.types", "psycopg.types.json", "psycopg.rows", "psycopg_pool"):
    sys.modules.setdefault(name, mock.MagicMock(name=name))

from app import cart_events as carts  # noqa: E402
from app.workers import cart_reminder  # noqa: E402

DB = {"carts": {}, "tomb": {}, "jobs": {}}
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)

def finalize_cart(conn, *, tenant_id, platform_cart_id, status):
    c = DB["carts"].get(platform_cart_id)
    if c and c["status"] == "open":
        c["status"] = status; return True
    return False
def tombstone_unseen_cart(conn, *, tenant_id, platform_cart_id, status, occurred_at):
    if platform_cart_id in DB["carts"] or platform_cart_id in DB["tomb"]:
        return False
    DB["tomb"][platform_cart_id] = status; return True
def cart_tombstone_status(conn, *, tenant_id, platform_cart_id):
    return DB["tomb"].get(platform_cart_id)
def upsert_cart_open(conn, *, tenant_id, customer_id, platform_cart_id, occurred_at, snapshot):
    c = DB["carts"].setdefault(platform_cart_id, {"status": "open", "last": occurred_at,
                                                  "customer_id": customer_id, "snapshot": {}})
    return c["last"], c["status"]
def lock_cart(conn, *, tenant_id, platform_cart_id):
    c = DB["carts"].get(platform_cart_id)
    return None if c is None else {"status": c["status"], "last_activity_at": c["last"],
                                   "customer_id": c["customer_id"], "snapshot": c["snapshot"]}
def schedule(conn, *, tenant_id, kind, dedupe_key, run_at, payload, max_lateness_s):
    if dedupe_key in DB["jobs"]: return False
    DB["jobs"][dedupe_key] = "pending"; return True
def cancel(conn, *, tenant_id, dedupe_key, reason):
    if DB["jobs"].get(dedupe_key) == "pending": DB["jobs"][dedupe_key] = "cancelled"

R = carts.repos_carts
patches = [mock.patch.object(R, n, f) for n, f in [
    ("finalize_cart", finalize_cart), ("tombstone_unseen_cart", tombstone_unseen_cart),
    ("cart_tombstone_status", cart_tombstone_status), ("upsert_cart_open", upsert_cart_open),
    ("lock_cart", lock_cart), ("customer_for_identity", lambda *a, **k: uuid.uuid4()),
    ("build_snapshot", lambda **k: {})]] + [
    mock.patch.object(carts.repos_scheduler, "schedule", schedule),
    mock.patch.object(carts.repos_scheduler, "cancel", cancel),
    mock.patch.object(carts.repos_scheduler, "reschedule", lambda *a, **k: None)]
for p in patches: p.start()

kw = dict(tenant_id=uuid.uuid4(), delay_h=24, max_late_s=43200, title_max=60, default_country_code="967")
upd = lambda cid: {"type": "cart.updated", "cart_id": cid, "occurred_at": NOW,
                   "customer": {"wa_id": "967700000000"}, "item_count": 1,
                   "total_minor": 100, "currency": "YER", "items": []}

r1 = carts.apply_cart_event(None, event={"type": "cart.recovered", "cart_id": "C1", "occurred_at": NOW}, **kw)
r2 = carts.apply_cart_event(None, event=upd("C1"), **kw)
print("A) recovered-before-updated:", r1, "->", r2, "| cart:", DB["carts"].get("C1"), "| job:", DB["jobs"].get("cart:C1:stage1"))
okA = DB["carts"].get("C1") is None and "cart:C1:stage1" not in DB["jobs"]

# B) race: cart opened first, tombstone committed concurrently (unseen by the webhook guard)
carts.apply_cart_event(None, event=upd("C2"), **kw)
DB["tomb"]["C2"] = "recovered"
settings = SimpleNamespace(cart_reminder_delay_h=24)
res = cart_reminder.handle_cart_reminder(settings, None, tenant_id=kw["tenant_id"],
                                         payload={"cart_id": "C2"}, now=NOW + timedelta(hours=25))
print("B) reminder after concurrent tombstone:", type(res).__name__, getattr(res, "reason", res), "| cart:", DB["carts"]["C2"]["status"])
okB = type(res).__name__ == "Cancel" and DB["carts"]["C2"]["status"] == "recovered"

# C) regression: normal open cart still schedules; terminal on a seen cart writes no tombstone
r = carts.apply_cart_event(None, event=upd("C3"), **kw)
r3 = carts.apply_cart_event(None, event={"type": "cart.recovered", "cart_id": "C3", "occurred_at": NOW}, **kw)
print("C) normal path:", r, "| then recovered:", r3, "| tombstone:", DB["tomb"].get("C3"), "| job:", DB["jobs"].get("cart:C3:stage1"))
okC = r == "applied" and r3 == "applied" and "C3" not in DB["tomb"] and DB["jobs"]["cart:C3:stage1"] == "cancelled"

print("FIX HOLDS:", okA and okB and okC, "(A=%s B=%s C=%s)" % (okA, okB, okC))
sys.exit(0 if (okA and okB and okC) else 1)
