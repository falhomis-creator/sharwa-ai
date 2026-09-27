"""core/app/commerce/port.py - CommercePort, the single platform abstraction (C7).

Exactly one interface and (in production) one adapter (SharwaCommerceAdapter).
The interface exists for isolation and testing - NOT to support other platforms
(D2: sharwa_ai is bound exclusively to sharwa_saas). A FakeCommerce in tests/
implements this same protocol from a JSON file.
"""
from __future__ import annotations

from typing import Any, Protocol


class CommercePort(Protocol):
    """The two platform read operations the catalog needs in this batch (P1.4)."""

    def get_changes(self, tenant_ref: str, since: str | None) -> tuple[list[dict[str, Any]], str]:
        """Fetch catalog events newer than `since` for one tenant.

        Returns (events, next_cursor) where next_cursor is an opaque string to
        pass back as `since` on the next call.
        """
        ...

    def get_snapshot(self, tenant_ref: str, page: str | None) -> tuple[list[dict[str, Any]], str | None]:
        """Fetch one full-snapshot page of a tenant's catalog.

        Returns (events, next_page) where next_page is None on the last page.
        """
        ...
