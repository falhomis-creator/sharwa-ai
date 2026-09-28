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

    def lookup_order(
        self, *, tenant_ref: str, order_ref: str,
        phone_candidates: tuple[str, ...], path: str,
    ) -> dict[str, Any] | None:
        """Fetch one order card, or None when no card can be returned.

        None is the ONLY failure value - no reason, no error code (H53, the
        anti-oracle boundary). "not found", "phone mismatch", "another tenant"
        and "no matching candidate" all collapse to the same None.
        """
        ...
