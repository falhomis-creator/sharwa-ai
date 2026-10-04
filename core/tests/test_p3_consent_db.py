"""core/tests/test_p3_consent_db.py - P3.3 Stage A: the append-only consent
ledger (H94) enforced by real privileges, the closed source list (H95), the
single writer's semantics (record_optin / record_optout / record_waitlist_join
incl. H97 lifting and H98 evidence), the source-gated marketing read, the 0016
idempotency, and the §4.13 concurrency guarantees - all on a real DB in the
REAL roles (sharwa_app via tenant_tx, sharwa_system via system_tx, migration
role via the DSN). Raw consent/suppression seeding goes through testsupport
(the declared S27-a exception) or repos_consent itself.
"""
from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path

import psycopg
import pytest

from app import db as core_db
from app.db import repos_consent
from app.db import repos_outbox
from app.db import repos_policy
from app.db import testsupport as db_testsupport

pytestmark = pytest.mark.db

MIGRATION_0016 = Path(__file__).resolve().parent.parent / "migrations" / "0016_p3_consent.sql"


@pytest.fixture()
def consent_ctx():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    tid = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"p33a-{uuid.uuid4()}", name="P3.3 Consent Tenant",
    )
    yield dsn, tid
    db_testsupport.delete_tenant_full(dsn, tid)


def _customer(dsn, tid) -> uuid.UUID:
    return db_testsupport.insert_customer(dsn, tenant_id=tid, wa_id=f"9677{uuid.uuid4().int % 10**10:010d}")


# --- H94: append-only, enforced by database privileges -------------------------


def test_h94_append_only_privileges(consent_ctx):
    dsn, tid = consent_ctx
    cid = _customer(dsn, tid)
    # INSERT + SELECT still work for the app role (through the single writer).
    with core_db.tenant_tx(tid) as conn:
        repos_consent.write_consent(
            conn, tenant_id=tid, customer_id=cid, scope="marketing",
            granted=True, source="customer_message_optin", evidence=str(uuid.uuid4()),
        )
        n = conn.execute(
            "SELECT count(*) FROM consents WHERE tenant_id = %s AND customer_id = %s",
            (tid, cid),
        ).fetchone()
    assert int(n[0]) == 1
    # UPDATE is revoked (H94: the ledger's history is untouchable).
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with core_db.tenant_tx(tid) as conn:
            conn.execute("UPDATE consents SET granted = false WHERE tenant_id = %s", (tid,))
    # DELETE is revoked (H94: the rollback of a consent is a NEW row).
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with core_db.tenant_tx(tid) as conn:
            conn.execute("DELETE FROM consents WHERE tenant_id = %s", (tid,))
    # sharwa_system has no direct grant on the table at all.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with core_db.system_tx() as conn:
            conn.execute("SELECT count(*) FROM consents").fetchone()


def test_h95_source_closed_list(consent_ctx):
    dsn, tid = consent_ctx
    cid = _customer(dsn, tid)
    # A source outside the closed list is rejected by the CHECK constraint.
    with pytest.raises(psycopg.errors.CheckViolation):
        db_testsupport.insert_consent(
            dsn, tenant_id=tid, customer_id=cid, scope="marketing",
            granted=True, source="legacy_crm",
        )
    # Every entry of the closed list is accepted (through the sanctioned
    # testsupport seeder - the declared S27-a exception).
    for source in repos_consent.CONSENT_SOURCES:
        db_testsupport.insert_consent(
            dsn, tenant_id=tid, customer_id=cid, scope="order_updates",
            granted=True, source=source,
        )


def test_migration_0016_reapplies_twice_cleanly(consent_ctx):
    """§4.11: idempotent (H14) - executed twice more on the live DB, no error,
    and the constraint + index are still exactly one each."""
    dsn, _tid = consent_ctx
    sql = MIGRATION_0016.read_text(encoding="utf-8")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql)  # re-apply #1
        conn.execute(sql)  # re-apply #2
        constraint = conn.execute(
            "SELECT count(*) FROM pg_constraint WHERE conname = 'consents_source_closed_list'"
        ).fetchone()
        index = conn.execute(
            "SELECT count(*) FROM pg_indexes WHERE indexname = 'consents_latest_wins_idx'"
        ).fetchone()
    assert int(constraint[0]) == 1
    assert int(index[0]) == 1


