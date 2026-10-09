"""core/app/tools/join_waitlist.py - the join_waitlist tool (P2.3, H50).

Third tool in the closed registry. Pure: no DB, no network, no `.execute()`, no
app.db import. It receives PRE-READ slots (last_shown_product_ids) and this
turn's texts and returns which PRODUCT the customer means (F-P4-07: the slot
carries platform_product_id values, written by code when product cards are
shown) - the coordinator (workers/stock.py) resolves the product to its single
variant, owns the read-before-write dedupe and the waitlist_entries insert.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.tools.registry import ToolContext


@dataclass(frozen=True)
class JoinWaitlistDecision:
    """The result. `kind` is the single discriminator: the tool emits
    joined|no_variant; the coordinator adds already_waiting|unavailable. The
    tool sets platform_product_id; only the coordinator sets platform_variant_id."""
    kind: str  # joined | no_variant | already_waiting | unavailable
    platform_variant_id: str | None = None
    platform_product_id: str | None = None


def extract_product(ctx: "ToolContext") -> str | None:
    """The product the customer means: the most-recently-shown product card, or
    None when no card was shown (fail-closed - never guessed)."""
    ids = ctx.last_shown_product_ids
    if ids:
        return ids[-1]
    return None


def run(ctx: "ToolContext") -> JoinWaitlistDecision:
    product = extract_product(ctx)
    if product is None:
        return JoinWaitlistDecision(kind="no_variant")
    return JoinWaitlistDecision(kind="joined", platform_product_id=product)
