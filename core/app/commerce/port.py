"""core/app/commerce/port.py - CommercePort, the single platform abstraction (C7).

Exactly one interface and (in production) one adapter (SharwaCommerceAdapter).
The interface exists for isolation and testing - NOT to support other platforms
(D2: sharwa_ai is bound exclusively to sharwa_saas). A FakeCommerce in tests/
implements this same protocol from a JSON file.
"""
from __future__ import annotations

from typing import Any, Protocol

# N3 (P1.7 audit): the order card that reaches core carries ONLY these fields -
# never phone, address, amounts, or any other platform field. The read model must
# not even name a field beyond this closed set (H36 in spirit). Both the real
# adapter and the test FakeCommerce project through this same constant.
ORDER_CARD_FIELDS = ("ref", "status", "updated_at")


def project_order_card(card: dict[str, Any]) -> dict[str, Any]:
    """Closed projection of one platform order row. Anything not in
    ORDER_CARD_FIELDS is dropped at the boundary, so a phone/address/amount on the
    platform side never reaches core (fail-closed: a missing field is simply
    absent from the projected dict)."""
    return {k: card[k] for k in ORDER_CARD_FIELDS if k in card}


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

    def get_stock_observation(
        self, *, tenant_ref: str, platform_variant_id: str,
    ) -> dict[str, Any] | None:
        """A fresh, dated stock observation for one variant, or None when the
        platform has no observation. H73: the platform is the stock authority;
        `observed_at` is the platform's own timestamp, never a clock core owns."""
        ...