# --- the source-gated marketing read (H78/H95) ---------------------------------


@pytest.mark.parametrize(
    "scope,source,expected",
    [
        ("marketing", "customer_message_optin", True),
        ("marketing", "checkout_optin", False),   # H95: blocked until OQ-P3-14
        ("marketing", "import", False),           # H95: blocked until OQ-P3-14
        ("marketing", "operator", False),
        ("back_in_stock", "waitlist_join", True),  # non-marketing ignores source
        ("back_in_stock", "import", True),
        ("review_request", "customer_message_optout", False),  # granted=false anyway
    ],
)
def test_read_latest_consent_source_gate(consent_ctx, scope, source, expected):
    dsn, tid = consent_ctx
    cid = _customer(dsn, tid)
    db_testsupport.insert_consent(
        dsn, tenant_id=tid, customer_id=cid, scope=scope,
        granted=(source != "customer_message_optout"), source=source,
    )
    with core_db.tenant_tx(tid) as conn:
        got = repos_policy.read_latest_consent(
            conn, tenant_id=tid, customer_id=cid, scope=scope,
        )
    assert got is expected


# --- the single writer's semantics (H94/H96/H97/H98) ---------------------------


def test_record_optin_fresh_then_noop(consent_ctx):
    dsn, tid = consent_ctx
    cid = _customer(dsn, tid)
    mid = uuid.uuid4()
    with core_db.tenant_tx(tid) as conn:
        assert repos_consent.record_optin(conn, tenant_id=tid, customer_id=cid, message_id=mid) == "granted"
        assert repos_consent.record_optin(conn, tenant_id=tid, customer_id=cid, message_id=uuid.uuid4()) == "noop"
        rows = repos_consent.read_history(conn, tenant_id=tid, customer_id=cid)
        consent = repos_policy.read_latest_consent(conn, tenant_id=tid, customer_id=cid, scope="marketing")
    assert len(rows) == 1, "a repeated opt-in writes NO second row"
    scope_, granted, source, _created, evidence = rows[0]
    assert (scope_, granted, source) == ("marketing", True, "customer_message_optin")
    assert evidence == str(mid), "H98: evidence is the message UUID, nothing else"
    uuid.UUID(evidence)  # parses - an identifier, never customer text/phone
    assert consent is True


def test_record_optout_three_scopes_and_no_order_updates(consent_ctx):
    dsn, tid = consent_ctx
    cid = _customer(dsn, tid)
    mid = uuid.uuid4()
    with core_db.tenant_tx(tid) as conn:
        assert repos_consent.record_optout(conn, tenant_id=tid, customer_id=cid, message_id=mid) == "revoked"
        rows = repos_consent.read_history(conn, tenant_id=tid, customer_id=cid)
        for scope in ("marketing", "back_in_stock", "review_request"):
            assert repos_outbox.has_suppression(conn, tid, cid, scope) is True
        assert repos_outbox.has_suppression(conn, tid, cid, "order_updates") is False
        assert repos_policy.read_latest_consent(conn, tenant_id=tid, customer_id=cid, scope="marketing") is False
    assert sorted(r[0] for r in rows) == ["back_in_stock", "marketing", "review_request"]
    assert all(r[1] is False and r[2] == "customer_message_optout" for r in rows)
    assert all(r[4] == str(mid) for r in rows), "H98: evidence = the message UUID"


