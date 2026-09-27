"""core/app/text/arabic.py - the single Arabic text-normalization module (C6).

P1.4 (PROMPT §6.1) extracts optout.normalize() here so the search layer and the
opt-out detector share ONE normalization implementation. The behavior is
byte-for-byte the P1.1.5 normalizer (H21: no behavior change - the existing
optout tests stay green untouched).

Pipeline: trim -> casefold -> NFKD -> strip diacritics/tashkeel -> map
Arabic-Indic digits to Latin -> unify alef/yeh/taa-marbuta variants -> remove
control/Bidi/zero-width chars -> collapse whitespace -> trim.
Deterministic and pure (no IO, no network).
"""
from __future__ import annotations

import re
import unicodedata

# Arabic-Indic digits (U+0660..U+0669) and Extended Arabic-Indic (U+06F0..U+06F9)
# mapped to their Latin counterparts.
_AR_DIGITS = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)

# Tashkeel / combining marks to strip: the full Arabic combining-marks block
# U+064B..U+065F. This includes Fatha/Kasra/Damma/Shadda/Sukun AND the hamza/
# madda marks (U+0653..U+0655) that NFKD exposes when it decomposes "أ/إ/آ"
# into alef + combining mark - so alef unification actually yields a plain ا.
_DIACRITICS = "".join(
    chr(c) for c in range(0x064B, 0x0660)
) + "\u0670\u06D6\u06D7\u06D8\u06D9\u06DA\u06DB\u06DC\u06DD\u06DE\u06DF\u06E0\u06E1\u06E2\u06E3\u06E4\u06E5\u06E6\u06E7\u06E8\u06EA\u06EB\u06EC\u06ED"

# Remove control chars, Unicode Bidi/formatting marks, and zero-width spaces.
_CONTROL_BIDI = re.compile(
    "[\u0000-\u001F\u007F\u061C\u200B\u200C\u200D\u200E\u200F"
    "\u202A-\u202E\u2060-\u206F\uFEFF]"
)

# Collapse any whitespace run to a single ASCII space.
_WHITESPACE = re.compile(r"\s+")

_ALEF_VARIANTS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا"})
_YA_VARIANTS = str.maketrans({"ى": "ي", "ئ": "ي", "ي": "ي"})
_TA_MARBUTA = str.maketrans({"ة": "ه"})


def normalize(text: str) -> str:
    """Normalize Arabic text for comparison/search:
    trim -> casefold -> strip diacritics -> unify alef/yeh/taa-marbuta ->
    Arabic-Indic digits to Latin -> remove control/Bidi/zero-width -> collapse
    whitespace. Deterministic and pure (no IO, no network)."""
    text = text.strip().casefold()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if ch not in _DIACRITICS)
    text = text.translate(_AR_DIGITS)
    text = text.translate(_ALEF_VARIANTS)
    text = text.translate(_YA_VARIANTS)
    text = text.translate(_TA_MARBUTA)
    text = _CONTROL_BIDI.sub("", text)
    text = _WHITESPACE.sub(" ", text)
    return text.strip()
