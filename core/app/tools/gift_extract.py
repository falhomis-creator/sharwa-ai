"""core/app/tools/gift_extract.py - the gift request reader (P4 Task 14), pure.

Reads a customer's own words (WhatsApp, Arabic or English) and returns WHAT
they asked for - never a decision. Deterministic: regex + word lists only, no
model, no IO (the tools-are-pure contract).

  * is_gift: the message names a gift (هدية / هدايا / gift / present). A code
    rule, like the size trigger (owner decision for 18b): the router model is
    not consulted for it.
  * budget_major: the amount in the customer's own unit (a Decimal), or None.
    "20 الف" / "20 ألف" = 20000; "20,000" / "20٬000" = 20000. When several
    numbers appear, the one tied to a currency word or "ألف" wins; a single
    bare number is the budget; anything else is ambiguous => None (we ask).
    Ages ("10 سنوات") are never a budget.
  * currency_hint: the currency the customer NAMED - "YER", "SAR", "USD",
    "AED", "RIYAL" (a bare ريال: Yemeni or Saudi) or None. sharwa_ai never
    converts currency (P4 constitution §3); the coordinator only checks it.
  * query: what is left once gift words, amounts and filler are removed - the
    recipient / interest words used to rank candidates ("لأمي تحب العطور").
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.text.arabic import normalize

_GIFT_RE = re.compile(
    r"(?:^|\s)(?:ل|و|ب)?(?:ال)?(?:هديه|هدايا|هديتي|هديتك|هديته|هديتها)(?=\s|$)"
    r"|\b(?:gifts?|presents?)\b"
)

# A number: grouped thousands, or a plain integer/decimal (Latin digits after normalize).
_NUM = r"(\d{1,3}(?:[,٬]\d{3})+|\d+(?:[.٫]\d+)?)"
_THOUSAND = r"(?:الف|الاف|k)"
_CURRENCY_WORDS: tuple[tuple[str, str], ...] = (
    (r"ريال\s+يمني|ر\.?\s?ي\b|يمني", "YER"),
    (r"ريال\s+سعودي|ر\.?\s?س\b|سعودي", "SAR"),
    (r"دولار|\$|usd", "USD"),
    (r"درهم|aed", "AED"),
    (r"ريال|ريالات|sar|yer", "RIYAL"),
)
_AGE_WORDS = r"(?:سنه|سنوات|سنين|عام|اعوام|شهر|شهور|اشهر|years?|yrs?|months?)"
_AMOUNT_RE = re.compile(
    _NUM + r"\s*(" + _THOUSAND + r")?\s*(" + "|".join(p for p, _c in _CURRENCY_WORDS) + r")?"
,
)
_AGE_AFTER_RE = re.compile(r"\s*" + _AGE_WORDS + r"(?:\s|$)")
_FILLER = frozenset({
    "ابي", "ابغى", "ابغي", "اريد", "بدي", "عايز", "ودي", "ابحث", "عن", "في", "على", "من",
    "حدود", "بحدود", "ميزانيتي", "ميزانيه", "ميزانية", "بميزانيه", "تقريبا", "مع", "الى",
    "او", "و", "ب", "بـ", "ل", "لـ", "شي", "شيء", "حق", "اقل", "اكثر", "need", "want", "a", "for",
    "budget", "around", "under", "about", "i",
})
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


@dataclass(frozen=True)
class GiftRequest:
    is_gift: bool
    budget_major: Decimal | None
    currency_hint: str | None
    query: str


def _currency_of(word: str) -> str | None:
    if not word:
        return None
    for pattern, code in _CURRENCY_WORDS:
        if re.fullmatch(pattern, word):
            return code
    return None


def _to_decimal(raw: str) -> Decimal | None:
    cleaned = raw.replace(",", "").replace("٬", "").replace("٫", ".")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return value if value > 0 else None


def extract_gift_request(bodies: tuple[str, ...]) -> GiftRequest:
    """The customer's gift request, read from this turn's messages (pure)."""
    text = normalize(" ".join(b for b in bodies if b))
    is_gift = bool(_GIFT_RE.search(text))

    tied: list[tuple[Decimal, str | None]] = []
    bare: list[Decimal] = []
    for m in _AMOUNT_RE.finditer(text):
        value = _to_decimal(m.group(1))
        if value is None or (not m.group(3) and _AGE_AFTER_RE.match(text, m.end(1))):
            continue  # "10 سنوات" is an age, never a budget
        if m.group(2):
            value *= 1000
        currency = _currency_of(m.group(3) or "")
        if m.group(2) or m.group(3):
            tied.append((value, currency))
        else:
            bare.append(value)
    budget: Decimal | None = None
    currency_hint: str | None = None
    if len(tied) == 1:
        budget, currency_hint = tied[0]
    elif not tied and len(bare) == 1:
        budget = bare[0]

    rest = _AMOUNT_RE.sub(" ", text)
    rest = _GIFT_RE.sub(" ", rest)
    words = [w for w in _WORD_RE.findall(rest) if w not in _FILLER and len(w) > 1]
    words = [w for w in words if _currency_of(w) is None and not re.fullmatch(_THOUSAND, w)]
    return GiftRequest(is_gift=is_gift, budget_major=budget, currency_hint=currency_hint,
                       query=" ".join(words))
