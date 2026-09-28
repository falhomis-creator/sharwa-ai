"""core/app/tools/extract.py - deterministic order-ref and phone extraction (H51).

Pure, no IO. The model only classifies the intent (order_status); THIS module
extracts the order number and phone candidates with fixed regexes (H51) - never
the LLM. Phone digits use app.text.arabic.normalize (the single normalizer, H21).
"""
from __future__ import annotations

import re

from app.text import arabic

_PHONE_RUN_RE = re.compile(r"\d{7,}")
_NON_DIGITS_RE = re.compile(r"\D")


def extract_order_ref(texts: tuple[str, ...], pattern: re.Pattern[str]) -> str | None:
    """The FIRST order-ref match across the turn's texts (never try several)."""
    for text in texts:
        m = pattern.search(text)
        if m:
            return m.group(0)
    return None


def extract_phone_candidates(texts: tuple[str, ...], *, max_candidates: int = 3) -> tuple[str, ...]:
    """All 7+ digit runs across the texts, Arabic-Indic digits mapped to Latin via
    app.text.arabic.normalize. Capped at max_candidates. Returns RAW digit runs;
    to_e164() normalizes each (a run that cannot be normalized is dropped by the
    caller - never guessed)."""
    candidates: list[str] = []
    for text in texts:
        norm = arabic.normalize(text)
        for m in _PHONE_RUN_RE.finditer(norm):
            digits = m.group(0)
            if digits not in candidates:
                candidates.append(digits)
            if len(candidates) >= max_candidates:
                return tuple(candidates)
    return tuple(candidates)


def to_e164(raw: str, default_country: str) -> str | None:
    """Normalize one digit run to E.164, or None when it cannot be normalized
    (a bare national number without a leading 0/country code, or an implausible
    length). Fail-closed (H52)."""
    digits = _NON_DIGITS_RE.sub("", raw)
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("+"):
        digits = digits[1:]
    elif digits.startswith("0"):
        digits = default_country + digits[1:]
    elif not digits.startswith(default_country):
        return None
    if len(digits) < 8 or len(digits) > 15:
        return None
    return "+" + digits
