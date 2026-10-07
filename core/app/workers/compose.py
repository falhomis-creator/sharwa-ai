"""core/app/workers/compose.py - the SINGLE outbound-text composition module (L7).

H38: no model text ever reaches a customer. Every outbox payload text is built
here, from exactly four allowed sources: approved templates (templates.py),
product titles from catalog_products (the merchant's own text), kb_chunks content
(the merchant's own policy text), and the closed order-status map (P1.7 §6, the
ONLY authorised fourth function). Six functions - compose_address_options (P2.2)
is the fifth, compose_stock_notice (P2.3) the sixth and compose_size_reply
(P4 Task 18b-1) the seventh.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from app.fit.size_advisor import SizeAdvice
from app.workers import templates

# Fixed template: titles verbatim, then a human-handoff offer in the tail.
PRODUCT_LIST_TEMPLATE = (
    "وجدتُ لك هذه المنتجات المطابقة:\n{items}\n\n"
    "سأحوّل محادثتك لممثل خدمة العملاء لأي تفاصيل إضافية. 🛍️"
)

MAX_POLICY_CHARS = 2000

# P1.7 §6: the closed platform-status -> approved-Arabic-label map (H55). An
# unknown code => no card (caller returns order_status_unknown); raw platform text
# never reaches the customer.
ORDER_STATUS_LABELS: dict[str, str] = {
    "pending": "قيد المعالجة",
    "confirmed": "مؤكّد",
    "shipped": "تم الشحن",
    "delivered": "تم التسليم",
    "cancelled": "ملغى",
}

ORDER_STATUS_TEMPLATE = "طلبك {ref_tail}: {status_label}\nآخر تحديث: {updated_label}"


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


def _relative_updated(updated_at: str | None) -> str:
    """A relative, approved label - never a raw platform timestamp (H55)."""
    if not updated_at:
        return "غير متاح"
    try:
        day = _dt.date.fromisoformat(str(updated_at)[:10])
    except ValueError:
        return "غير متاح"
    today = _dt.datetime.now(_dt.timezone.utc).date()
    if day == today:
        return "اليوم"
    if day == today - _dt.timedelta(days=1):
        return "أمس"
    return day.isoformat()


def compose_order_status(card: dict[str, Any], *, labels: dict[str, str]) -> str | None:
    """The status card: status + relative updated time + the LAST THREE chars of
    the order ref only (never the full number - H54). No amounts, no fees, no raw
    platform text (H36/H55). Returns None when the status code is unknown."""
    status = card.get("status")
    label = labels.get(str(status)) if status is not None else None
    if label is None:
        return None
    ref = str(card.get("ref", ""))
    ref_tail = ref[-3:] if ref else ""
    updated_label = _relative_updated(card.get("updated_at"))
    return ORDER_STATUS_TEMPLATE.format(ref_tail=ref_tail, status_label=label, updated_label=updated_label)


def compose_address_options(candidates: list[str], *, template: str) -> str:
    """List gazetteer names VERBATIM (H67: no generated description, no
    coordinates - the pin is confirmed back to the customer as a place name,
    never as numbers). `template` is one of the address_* templates; the two with
    sentinels (address_confirm -> «name», address_disambiguate -> «options») are
    filled with the literal names only."""
    text = templates.template_text(template)
    if "«options»" in text:
        return text.replace("«options»", "\n".join(f"• {c}" for c in candidates))
    if "«name»" in text and candidates:
        return text.replace("«name»", candidates[0])
    return text


def compose_stock_notice(kind: str, title: str) -> str:
    """The sixth (P2.3) composition function: a back-in-stock notice. The product
    title is the merchant's own text, filled verbatim into the «name» sentinel
    (H35: title only - no price, no available quantity)."""
    text = templates.template_text(kind)
    if "«name»" in text:
        return text.replace("«name»", title)
    return text


# P4 Task 18b-1: which size template answers which advice. Closed; anything not
# covered here is a hand-off (fail closed), never an invented sentence.
_SIZE_HANDOFF_REASONS = frozenset({"no_chart", "invalid_chart"})
_SIZE_ASK_REASONS = frozenset({"missing_height_weight", "implausible_input"})


def compose_size_reply(advice: SizeAdvice) -> tuple[str, str]:
    """The seventh composition function: (template_id, text) for a SizeAdvice.
    The ONLY variable parts are the advice's own size / alt_size labels (the
    merchant's chart labels), filled after the word «مقاس» - so the Verifier's
    size rule always sees them (OQ-P4-15). The model never writes this text."""
    reasons = set(advice.reasons)
    if advice.size is not None and advice.out_of_range:
        template_id = "size_nearest_out_of_range"
    elif advice.size is not None and advice.alt_size is not None:
        template_id = "size_recommend_alt"
    elif advice.size is not None:
        template_id = "size_recommend"
    elif advice.out_of_range:
        template_id = "size_no_fit"
    elif reasons & _SIZE_HANDOFF_REASONS:
        template_id = "size_no_chart"
    elif reasons & _SIZE_ASK_REASONS:
        template_id = "size_need_inputs"
    else:
        template_id = "handoff_notice"
    text = templates.template_text(template_id)
    if advice.size is not None:
        text = text.replace("«size»", advice.size)
    if advice.alt_size is not None:
        text = text.replace("«alt»", advice.alt_size)
    return template_id, text