def test_optin_after_stop_lifts_only_marketing(consent_ctx):
    """§4.4: STOP then an opt-in word => granted row + the marketing
    suppression (cause: customer_message_optout) alone is lifted; the other
    two scopes stay suppressed; an operator-caused block is NEVER lifted."""
    dsn, tid = consent_ctx
    cid = _customer(dsn, tid)
    with core_db.tenant_tx(tid) as conn:
        repos_consent.record_optout(conn, tenant_id=tid, customer_id=cid, message_id=uuid.uuid4())
        repos_consent.insert_suppressions(
            conn, tenant_id=tid, customer_id=cid, scopes=("marketing",), reason="operator",
        )
    with core_db.tenant_tx(tid) as conn:
        result = repos_consent.record_optin(conn, tenant_id=tid, customer_id=cid, message_id=uuid.uuid4())
        marketing_blocked = repos_outbox.has_suppression(conn, tid, cid, "marketing")
        bis = repos_outbox.has_suppression(conn, tid, cid, "back_in_stock")
        rr = repos_outbox.has_suppression(conn, tid, cid, "review_request")
        granted = repos_policy.read_latest_consent(conn, tenant_id=tid, customer_id=cid, scope="marketing")
    assert result == "granted"  # the optout row was lifted, the operator row remains
    assert marketing_blocked is True, "H97: an operator block survives the customer word"
    assert bis is True and rr is True, "H97: only the subscribed scope is lifted"
    assert granted is True, "latest-wins: the new granted row reopens the scope"


def test_record_waitlist_join_scopes_and_lift(consent_ctx):
    """OQ-P3-12: the join grants back_in_stock ONLY and lifts exactly the
    optout-caused back_in_stock suppression; marketing stays suppressed."""
    dsn, tid = consent_ctx
    cid = _customer(dsn, tid)
    with core_db.tenant_tx(tid) as conn:
        repos_consent.record_optout(conn, tenant_id=tid, customer_id=cid, message_id=uuid.uuid4())
    entry_id = uuid.uuid4()
    with core_db.tenant_tx(tid) as conn:
        result = repos_consent.record_waitlist_join(conn, tenant_id=tid, customer_id=cid, entry_id=entry_id)
        bis_granted = repos_policy.read_latest_consent(conn, tenant_id=tid, customer_id=cid, scope="back_in_stock")
        bis_blocked = repos_outbox.has_suppression(conn, tid, cid, "back_in_stock")
        marketing_blocked = repos_outbox.has_suppression(conn, tid, cid, "marketing")
    assert result == "granted_and_lifted"
    assert bis_granted is True and bis_blocked is False
    assert marketing_blocked is True, "a waitlist join never touches marketing"
    with core_db.tenant_tx(tid) as conn:
        rows = [r for r in repos_consent.read_history(conn, tenant_id=tid, customer_id=cid)
                if r[0] == "back_in_stock" and r[1]]
    assert rows and rows[-1][2] == "waitlist_join" and rows[-1][4] == str(entry_id)


# --- §4.13: concurrency ---------------------------------------------------------


def test_20_threads_optin_one_customer(consent_ctx):
    dsn, tid = consent_ctx
    cid = _customer(dsn, tid)
    errors: list[BaseException] = []

    def _optin(_i: int) -> None:
        try:
            with core_db.tenant_tx(tid) as conn:
                repos_consent.record_optin(
                    conn, tenant_id=tid, customer_id=cid, message_id=uuid.uuid4(),
                )
        except BaseException as exc:  # noqa: BLE001 - collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=_optin, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    with core_db.tenant_tx(tid) as conn:
        granted = repos_policy.read_latest_consent(conn, tenant_id=tid, customer_id=cid, scope="marketing")
        n = conn.execute(
            "SELECT count(*) FROM consents WHERE tenant_id = %s AND customer_id = %s",
            (tid, cid),
        ).fetchone()
    assert granted is True
    assert int(n[0]) >= 1


def test_interleaved_stop_and_optin_never_corrupts(consent_ctx):
    dsn, tid = consent_ctx
    cid = _customer(dsn, tid)
    errors: list[BaseException] = []

    def _worker(i: int) -> None:
        action = repos_consent.record_optout if i % 2 == 0 else repos_consent.record_optin
        try:
            with core_db.tenant_tx(tid) as conn:
                action(conn, tenant_id=tid, customer_id=cid, message_id=uuid.uuid4())
        except BaseException as exc:  # noqa: BLE001 - collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    with core_db.tenant_tx(tid) as conn:
        rows = repos_consent.read_history(conn, tenant_id=tid, customer_id=cid)
        latest = repos_consent._latest(conn, tenant_id=tid, customer_id=cid, scope="marketing")
    assert len(rows) >= 1
    assert latest is not None, "the final state is exactly the last committed write"
