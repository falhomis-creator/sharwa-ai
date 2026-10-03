"""core/app/tools/registry.py - the closed, literal-keyed tool registry (S11-c).

ToolContext carries only pre-read values + the injected port; ToolSpec binds a
name to a run callable. TOOLS is a literal dict - no register(), no dynamic
import, no globals(). Adding a tool means editing this literal and passing review.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ToolContext:
    """Everything a tool needs, read in advance. No DB connection here."""
    tenant_id: uuid.UUID
    conversation_id: uuid.UUID
    tenant_ref: str                 # platform_ref for the platform call
    channel_phone_e164: str | None  # the channel's phone identity (E.164), or None
    message_texts: tuple[str, ...]  # this turn's texts
    commerce: Any                   # injected CommercePort
    settings: Any
    # N2 (P1.7 audit): extracted ONCE in the coordinator (orders.py phase 1) and
    # frozen here, so the fingerprint logged (order_ref_hash/path) and the values
    # used for the platform call come from ONE extraction - never two that could
    # silently diverge.
    order_ref: str
    phone_candidates: tuple[str, ...]  # already-normalized E.164 candidates
    path: str                          # same_number | other_number
    # P2.3: the ids of the last product cards shown to this customer (<= 3),
    # pre-read from conversations.slots by the stock coordinator - the tool
    # resolves the requested variant from these (never by guessing).
    last_shown_product_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolSpec:
    name: str
    run: Callable[[ToolContext], Any]


# Imported AFTER ToolContext/ToolSpec so track_order / resolve_address (which
# import ToolContext back) do not deadlock on a partially-initialised registry.
from app.tools import join_waitlist  # noqa: E402
from app.tools import resolve_address  # noqa: E402
from app.tools import track_order  # noqa: E402

TOOLS: dict[str, ToolSpec] = {
    "track_order": ToolSpec("track_order", track_order.run),
    "resolve_address": ToolSpec("resolve_address", resolve_address.run),
    "join_waitlist": ToolSpec("join_waitlist", join_waitlist.run),
}
