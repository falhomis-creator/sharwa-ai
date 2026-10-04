"""core/app/workers/optout.py - deterministic opt-out detection (P1.1.5, H16).

100% deterministic word-list matching - no classifier, no LLM, no embedding
(H16). A single normalize() function with a table-driven test suite, and a
single detect() that matches the WHOLE normalized message or its beginning -
never a partial match buried mid-text (a customer typing "لا توقف طلبي" must
not opt out).

Owner decision (recorded in P1_DEVIATIONS.md): `cancel` / `الغاء` alone are NOT
opt-out - they mean "cancel my order" in a commerce context. Only the phrases
below count.

The suppression effect itself (which scopes to block) lives in
app/db/repos_ingest.py; this module only classifies text and never logs the
message text (H20).

normalize() now lives in app/text/arabic.py (P1.4 C6) and is re-exported here
so the existing `optout.normalize(...)` call sites (including the untouched
tests) keep working against the single shared normalizer.
"""
from __future__ import annotations

from app.text.arabic import normalize  # re-exported: optout.normalize stays valid (H21)


def _match(message: str, phrases: tuple[str, ...]) -> bool:
    for phrase in phrases:
        p = normalize(phrase)
        if not p:
            continue
        # Whole message OR its beginning equals the phrase; never a partial
        # match mid-text (spec, literal).
        if message == p or message.startswith(p + " "):
            return True
    return False


def detect(message: str, *, phrases_ar: tuple[str, ...], phrases_en: tuple[str, ...]) -> tuple[str, ...]:
    """Return the language tags ('ar'/'en'/both) whose opt-out phrases match.
    Empty tuple = not an opt-out. Never returns the matched text (H20)."""
    if not message:
        return ()
    normalized = normalize(message)
    if not normalized:
        return ()
    detected: list[str] = []
    if _match(normalized, phrases_ar):
        detected.append("ar")
    if _match(normalized, phrases_en):
        detected.append("en")
    return tuple(detected)


def detect_optin(message: str, *, phrases_ar: tuple[str, ...], phrases_en: tuple[str, ...]) -> bool:
    """P3.3 (H96): True only when the WHOLE normalized message EQUALS one of
    the opt-in phrases - no startswith, no contains, no classifier (an opt-in
    is an explicit, unambiguous word; "اشتراك الباقة كم سعرها" is a question,
    not a consent). STOP precedence is the CALLER's rule (checked first);
    a message that matches both lists is a STOP, and «إيقاف اشتراك» never
    full-equals an opt-in phrase anyway. Never logs the text (H20)."""
    if not message:
        return False
    normalized = normalize(message)
    if not normalized:
        return False
    for phrase in tuple(phrases_ar) + tuple(phrases_en):
        p = normalize(phrase)
        if p and p == normalized:
            return True
    return False


def detect_handoff(message: str, *, phrases_ar: tuple[str, ...], phrases_en: tuple[str, ...]) -> bool:
    """D6: True when the customer explicitly asks for a human (same normalize +
    whole/beginning match, same deterministic word-list discipline - H25)."""
    if not message:
        return False
    normalized = normalize(message)
    if not normalized:
        return False
    return _match(normalized, phrases_ar) or _match(normalized, phrases_en)
