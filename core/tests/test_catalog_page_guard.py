"""F-P4-03 page guard: a /changes page larger than the contract limit is not
applied and its cursor is not advanced (docs/PLATFORM_COMMERCE_CONTRACT.md §5).
Pure: the DB layer is monkeypatched, no live Postgres."""
from __future__ import annotations

import contextlib
import types
import uuid

import pytest

from app.workers import catalog


TENANT = uuid.uuid4()


def _patch(monkeypatch: pytest.MonkeyPatch, events: list[dict], cursor: str = "cursor-next"):
    monkeypatch.setattr(
        catalog.core_db, "system_tx", lambda: contextlib.nullcontext(object())
    )
    monkeypatch.setattr(
        catalog.core_db, "tenant_tx", lambda tenant_id: contextlib.nullcontext(object())
    )
    monkeypatch.setattr(
        catalog.repos_catalog, "list_catalog_sync_state",
        lambda conn: [(TENANT, "store1", None, None)],
    )
    calls: list = []

    def apply(conn, **kwargs):
        calls.append(("apply", kwargs["events"]))
        return {}

    def set_cursor(conn, **kwargs):
        calls.append(("cursor", kwargs["cursor"]))

    monkeypatch.setattr(catalog.repos_catalog, "apply_catalog_events", apply)
    monkeypatch.setattr(catalog.repos_catalog, "set_catalog_sync_cursor", set_cursor)

    class Port:
        def get_changes(self, tenant_ref, since):
            return events, cursor

    settings = types.SimpleNamespace(
        catalog_reconcile_max_tenants=100,
        catalog_reconcile_max_events_per_tenant=500,
    )
    return Port(), settings, calls


def test_oversized_page_is_not_applied_and_cursor_not_advanced(monkeypatch: pytest.MonkeyPatch):
    port, settings, calls = _patch(
        monkeypatch, [{"type": "product.upserted"} for _ in range(501)]
    )
    result = catalog.reconcile_once(port, settings=settings, seen_unknown_types=set())
    assert result == "failed"
    assert calls == []


def test_page_at_limit_is_applied_and_cursor_advances(monkeypatch: pytest.MonkeyPatch):
    events = [{"type": "product.upserted"} for _ in range(500)]
    port, settings, calls = _patch(monkeypatch, events)
    result = catalog.reconcile_once(port, settings=settings, seen_unknown_types=set())
    assert result == "ok"
    assert len(calls) == 2
    assert calls[0][0] == "apply" and len(calls[0][1]) == 500
    assert calls[1] == ("cursor", "cursor-next")
