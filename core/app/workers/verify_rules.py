"""core/app/workers/verify_rules.py - the deterministic output-verifier rules (H45).

Pure and deterministic: regex + explicit blocklists only. No LLM, no IO, no DB,
no network, no randomness, no time, no mutable global state (H45). check_text is
a PURE function: the same text always yields the same verdict.

Imports are CLOSED to exactly: re, dataclasses, typing, enum, __future__,
app.text.arabic (S10-b). The blocklist vocabulary itself never lives here - it is
policy data owned by the merchant/owner and arrives pre-built via build_rules()
from app.workers.config (H45 + S8 rule 3).

Task 11 adds the size_mismatch rule under the same discipline: regex + lists
only, fed the SizeAdvice outcome as a frozen SizeContext (plain strings - never
the model, never app.fit/app.tools; lint-imports guards both directions).
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
# NOTE: the space is deliberately ABSENT here (it never reaches squeeze - tokens
# are split on spaces first). Space-evasion ('ك ل ب' -> 'كلب') is caught by the
# joined-window match in _match_joined instead (PROMPT_P1_07 §0.2).
_SQUEEZE_TRANS = str.maketrans("", "", ".-_*+'\"\u0640")


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
    joined: str                  # squeeze("".join(tokens)) - the space-evasion form


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


def _match_joined(tokens: list[str], phrases: tuple[_Phrase, ...], window_max: int) -> bool:
    """Space-evasion match (PROMPT_P1_07 §0.2): a blocked phrase smuggled by
    spaces ('ك ل ب' -> 'كلب', 's a l l a' -> 'salla'). For every window of
    2..window_max adjacent tokens, join then squeeze, and compare to
    squeeze(joined phrase) by EQUALITY - never substring, so the 'زبون'/'كلبي'
    trap stays closed."""
    joined_forms = {p.joined for p in phrases if p.joined}
    if not joined_forms:
        return False
    n = len(tokens)
    for w in range(2, min(window_max, n) + 1):
        for i in range(n - w + 1):
            if squeeze("".join(tokens[i:i + w])) in joined_forms:
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


# The CLOSED rule_id list (H20/H4). size_mismatch (Task 11) is the newest
# member - category "size", evaluated only when the coordinator hands the
# SizeAdvice outcome in as a frozen SizeContext.
CLOSED_RULE_IDS: frozenset[str] = frozenset({
    "empty", "oversize", "control_chars", "placeholder",
    "profanity", "competitor", "disclosure", "verifier_error",
    "size_mismatch",
})


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
        phrases.append(
            _Phrase(tokens=tokens, squeezed=tuple(squeeze(t) for t in tokens),
                    joined=squeeze("".join(tokens)))
        )
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


# --- size_mismatch (Task 11): the advice arrives as plain frozen data (H45) ---

@dataclass(frozen=True)
class SizeContext:
    """The SizeAdvice outcome the coordinator froze for THIS reply: the
    recommended size, the acceptable alternative, and the chart's labels. Plain
    strings only - verify_rules never imports app.fit nor app.tools (guarded by
    lint-imports); a context of any other shape is a violation (fail closed)."""
    size: str | None
    alt_size: str | None
    all_labels: tuple[str, ...] = ()


# Raw -> canonical size token: the Arabic words map to their Latin letter so a
# reply in Arabic compares against a Latin label and vice versa. 2XL/3XL are
# the SAME garments as XXL/XXXL (two names, one size) and fold onto them - NOT
# onto XL (R5: XL and XXL are different sizes); 4XL/5XL are standalone labels.
_CANON_RAW = {
    "سمول": "s", "ميديوم": "m", "مديوم": "m", "لارج": "l",
    "اكسترا سمول": "xs", "اكس سمول": "xs",
    "اكسترا لارج": "xl", "اكس لارج": "xl",
    "2xl": "xxl", "3xl": "xxxl",
}
_CANON = {arabic.normalize(k): v for k, v in _CANON_RAW.items()}
# The joined form matches the pair path exactly: tokens joined WITHOUT a space,
# then squeezed - same discipline as _Phrase.joined for the blocklists.
_JOINED = {squeeze(arabic.normalize(k).replace(" ", "")): v
           for k, v in _CANON_RAW.items() if " " in k}

# The closed general vocabulary, stored in canonical form (post-fold); any
# reply token canonicalizing into this set is a size mention wherever it stands.
_SIZE_GENERAL = frozenset({"s", "m", "l", "xs", "xl", "xxl", "xxxl", "4xl", "5xl"})

# A PURE-NUMERIC label ("42") binds only within the two tokens after one of
# these size words - so "175 سم" never matches a numeric chart label. The size
# word is matched AFTER stripping the common Arabic clitics (R4): prefixes
# (و ف ب ل ال وال بال لل) and one possessive suffix (ها نا ي ك ه), else
# "بمقاس 44"/"مقاساتك 44" slip through.
_SIZE_WORDS = frozenset({"مقاس", "مقاسات", "مقاسك", "قياس", "size", "sizes"})
_SIZE_PREFIXES = ("وال", "بال", "لل", "ال", "و", "ف", "ب", "ل")
_SIZE_SUFFIXES = ("ها", "نا", "ي", "ك", "ه")


def _is_size_word(word: str) -> bool:
    """A size word after stripping Arabic clitics ("ولمقاس", "مقاساتك", ...).
    The input is already arabic.normalize output; over-stripping is harmless -
    only an exact member of _SIZE_WORDS counts."""
    w = word
    changed = True
    while changed:
        changed = False
        for prefix in _SIZE_PREFIXES:
            if w.startswith(prefix) and len(w) > len(prefix):
                w = w[len(prefix):]
                changed = True
                break
    for suffix in _SIZE_SUFFIXES:
        if w.endswith(suffix) and len(w) > len(suffix):
            w = w[: -len(suffix)]
            break
    return w in _SIZE_WORDS


def _canon(text: str) -> str:
    """Normalize (idempotent) and map the Arabic size words to their letter."""
    norm = arabic.normalize(text)
    return _CANON.get(norm, norm)


def _captured_size_tokens(tokens: list[str], labels: set[str]) -> set[str]:
    """Canonical size tokens in the reply: chart labels and the general
    vocabulary match by TOKEN EQUALITY after normalization (never substring -
    the M inside a word is not a size, H49); a pure-numeric label only within
    two tokens after a size word. A matched two-word compound ("اكسترا لارج")
    consumes its two tokens, so its head word is not ALSO read as a standalone
    mention ("لارج" -> "l")."""
    words = [t for t in tokens if t]
    word_labels = {label for label in labels if label and not label.isdigit()}
    numeric_labels = {label for label in labels if label.isdigit()}
    consumed: set[int] = set()
    captured: set[str] = set()
    for i in range(len(words) - 1):
        joined = squeeze(words[i] + words[i + 1])
        if joined in _JOINED:
            captured.add(_JOINED[joined])
            consumed.add(i)
            consumed.add(i + 1)
    for i, token in enumerate(words):
        if i in consumed:
            continue
        canon = _canon(token)
        if (canon in word_labels or canon in _SIZE_GENERAL) or (
            canon in numeric_labels and any(
                _is_size_word(words[j]) for j in (i - 1, i - 2) if j >= 0
            )
        ):
            captured.add(canon)
    return captured


def _check_size_mismatch(tokens: list[str], sc: SizeContext) -> RuleVerdict:
    """The deterministic size rule (H45): every captured size token must equal
    the advice's size or alt_size; with no advice (size is None) ANY captured
    size token violates. A malformed context is itself a violation (H47)."""
    if not isinstance(sc, SizeContext):
        return _violation("size_mismatch", "size")
    if (sc.size is not None and not isinstance(sc.size, str)) \
            or (sc.alt_size is not None and not isinstance(sc.alt_size, str)) \
            or not isinstance(sc.all_labels, tuple) \
            or any(not isinstance(label, str) for label in sc.all_labels):
        return _violation("size_mismatch", "size")
    captured = _captured_size_tokens(tokens, {_canon(label) for label in sc.all_labels})
    if not captured:
        return RuleVerdict(ok=True, rule_id=None, category=None)
    if sc.size is None:
        return _violation("size_mismatch", "size")
    allowed = {_canon(sc.size)}
    if sc.alt_size is not None:
        allowed.add(_canon(sc.alt_size))
    if captured - allowed:
        return _violation("size_mismatch", "size")
    return RuleVerdict(ok=True, rule_id=None, category=None)


def check_text(
    text: str, *, rules: BlocklistSet, max_chars: int, window_max: int = 6,
    size_context: SizeContext | None = None,
) -> RuleVerdict:
    """The single pure entry point. Structure checks first (empty -> oversize ->
    control_chars -> placeholder), then blocklists (profanity -> competitor ->
    disclosure), then the size rule ONLY when the coordinator passed the advice
    context (Task 11). The FIRST violation wins - order is fixed and written
    (H45). size_context=None keeps every legacy verdict byte-identical."""
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
        if _match_joined(tokens, phrases, window_max):
            return _violation(rule_id, "blocklist")
    if size_context is not None:
        try:
            verdict = _check_size_mismatch(tokens, size_context)
        except Exception:  # noqa: BLE001 - H47: any failure inside the size rule is a violation
            return _violation("size_mismatch", "size")
        if not verdict.ok:
            return verdict
    return RuleVerdict(ok=True, rule_id=None, category=None)


def approve(
    text: str, *, rules: BlocklistSet, max_chars: int,
    window_max: int = 6, size_context: SizeContext | None = None,
) -> VerifiedText | RuleVerdict:
    """The ONLY constructor of VerifiedText: check_text first, then approve."""
    verdict = check_text(
        text, rules=rules, max_chars=max_chars, window_max=window_max,
        size_context=size_context,
    )
    if verdict.ok:
        return VerifiedText(value=text)
    return verdict

