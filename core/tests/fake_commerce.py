"""core/tests/fake_commerce.py - FakeCommerce, a CommercePort backed by a dict/JSON.

The real platform (sharwa_saas) has not implemented its catalog events yet, so
the receiver is tested against this fake. It implements the same CommercePort
protocol as app/commerce/port.py and serves get_changes/get_snapshot from a
small document:

    {
      "changes": {
        "<tenant_ref>": {
          "pages": [{"cursor": "c1", "events": [...]}, ...]
        }
      },
      "snapshot": {
        "<tenant_ref>": [events...]
      }
    }
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.commerce.port import CommercePort, project_order_card


class FakeCommerce(CommercePort):
    def __init__(self, source: dict[str, Any] | Path | str | None = None) -> None:
        if source is None:
            self._data: dict[str, Any] = {"changes": {}, "snapshot": {}}
        elif isinstance(source, (Path, str)):
            self._data = json.loads(Path(source).read_text(encoding="utf-8"))
        else:
            self._data = source

    def get_changes(self, tenant_ref: str, since: str | None) -> tuple[list[dict[str, Any]], str]:
        pages = self._data.get("changes", {}).get(tenant_ref, {}).get("pages", [])
        idx = 0
        if since is not None:
            for i, page in enumerate(pages):
                if page.get("cursor") == since:
                    idx = i + 1
                    break
        if idx >= len(pages):
            return [], since or ""
        page = pages[idx]
        return list(page.get("events", [])), page.get("cursor", "")

    def get_snapshot(self, tenant_ref: str, page: str | None) -> tuple[list[dict[str, Any]], str | None]:
        events = self._data.get("snapshot", {}).get(tenant_ref, [])
        return list(events), None

    def lookup_order(
        self, *, tenant_ref: str, order_ref: str,
        phone_candidates: tuple[str, ...], path: str,
    ) -> dict[str, Any] | None:
        """The anti-oracle boundary (PROMPT_P1_07 §5.2): None identically for
        'not found', 'phone mismatch', 'another tenant' and 'no matching
        candidate'. Phone matching happens HERE (inside the fake = the platform),
        never in core/."""
        orders = self._data.get("orders", {}).get(tenant_ref, {})
        order = orders.get(order_ref)
        if order is None:
            return None  # not found OR another tenant (identical None)
        order_phone = order.get("phone")
        if not phone_candidates or order_phone not in phone_candidates:
            return None  # phone mismatch (identical None)
        # N3 (P1.7 audit): closed projection - the platform never sends a phone,
        # address or amount back to core; only ref/status/updated_at cross here.
        return project_order_card(order)
