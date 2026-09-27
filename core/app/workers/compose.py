"""core/app/workers/compose.py - the SINGLE outbound-text composition module (L7).

H38: no model text ever reaches a customer. Every outbox payload text is built
here, from exactly three allowed sources: approved templates (templates.py),
product titles from catalog_products (the merchant's own text), and kb_chunks
content (the merchant's own policy text). Three functions, no fourth.

S8 (rule 2) enforces that any insert_outbox(... text=X ...) uses X produced by
one of these three functions - never a raw model string.
"""
from __future__ import annotations

from typing import Any

from app.workers import templates

# Fixed template: titles verbatim, then a human-handoff offer in the tail.
PRODUCT_LIST_TEMPLATE = (
    "وجدتُ لك هذه المنتجات المطابقة:\n{items}\n\n"
    "سأحوّل محادثتك لممثل خدمة العملاء لأي تفاصيل إضافية. 🛍️"
)

MAX_POLICY_CHARS = 2000


def compose_template(template_id: str) -> str:
    """An approved template, exactly as today."""
    return templates.template_text(template_id)


def compose_product_list(cards: list[dict[str, Any]]) -> str:
    """Titles only (H35: no price, no availability, no generated sentence). The
    titles are the merchant's own text, listed verbatim, <= 3 cards."""
    titles = [str(c["title"]) for c in cards[:3]]
    return PRODUCT_LIST_TEMPLATE.format(items="\n".join(f"• {t}" for t in titles))


def compose_policy_answer(content: str) -> str:
    """The merchant's own policy text, verbatim, truncated at the written cap."""
    return content[:MAX_POLICY_CHARS]
