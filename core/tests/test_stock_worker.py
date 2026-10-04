"""core/tests/test_stock_worker.py - pure coordinator tests (P2.3) with recording
doubles: join dedupe, no-variant, compose, and the sweep's H40/H73 discipline.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.workers import compose, stock


def _settings(**kw) -> SimpleNamespace:
    defaults = dict(
        stock_max_waitlist_per_customer=10,
        stock_hold_ttl_s=3600,
        stock_observation_max_age_s=300,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_join_waitlist_registers_once(monkeypatch):
    customer_id = uuid.uuid4()
    inserts: list[dict] = []
    monkeypatch.setattr(stock.repos_outbox, "customer_id_for_conversation", lambda conn, cid: customer_id)
    monkeypatch.setattr(stock.repos_stock, "read_conversation_slots", lambda conn, **kw: {"last_shown_product_ids": ["VAR-X"]})
    monkeypatch.setattr(stock.repos_stock, "has_active_waitlist", lambda conn, **kw: False)
    monkeypatch.setattr(stock.repos_stock, "count_active_waitlists", lambda conn, **kw: 0)
    monkeypatch.setattr(
        stock.repos_stock, "insert_waitlist_entry",
        lambda conn, **kw: (inserts.append(kw) or uuid.uuid4()),
    )
    consents: list[dict] = []
    monkeypatch.setattr(stock.repos_consent, "write_consent", lambda conn, **kw: consents.append(kw))

    d = stock.join_waitlist(
        None, _settings(), tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(), bodies=("سجّلني لهذا المنتج",),
    )
    assert d.kind == "joined"
    assert d.platform_variant_id == "VAR-X"
    assert len(inserts) == 1
    # D3: joining the waitlist writes a back_in_stock consent in the same tx.
    assert consents and consents[0]["scope"] == "back_in_stock"
    assert consents[0]["granted"] is True


def test_join_waitlist_duplicate_rejected(monkeypatch):
    customer_id = uuid.uuid4()
    inserts: list[dict] = []
    monkeypatch.setattr(stock.repos_outbox, "customer_id_for_conversation", lambda conn, cid: customer_id)
    monkeypatch.setattr(stock.repos_stock, "read_conversation_slots", lambda conn, **kw: {"last_shown_product_ids": ["VAR-X"]})
    monkeypatch.setattr(stock.repos_stock, "has_active_waitlist", lambda conn, **kw: True)
    monkeypatch.setattr(
        stock.repos_stock, "insert_waitlist_entry",
        lambda conn, **kw: (inserts.append(kw) or uuid.uuid4()),
    )

    d = stock.join_waitlist(None, _settings(), tenant_id=uuid.uuid4(), conversation_id=uuid.uuid4(), bodies=("سجّلني",))
    assert d.kind == "already_waiting"
    assert inserts == []  # no second live row written (P2.3 §3 item 2)


def test_join_waitlist_no_variant(monkeypatch):
    monkeypatch.setattr(stock.repos_outbox, "customer_id_for_conversation", lambda conn, cid: uuid.uuid4())
    monkeypatch.setattr(stock.repos_stock, "read_conversation_slots", lambda conn, **kw: {})
    d = stock.join_waitlist(None, _settings(), tenant_id=uuid.uuid4(), conversation_id=uuid.uuid4(), bodies=("مرحبا",))
    assert d.kind == "no_variant"


def test_compose_stock_notice_fills_title_and_never_quantity():
    text = compose.compose_stock_notice("stock_available", "قميص قطني")
    assert "قميص قطني" in text
    assert "«name»" not in text
    assert "3" not in text  # H35: never an available quantity


def test_sweep_stale_observation_skips_allocation(monkeypatch):
    allocated: list[dict] = []
    monkeypatch.setattr(stock.repos_stock, "list_expired_held_entry_ids", lambda conn, **kw: [])
    monkeypatch.setattr(stock.repos_stock, "expire_holds", lambda conn, **kw: 0)
    monkeypatch.setattr(
        stock.repos_stock, "allocate_holds",
        lambda conn, **kw: (allocated.append(kw) or []),
    )
    commerce = SimpleNamespace(get_stock_observation=lambda *, tenant_ref, platform_variant_id: {
        "available": 5, "observed_at": "2000-01-01T00:00:00Z",
    })
    stock._sweep_variant(
        commerce, _settings(stock_observation_max_age_s=300), None,
        tenant_id=uuid.uuid4(), platform_ref="r", variant="VAR-X",
    )
    assert allocated == []  # H73: an old observation never allocates or notifies


def test_sweep_allocates_and_notifies_in_one_transaction(monkeypatch):
    notifs: list[dict] = []
    entry_id = uuid.uuid4()
    monkeypatch.setattr(stock.repos_stock, "list_expired_held_entry_ids", lambda conn, **kw: [])
    monkeypatch.setattr(stock.repos_stock, "expire_holds", lambda conn, **kw: 0)
    monkeypatch.setattr(
        stock.repos_stock, "allocate_holds",
        lambda conn, **kw: [{"waitlist_entry_id": entry_id}],
    )
    monkeypatch.setattr(stock.repos_stock, "fetch_variant_title", lambda conn, **kw: "قميص")
    monkeypatch.setattr(stock.repos_stock, "fetch_notify_target", lambda conn, **kw: {
        "conversation_id": uuid.uuid4(), "channel_account_id": uuid.uuid4(),
        "to_wa_id": "967700000001", "epoch": 1,
    })
    monkeypatch.setattr(
        stock.proactive, "enqueue_proactive",
        lambda conn, **kw: (notifs.append(kw) or uuid.uuid4()),
    )

    stock._expire_allocate_notify(
        None, _settings(), None, tenant_id=uuid.uuid4(), variant="VAR-X", available=1,
    )
    assert len(notifs) == 1
    assert notifs[0]["template_id"] == "stock_available"
    assert notifs[0]["merge"]["title"] == "قميص"
