"""core/app/commerce/adapter.py - SharwaCommerceAdapter, the single CommercePort
implementation (D2: bound exclusively to sharwa_saas). HTTP transport lives in
app.channels.commerce_client (H24: only channels may reach the network); this
adapter only shapes the HTTP payload into the CommercePort contract."""
from __future__ import annotations

from typing import Any

from app.channels.commerce_client import CommerceClient
from app.commerce.port import CommercePort, project_order_card


class SharwaCommerceAdapter(CommercePort):
    def __init__(self, client: CommerceClient) -> None:
        self._client = client

    def get_changes(self, tenant_ref: str, since: str | None) -> tuple[list[dict[str, Any]], str]:
        payload = self._client.get_changes(tenant_ref, since)
        return payload["events"], payload["next_cursor"]

    def get_snapshot(self, tenant_ref: str, page: str | None) -> tuple[list[dict[str, Any]], str | None]:
        payload = self._client.get_snapshot(tenant_ref, page)
        return payload["events"], payload.get("next_page")

    def lookup_order(
        self, *, tenant_ref: str, order_ref: str,
        phone_candidates: tuple[str, ...], path: str,
    ) -> dict[str, Any] | None:
        card = self._client.lookup_order(tenant_ref, order_ref, phone_candidates, path)
        if card is None:
            return None
        # N3: closed projection - only ref/status/updated_at reach core.
        return project_order_card(card)
