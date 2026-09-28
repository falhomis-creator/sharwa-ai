"""core/app/tools/extract.py - deterministic order-ref and phone extraction (H51).

Pure, no IO. The model only classifies the intent (order_status); THIS module
extracts the order number and phone candidates with fixed regexes (H51) - never
the LLM. Phone digits use app.text.arabic.normalize (the single normalizer, H21).
"""
from __future__ import annotations

import re

from app.text import arabic

# A phone candidate is a merged digit run of at least this many digits. Runs
# shorter than this are not phones (H51/H52: fail-closed, never guessed).
_MIN_PHONE_DIGITS = 7

# Separators that can appear INSIDE one phone number (never between two numbers).
# A space is handled separately below because it is ambiguous: it joins two digit
# runs only when BOTH runs are shorter than a complete phone (< 7 digits).
_PHONE_GLUE_RE = re.compile(r"[().+\-]")
_WHITESPACE_RE = re.compile(r"\s+")
_DIGIT_RUN_RE = re.compile(r"\d+")
_NON_DIGITS_RE = re.compile(r"\D")


def extract_order_ref(texts: tuple[str, ...], pattern: re.Pattern[str]) -> str | None:
    """The FIRST order-ref match across the turn's texts (never try several)."""
    for text in texts:
        m = pattern.search(text)
        if m:
            return m.group(0)
    return None


def _merged_runs(text: str) -> list[str]:
    """Yield the merged digit runs of one normalized text (F-P1-10).

    `-`, `.`, `(`, `)`, `+` always join adjacent digit runs (they are unambiguous
    in-number glue). Whitespace joins them ONLY when both sides are shorter than a
    complete phone (< 7 digits), so `77 123 4567` and `+967 771 234 567` are one
    number while `77123456 99887766` stays TWO candidates (a space between two
    complete numbers is a boundary, not a separator)."""
    runs = list(_DIGIT_RUN_RE.finditer(text))
    merged: list[str] = []
    i = 0
    while i < len(runs):
        digits = runs[i].group(0)
        j = i
        while j + 1 < len(runs):
            gap = text[runs[j].end(): runs[j + 1].start()]
            if _PHONE_GLUE_RE.search(gap):
                pass  # unambiguously inside one number
            elif _WHITESPACE_RE.fullmatch(gap) and len(runs[j].group(0)) < _MIN_PHONE_DIGITS and len(runs[j + 1].group(0)) < _MIN_PHONE_DIGITS:
                pass  # space between two incomplete groups (e.g. 77 123 4567)
            else:
                break
            digits += runs[j + 1].group(0)
            j += 1
        if len(digits) >= _MIN_PHONE_DIGITS:
            merged.append(digits)
        i = j + 1
    return merged


def extract_phone_candidates(
    texts: tuple[str, ...],
    *,
    max_candidates: int = 3,
    exclude: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """All 7+ digit runs across the texts, Arabic-Indic digits mapped to Latin via
    app.text.arabic.normalize. Capped at max_candidates. Returns RAW digit runs;
    to_e164() normalizes each (a run that cannot be normalized is dropped by the
    caller - never guessed).

    `exclude` drops specific digit runs (N5: the order-ref match must not also be
    read as a phone candidate)."""
    candidates: list[str] = []
    for text in texts:
        norm = arabic.normalize(text)
        for digits in _merged_runs(norm):
            if digits in exclude:
                continue
            if digits not in candidates:
                candidates.append(digits)
            if len(candidates) >= max_candidates:
                return tuple(candidates)
    return tuple(candidates)


def to_e164(
    raw: str,
    default_country: str,
    *,
    national_len: int = 9,
    mobile_prefixes: tuple[str, ...] = ("7",),
) -> str | None:
    """Normalize one digit run to E.164, or None when it cannot be normalized
    (a bare national number without a leading 0/country code, or an implausible
    length). Fail-closed (H52).

    F-P1-10: the Yemeni national mobile format is nine digits starting with a
    mobile prefix (e.g. `7`) with NO leading zero. When the run has exactly
    `national_len` digits and starts with one of `mobile_prefixes`, the country
    code is prepended - otherwise a bare number is ignored, never guessed."""
    digits = _NON_DIGITS_RE.sub("", raw)
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = default_country + digits[1:]
    elif not digits.startswith(default_country):
        if len(digits) == national_len and any(digits.startswith(p) for p in mobile_prefixes):
            digits = default_country + digits
        else:
            return None
    if len(digits) < 8 or len(digits) > 15:
        return None
    return "+" + digits
