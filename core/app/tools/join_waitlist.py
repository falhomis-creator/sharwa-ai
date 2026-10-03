"""core/app/tools/join_waitlist.py - the join_waitlist tool (P2.3, H50).

Third tool in the closed registry. Pure: no DB, no network, no `.execute()`, no
app.db import. It receives PRE-READ slots (last_shown_product_ids) and this
turn's texts and returns which platform_variant_id the customer wants - the
coordinator (workers/stock.py) owns the read-before-write dedupe and the actual
waitlist_entries insert.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.tools.registry import ToolContext


@dataclass(frozen=True)
class JoinWaitlistDecision:
    """The result. `kind` is the single discriminator: the tool emits
    joined|no_variant; the coordinator adds already_waiting|unavailable."""
    kind: str  # joined | no_variant | already_waiting | unavailable
    platform_variant_id: str | None = None


def extract_variant(ctx: "ToolContext") -> str | None:
    """The variant the customer wants: the most-recently-shown product card, or
    None when no card was shown and no variant can be resolved (fail-closed -
    never guessed)."""
    ids = ctx.last_shown_product_ids
    if ids:
        return ids[-1]
    return None


def run(ctx: "ToolContext") -> JoinWaitlistDecision:
    variant = extract_variant(ctx)
    if variant is None:
        return JoinWaitlistDecision(kind="no_variant")
    return JoinWaitlistDecision(kind="joined", platform_variant_id=variant)
