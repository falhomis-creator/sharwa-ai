"""core/app/tools/size_extract.py - deterministic height/weight extraction (H51).

Pure, no IO, no model: the model never supplies structured arguments - THIS
module extracts the customer's height and weight from the turn's texts with
fixed token rules. Text goes through app.text.arabic.normalize (H21, the single
normalizer): Arabic-Indic digits map to Latin, Latin casefolds, alef/ya/
taa-marbuta unify (so "إنش" -> "انش", "متراً" -> "مترا").

Binding rules (audit round R1-R3):
- A unit AFTER a number is THAT number's ("175cm 80kg" reads both) and is never
  borrowed as the next number's left unit; a unit BEFORE a number binds only
  when no number precedes it ("سم 175" works, "175 سم 80" gives سم to 175).
- A field word (طول/وزن and their clitic forms) binds from the LEFT only - the
  Arabic possessive "وزني 95 وطولي 175" reads both numbers correctly. A unit
  beats a field word; a height/weight conflict drops the number.
- The meter SHORTHAND (m/م) is a unit only for a NON-integer value in
  [1.00, 2.50] (a body height like 1.75 - "ابغى 2 M" is a quantity, "175 M"
  is not 175 meters); the full words (متر/مترا/مترات) are unrestricted.
- Feet followed (after an optional connector) by a trailing number with NO
  known unit of its own, or fractional feet ("5.7 قدم" = 5'7"), are ambiguous
  inch readings and contribute nothing (the customer is asked); a trailing
  number WITH its own unit is a second quantity ("6 قدم 80 كيلو"), and
  "5 قدم و7 انش" and "6 قدم" read exactly.

Exact Decimal conversions: 1 lb = 0.45359237 kg, 1 inch = 2.54 cm,
1 foot = 30.48 cm, 1 m = 100 cm. Two different values for one field make it
None (the customer is asked); a leading "و" ("ووزني") is transparent for
keyword lookup. Plausibility bounds are NOT checked here - they stay in
size_advisor.advise (§5.1: implausible_input).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from app.text import arabic

LB_TO_KG = Decimal("0.45359237")
IN_TO_CM = Decimal("2.54")
FT_TO_CM = Decimal("30.48")
M_TO_CM = Decimal("100")

# R2/R7: the m/م shorthand reads as meters only for a NON-integer body-height
# value in [1.00, 2.50] (e.g. 1.75) - "ابغى 2 M" is a quantity, never 2 meters.
_M_SHORT_MIN = Decimal("1.00")
_M_SHORT_MAX = Decimal("2.50")

_FIELD_DEFAULT_CONV: dict[str, Decimal] = {"height": Decimal("1"), "weight": Decimal("1")}


@dataclass(frozen=True)
class SizeInputs:
    """The extracted inputs. None / absent means "not stated, contradictory or
    ambiguous" - the customer is asked, never guessed at (H52). body_cm holds
    (dimension, cm) pairs sorted by dimension name (chest / hips / waist)."""
    height_cm: Decimal | None
    weight_kg: Decimal | None
    fit_pref: str | None = None
    body_cm: tuple[tuple[str, Decimal], ...] = ()


def _normed(words: tuple[str, ...]) -> frozenset[str]:
    """The vocabulary stored exactly as arabic.normalize stores text."""
    return frozenset(arabic.normalize(w) for w in words)


_HEIGHT_FIELDS = _normed(("طول", "طولي", "طولك", "طوله", "طولها", "الطول"))
_WEIGHT_FIELDS = _normed(("وزن", "وزني", "وزنك", "وزنه", "وزنها", "الوزن"))
_CM_UNITS = _normed(("سم", "سنتيمتر", "سنتيمترات", "سانتي", "cm"))
_M_UNITS = _normed(("متر", "مترا", "مترات"))
_M_SHORT_UNITS = _normed(("م", "m"))
_FT_UNITS = _normed(("قدم", "اقدام", "ft", "feet", "foot"))
_IN_UNITS = _normed(("انش", "بوصه", "بوصات", "inch", "inches"))
_KG_UNITS = _normed(("كيلو", "كيلوجرام", "كيلوغرام", "كجم", "كغ", "kg"))
_LB_UNITS = _normed(("رطل", "رطلا", "باوند", "باوندات", "pound", "pounds", "lb", "lbs"))
_CONNECTORS = _normed(("و", "and"))

# Task 18b-1 (OQ-P4-14): body measurements - a field word immediately LEFT of
# the number; unit cm (default) or inch; any other unit drops the number.
_BODY_FIELDS: dict[str, frozenset[str]] = {
    "chest": _normed(("صدر", "صدري", "الصدر", "chest")),
    "waist": _normed(("خصر", "خصري", "الخصر", "waist")),
    "hips": _normed(("ورك", "وركي", "الورك", "اوراك", "الاوراك", "hip", "hips")),
}
# Fit preference: a closed vocabulary; a negation within the two tokens before a
# preference word, or two different preferences, make the preference unknown.
_FIT_WORDS: dict[str, frozenset[str]] = {
    "fitted": _normed(("ضيق", "ضيقه", "الضيق", "محكم", "fitted", "tight")),
    "regular": _normed(("عادي", "عاديه", "العادي", "regular", "normal")),
    "loose": _normed(("واسع", "واسعه", "الواسع", "فضفاض", "فضفاضه", "loose", "baggy")),
}
_NEGATIONS = _normed(("ما", "لا", "مو", "مش", "مب", "not", "no", "dont"))

# A number: digits with one optional decimal separator ('.' or the Arabic
# decimal separator U+066B), or a Latin-comma decimal "1,75" / "80,5" (OQ-P4-14:
# 1-2 digits, comma, 1-2 digits, no digit after - so "1,750" is NOT 1.750).
# A word: any digit-free run (punctuation is end-stripped below, like the
# verifier's token discipline).
_TOKEN_RE = re.compile(r"\d{1,2},\d{1,2}(?!\d)|\d+(?:[.٫]\d+)?|[^\s\d]+")
_NUMBER_FULL_RE = re.compile(r"\d{1,2},\d{1,2}|\d+(?:[.٫]\d+)?")
_END_STRIP_RE = re.compile(r"^[\W_]+|[\W_]+$")


def _is_number(token: str) -> bool:
    return _NUMBER_FULL_RE.fullmatch(token) is not None


def _number_value(token: str) -> Decimal:
    return Decimal(token.replace("٫", ".").replace(",", "."))


def _is_meter_shorthand_value(value: Decimal) -> bool:
    """R7: m/م mean meters only for a NON-integer value in [1.00, 2.50] (a body
    height like 1.75) - integers are order quantities, never meters."""
    return _M_SHORT_MIN <= value <= _M_SHORT_MAX and value != value.to_integral_value()


def _vocab_hit(word: str, vocab: frozenset[str]) -> bool:
    """Keyword lookup; a leading waw ("ووزني") is transparent."""
    return word in vocab or (word[:1] == "و" and word[1:] in vocab)


def _unit(word: str) -> tuple[str, Decimal, bool] | None:
    """(field, to-cm/kg factor, is-meter-shorthand) for a unit word, or None."""
    if _vocab_hit(word, _CM_UNITS):
        return ("height", Decimal("1"), False)
    if _vocab_hit(word, _M_UNITS):
        return ("height", M_TO_CM, False)
    if _vocab_hit(word, _M_SHORT_UNITS):
        return ("height", M_TO_CM, True)
    if _vocab_hit(word, _FT_UNITS):
        return ("height", FT_TO_CM, False)
    if _vocab_hit(word, _IN_UNITS):
        return ("height", IN_TO_CM, False)
    if _vocab_hit(word, _KG_UNITS):
        return ("weight", Decimal("1"), False)
    if _vocab_hit(word, _LB_UNITS):
        return ("weight", LB_TO_KG, False)
    return None


def _field(word: str) -> str | None:
    if _vocab_hit(word, _HEIGHT_FIELDS):
        return "height"
    if _vocab_hit(word, _WEIGHT_FIELDS):
        return "weight"
    return None


def _keyword_at(tokens: list[str], i: int, value: Decimal) -> tuple[str, Decimal] | None:
    """The single binding for the number at index i. A unit AFTER the number is
    that number's and is never re-used as the next number's left unit (R1); a
    unit BEFORE the number binds only when no number precedes it; a field word
    binds only from the left; a unit beats a field word; the meter shorthand
    counts only for a non-integer value in [1.00, 2.50] (R2/R7); two different
    units (or a height/weight conflict) drop the number."""
    n = len(tokens)
    left = tokens[i - 1] if i >= 1 and not _is_number(tokens[i - 1]) else None
    right = tokens[i + 1] if i + 1 < n and not _is_number(tokens[i + 1]) else None

    unit: tuple[str, Decimal] | None = None
    for is_right, word in ((True, right), (False, left)):
        if word is None:
            continue
        if not is_right and i >= 2 and _is_number(tokens[i - 2]):
            continue  # R1: that unit already belongs to the previous number
        found = _unit(word)
        if found is None:
            continue
        field, conv, short = found
        if short and not _is_meter_shorthand_value(value):
            continue  # R2/R7: m/م is not a meter at this magnitude
        if unit is not None and (field, conv) != unit:
            return None
        unit = (field, conv)
    if unit is not None:
        return unit
    if left is not None:
        left_field = _field(left)
        if left_field is not None:
            return (left_field, _FIELD_DEFAULT_CONV[left_field])
    return None


def _compound_ft_in(tokens: list[str], i: int) -> tuple[Decimal, Decimal, int] | None:
    """5 قدم و7 انش / 5 feet 7 inches: a feet number whose unit is immediately
    adjacent, followed within two tokens (one connector skipped) by an inches
    number. Returns (feet, inches, index after the compound) so the inches
    number is consumed once, never re-read as a second height."""
    n = len(tokens)
    if i + 1 >= n or not _vocab_hit(tokens[i + 1], _FT_UNITS):
        return None
    j = i + 2
    if j < n and _vocab_hit(tokens[j], _CONNECTORS):
        j += 1
    if j + 1 < n and _is_number(tokens[j]) and _vocab_hit(tokens[j + 1], _IN_UNITS):
        return _number_value(tokens[i]), _number_value(tokens[j]), j + 2
    return None


def _has_own_unit(tokens: list[str], j: int) -> bool:
    """R8: the number at j is followed by a unit that genuinely applies to it;
    the m/M shorthand counts only when the value passes the shorthand gate."""
    if j + 1 >= len(tokens):
        return False
    found = _unit(tokens[j + 1])
    if found is None:
        return False
    return not (found[2] and not _is_meter_shorthand_value(_number_value(tokens[j])))


def _ambiguous_feet(tokens: list[str], i: int, value: Decimal) -> bool:
    """R3: a feet number that is fractional ("5.7 قدم" = 5'7"), or that is
    followed (after an optional connector) by a trailing number with no known
    unit of its own ("5 قدم و7") - the inches reading is the likely intent, so
    the feet value must not be read silently."""
    n = len(tokens)
    if i + 1 >= n or not _vocab_hit(tokens[i + 1], _FT_UNITS):
        return False
    if value != value.to_integral_value():
        return True
    j = i + 2
    if j < n and _vocab_hit(tokens[j], _CONNECTORS):
        j += 1
    if j < n and _is_number(tokens[j]):
        # R6: the trailing number is a bare inches reading ONLY when it has no
        # known unit of its own - "6 قدم 80 كيلو"/"6 feet 180 lbs" are two
        # quantities, not a silent feet+inches guess.
        # R8: the m/M shorthand is a unit only if THIS number passes its gate.
        return not _has_own_unit(tokens, j)
    return False


def _compound_m_cm(tokens: list[str], i: int) -> tuple[Decimal, int] | None:
    """"متر و75" / "1 متر و75" / "1 متر و 75 سم" = 1 m + N cm (OQ-P4-14): the
    meter word (alone, or after the number 1), a connector, then an INTEGER
    0 < N < 100 with no unit or a cm unit. Returns (height_cm, index after)."""
    n = len(tokens)
    if _is_number(tokens[i]):
        if _number_value(tokens[i]) != 1 or i + 1 >= n or not _vocab_hit(tokens[i + 1], _M_UNITS):
            return None
        j = i + 2
    else:
        if not _vocab_hit(tokens[i], _M_UNITS) or (i >= 1 and _is_number(tokens[i - 1])):
            return None
        j = i + 1
    if j >= n or not _vocab_hit(tokens[j], _CONNECTORS):
        return None
    j += 1
    if j >= n or not _is_number(tokens[j]):
        return None
    cm = _number_value(tokens[j])
    if cm != cm.to_integral_value() or not (0 < cm < 100):
        return None
    end = j + 1
    if end < n and _unit(tokens[end]) is not None:
        if not _vocab_hit(tokens[end], _CM_UNITS):
            return None
        end += 1
    return M_TO_CM + cm, end


def _body_claims(tokens: list[str]) -> dict[int, tuple[str, Decimal | None]]:
    """Index of every number a body field word claims -> (dimension, cm or None).
    None = the number is claimed (never read as height/weight) but unusable
    (a non-length unit after it)."""
    claims: dict[int, tuple[str, Decimal | None]] = {}
    for i in range(len(tokens) - 1):
        dim = next((d for d, vocab in _BODY_FIELDS.items() if _vocab_hit(tokens[i], vocab)), None)
        if dim is None or not _is_number(tokens[i + 1]):
            continue
        value = _number_value(tokens[i + 1])
        after = _unit(tokens[i + 2]) if i + 2 < len(tokens) else None
        if after is None or _vocab_hit(tokens[i + 2], _CM_UNITS):
            claims[i + 1] = (dim, value)
        elif _vocab_hit(tokens[i + 2], _IN_UNITS):
            claims[i + 1] = (dim, value * IN_TO_CM)
        else:
            claims[i + 1] = (dim, None)
    return claims


def _fit_prefs(tokens: list[str]) -> set[str | None]:
    """Every preference stated in the text; a negated one contributes None."""
    found: set[str | None] = set()
    for i, token in enumerate(tokens):
        pref = next((p for p, vocab in _FIT_WORDS.items() if _vocab_hit(token, vocab)), None)
        if pref is None:
            continue
        negated = any(_vocab_hit(tokens[k], _NEGATIONS) for k in (i - 1, i - 2) if k >= 0)
        found.add(None if negated else pref)
    return found


def _scan_tokens(
    tokens: list[str], heights: list[Decimal], weights: list[Decimal],
    claimed: frozenset[int] = frozenset(),
) -> None:
    i = 0
    while i < len(tokens):
        meter = _compound_m_cm(tokens, i)
        if meter is not None:
            heights.append(meter[0])
            i = meter[1]
            continue
        if not _is_number(tokens[i]) or i in claimed:
            i += 1
            continue
        compound = _compound_ft_in(tokens, i)
        if compound is not None:
            feet, inches, end = compound
            heights.append(feet * FT_TO_CM + inches * IN_TO_CM)
            i = end
            continue
        value = _number_value(tokens[i])
        if _ambiguous_feet(tokens, i, value):
            i += 1
            continue
        hit = _keyword_at(tokens, i, value)
        if hit is not None:
            field, conv = hit
            (heights if field == "height" else weights).append(value * conv)
        i += 1


def _tokens(text: str) -> list[str]:
    stripped = (_END_STRIP_RE.sub("", t) for t in _TOKEN_RE.findall(arabic.normalize(text)))
    return [t for t in stripped if t]


def _resolve(values: list[Decimal]) -> Decimal | None:
    """Exactly one distinct value survives; two different values for the same
    field are contradictory => None (the customer is asked)."""
    distinct = set(values)
    if len(distinct) == 1:
        return next(iter(distinct))
    return None


def extract_size_inputs(texts: tuple[str, ...]) -> SizeInputs:
    """The deterministic extraction over one turn's texts (H51): height, weight,
    fit preference and body measurements. Values accumulate ACROSS the turn's
    texts; identical repeats stay one value; contradictions become unknown."""
    heights: list[Decimal] = []
    weights: list[Decimal] = []
    body: dict[str, list[Decimal | None]] = {}
    prefs: set[str | None] = set()
    for text in texts:
        tokens = _tokens(text)
        claims = _body_claims(tokens)
        for dim, value in claims.values():
            body.setdefault(dim, []).append(value)
        prefs |= _fit_prefs(tokens)
        _scan_tokens(tokens, heights, weights, frozenset(claims))
    body_cm = tuple(
        (dim, values[0]) for dim, values in sorted(body.items())
        if None not in values and len(set(values)) == 1 and values[0] is not None
    )
    fit_pref = next(iter(prefs)) if len(prefs) == 1 else None
    return SizeInputs(
        height_cm=_resolve(heights), weight_kg=_resolve(weights),
        fit_pref=fit_pref, body_cm=body_cm,
    )
