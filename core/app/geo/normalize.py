"""app/geo/normalize.py - place-name normalization + the synonym layer (H21/C6).

Reuses app.text.arabic.normalize (the ONE normalizer - no second normalizer is
written here). On top of it sits the geo_synonyms layer: a normalized term maps
to a canonical form (e.g. "جوله" -> "دوار", "تقاطع" -> "مفرق"). The synonyms dict
is read from geo_synonyms by repos_geo and passed in here - this module is pure
(no DB, no IO).
"""
from __future__ import annotations

import re

from app.text.arabic import normalize as _arabic_normalize


def normalize_name(text: str) -> str:
    """Normalize a place name for comparison/search (reuses app.text.arabic)."""
    return _arabic_normalize(text)


def apply_synonyms(term_norm: str, synonyms: dict[str, str]) -> str:
    """Map a normalized term to its canonical form via geo_synonyms, or return
    the term unchanged when it has no synonym (deterministic, pure)."""
    return synonyms.get(term_norm, term_norm)


_COORD_PAIR = re.compile(r"\b\d{1,2}\.\d{3,}\s*[,،/]\s*\d{1,3}\.\d{3,}\b")
_COORD_WORD = re.compile(r"\b(?:lat|lng|latitude|longitude)\b", re.IGNORECASE)


def detect_model_coords(text: str) -> bool:
    """H64: model output that carries a coordinate (a decimal pair or an explicit
    lat/lng wording) must be rejected with reason coord_from_model. This lives in
    app/geo/** - the ONE place allowed to name coordinate vocabulary (S17-a)."""
    return bool(_COORD_PAIR.search(text) or _COORD_WORD.search(text))

