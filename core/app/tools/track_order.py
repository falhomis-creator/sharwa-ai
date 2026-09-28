"""core/app/tools/track_order.py - the track_order tool (H50/H52/H53).

Pure: receives a frozen ToolContext and returns a frozen OrderLookup. No DB, no
direct httpx (the CommercePort is injected), no logging, no outbox knowledge. The
platform call happens through the injected port; the coordinator (orders.py) owns
transactions and audit logging.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.tools import extract

if TYPE_CHECKING:
    from app.tools.registry import ToolContext


@dataclass(frozen=True)
class OrderLookup:
    """The tool result. `kind` is the single discriminator; the four non-card
    kinds carry NO information about whether the order exists (H53)."""
    kind: str  # card | unverified | need_order_ref | need_phone | unavailable
    card: dict[str, Any] | None = None


def resolve_path(channel_phone_e164: str | None, phone_candidates: tuple[str, ...]) -> str:
    """same_number ONLY when the channel identity is normalizable AND (no phone
    was mentioned OR a candidate equals it). Otherwise other_number (H52: fail
    closed)."""
    if channel_phone_e164 is not None and (not phone_candidates or channel_phone_e164 in phone_candidates):
        return "same_number"
    return "other_number"


def run(ctx: ToolContext) -> OrderLookup:
    order_ref = extract.extract_order_ref(ctx.message_texts, ctx.settings.order_ref_pattern)
    if order_ref is None:
        return OrderLookup(kind="need_order_ref")

    raw = extract.extract_phone_candidates(
        ctx.message_texts, max_candidates=ctx.settings.order_lookup_max_phone_candidates,
    )
    country = ctx.settings.default_country_code
    candidates = tuple(c for c in (extract.to_e164(r, country) for r in raw) if c)

    path = resolve_path(ctx.channel_phone_e164, candidates)
    phones = (ctx.channel_phone_e164,) if path == "same_number" else candidates

    if path == "other_number" and not phones:
        return OrderLookup(kind="need_phone")

    card = ctx.commerce.lookup_order(
        tenant_ref=ctx.tenant_ref, order_ref=order_ref,
        phone_candidates=phones, path=path,
    )
    if card is None:
        return OrderLookup(kind="unverified")
    return OrderLookup(kind="card", card=card)
