"""core/app/workers/verify_rules.py - the deterministic output-verifier rules (H45).

Pure and deterministic: regex + explicit blocklists only. No LLM, no IO, no DB,
no network, no randomness, no time, no mutable global state (H45). check_text is
a PURE function: the same text always yields the same verdict.

Imports are CLOSED to exactly: re, dataclasses, typing, enum, __future__,
app.text.arabic (S10-b). The blocklist vocabulary itself never lives here - it is
policy data owned by the merchant/owner and arrives pre-built via build_rules()
from app.workers.config (H45 + S8 rule 3).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.text import arabic

# ---- structural checks -------------------------------------------------------

# Control chars, Unicode Bidi/formatting marks, and zero-width spaces - the same
# set arabic.normalize strips for comparison, but the structure check runs on the
# RAW text so a smuggled zero-width char is caught before normalization erases it.
# NOTE: the basic whitespace controls \t \n \r are deliberately EXCLUDED - the
# approved PRODUCT_LIST_TEMPLATE legitimately contains newlines, and blocking them
# would block every product list.
_CONTROL_RE = re.compile(
    "[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u061c\u200b\u200c\u200d\u200e\u200f"
    "\u202a-\u202e\u2060-\u206f\ufeff]"
)

# An unsubstituted format placeholder - a leaked template literal (H38).
_PLACEHOLDER_RE = re.compile(r"\{[^{}]*\}")

# Strip punctuation/emoji from token ENDS only (never from the middle). \W is
# Unicode-aware: Arabic/English letters and digits are word chars, punctuation and
# emoji are not.
_END_STRIP_RE = re.compile(r"^[\W_]+|[\W_]+$")

# Separator characters used to smuggle a word through a naive matcher, plus the
# Arabic tatweel (U+0640). Removed before collapsing runs of one character.
_SQUEEZE_TRANS = str.maketrans("", "", " .-_*+'\"\u0640")


def squeeze(token: str) -> str:
    """Remove separator/smuggling chars, then collapse runs of one char to one.

    'ك.ل.ب', 'كـلـب' and 'كلللب' all become 'كلب' (the same canonical form), so a
    blocked phrase written with separators or doubled letters still matches.
    """
    token = token.translate(_SQUEEZE_TRANS)
    collapsed: list[str] = []
    prev = ""
    for ch in token:
        if ch != prev:
            collapsed.append(ch)
            prev = ch
    return "".join(collapsed)


def _strip_token(token: str) -> str:
    return _END_STRIP_RE.sub("", token)


@dataclass(frozen=True)
class _Phrase:
    tokens: tuple[str, ...]      # normalized, end-stripped tokens
    squeezed: tuple[str, ...]    # squeeze() of each token


def _match_phrase(tokens: list[str], phrase: _Phrase) -> bool:
    """Equality (NOT substring) match against a window of len(phrase.tokens)
    consecutive tokens. Each position matches by raw equality OR squeeze equality
    (H49: block on doubt, but never substring-scan - a short blocked phrase must
    not match a longer clean word like 'زبون'/'زبدة')."""
    n = len(phrase.tokens)
    if n == 0 or n > len(tokens):
        return False
    for i in range(len(tokens) - n + 1):
        window = tokens[i:i + n]
        if all(
            w == p or squeeze(w) == sq
            for w, p, sq in zip(window, phrase.tokens, phrase.squeezed)
        ):
            return True
    return False


@dataclass(frozen=True)
class BlocklistSet:
    """Frozen, pre-normalized blocklists built once at boot (H45)."""
    profanity: tuple[_Phrase, ...]
    competitor: tuple[_Phrase, ...]
    disclosure: tuple[_Phrase, ...]
    ignored_empty: int = 0


@dataclass(frozen=True)
class RuleVerdict:
    """ok=True => rule_id/category are None. Otherwise a rule_id from the CLOSED
    list below (never extended without an architectural decision - H20/H4)."""
    ok: bool
    rule_id: str | None
    category: str | None


@dataclass(frozen=True)
class VerifiedText:
    """Text that passed check_text. Built ONLY via approve() - S10-e makes any
    other construction visible to the static gate; Python cannot enforce it at
    runtime, but mypy + S10-e can catch the bypass in review (not 'prevent' it)."""
    value: str


def _build_phrases(raw: tuple[str, ...]) -> tuple[tuple[_Phrase, ...], int]:
    """Normalize + end-strip each phrase ONCE here. A phrase that normalizes to
    empty is ignored (never matches everything) and counted."""
    phrases: list[_Phrase] = []
    ignored = 0
    for text in raw:
        norm = arabic.normalize(text)
        tokens = tuple(_strip_token(t) for t in norm.split(" ") if _strip_token(t))
        if not tokens:
            ignored += 1
            continue
        phrases.append(_Phrase(tokens=tokens, squeezed=tuple(squeeze(t) for t in tokens)))
    return tuple(phrases), ignored


def build_rules(
    *,
    profanity: tuple[str, ...],
    competitor: tuple[str, ...],
    disclosure: tuple[str, ...],
) -> BlocklistSet:
    """Build a frozen BlocklistSet from raw policy lists (called once at boot from
    app.workers.config - never at check time)."""
    p, pi = _build_phrases(profanity)
    c, ci = _build_phrases(competitor)
    d, di = _build_phrases(disclosure)
    return BlocklistSet(profanity=p, competitor=c, disclosure=d, ignored_empty=pi + ci + di)


def _violation(rule_id: str, category: str) -> RuleVerdict:
    return RuleVerdict(ok=False, rule_id=rule_id, category=category)


def check_text(text: str, *, rules: BlocklistSet, max_chars: int) -> RuleVerdict:
    """The single pure entry point. Structure checks first (empty -> oversize ->
    control_chars -> placeholder), then blocklists (profanity -> competitor ->
    disclosure). The FIRST violation wins - order is fixed and written (H45)."""
    if not text.strip():
        return _violation("empty", "structure")
    if len(text) > max_chars:
        return _violation("oversize", "structure")
    if _CONTROL_RE.search(text):
        return _violation("control_chars", "structure")
    if _PLACEHOLDER_RE.search(text):
        return _violation("placeholder", "structure")

    norm = arabic.normalize(text)
    tokens = [_strip_token(t) for t in norm.split(" ")]
    for rule_id, phrases in (
        ("profanity", rules.profanity),
        ("competitor", rules.competitor),
        ("disclosure", rules.disclosure),
    ):
        for phrase in phrases:
            if _match_phrase(tokens, phrase):
                return _violation(rule_id, "blocklist")
    return RuleVerdict(ok=True, rule_id=None, category=None)


def approve(text: str, *, rules: BlocklistSet, max_chars: int) -> VerifiedText | RuleVerdict:
    """The ONLY constructor of VerifiedText: check_text first, then approve."""
    verdict = check_text(text, rules=rules, max_chars=max_chars)
    if verdict.ok:
        return VerifiedText(value=text)
    return verdict

