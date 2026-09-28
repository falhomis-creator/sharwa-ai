"""app/geo/resolve.py - the deterministic address decision (H65/H66/H64).

Pure: receives pre-read candidates + an optional pin + the config weights and
returns a frozen AddressDecision. No IO, no DB, no model, no app.text.arabic
(S17-b closed import set). The model extracts TEXT place names only; coordinates
come from pin / geocoder / gazetteer-centroid - never from the model (H64).

Decision rules, in order (PROMPT §6):
  1. model output carries a coordinate => rejected / coord_from_model;
  2. a pin was sent => accepted / pin (or rejected / pin_outside_coverage);
  3. a single exact district-or-finer match at/above the threshold => accepted;
  4. more than one deliverable candidate => disambiguate (top-N names verbatim);
  5. a single candidate below the threshold => confirm_with_customer;
  6. zero candidates, or only governorate/country => ask_for_pin.

Confidence (H65) = w_level[level] * w_match[kind] * penalty(n) * parent_bonus,
clipped to [0,1]. Every weight has a written default in ResolveConfig.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DECISIONS = ("accepted", "confirm_with_customer", "disambiguate", "ask_for_pin", "rejected")
SOURCES = ("pin", "geocoder", "gazetteer_centroid")
REASON_CODES = (
    "pin", "pin_outside_coverage", "gazetteer_match", "ambiguous",
    "insufficient_confidence", "no_match", "coord_from_model",
)

# district or finer is "deliverable"; governorate alone is too coarse (H66).
DELIVERABLE_LEVELS = frozenset({"district", "area", "neighborhood", "landmark"})

# Written defaults (overridable via ADDRESS_CONF_* in settings).
DEFAULT_W_LEVEL = {
    "landmark": 1.00, "neighborhood": 0.95, "area": 0.90,
    "district": 0.85, "governorate": 0.50, "country": 0.30,
}
DEFAULT_W_MATCH = {"exact": 1.00, "synonym": 0.90, "prefix": 0.70}
DEFAULT_PARENT_BONUS = 1.05
DEFAULT_ACCEPT_THRESHOLD = 0.75
DEFAULT_MAX_CANDIDATES = 3


@dataclass(frozen=True)
class AddressCandidate:
    gazetteer_id: int
    level: str                       # country|governorate|district|area|neighborhood|landmark
    name_norm: str
    parent_id: int | None
    match_kind: str                  # exact | synonym | prefix (no fuzzy this batch)
    tenant_scoped: bool


@dataclass(frozen=True)
class AddressDecision:
    decision: str                    # one of DECISIONS
    confidence: float                # computed, never estimated (H65)
    source: str | None               # pin | geocoder | gazetteer_centroid
    gazetteer_id: int | None
    reason_code: str                 # closed list REASON_CODES
    candidates: tuple[str, ...] = () # gazetteer names verbatim, for disambiguate


@dataclass(frozen=True)
class ResolveConfig:
    accept_threshold: float = DEFAULT_ACCEPT_THRESHOLD
    max_candidates: int = DEFAULT_MAX_CANDIDATES
    w_level: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_W_LEVEL))
    w_match: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_W_MATCH))
    parent_bonus: float = DEFAULT_PARENT_BONUS


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def confidence_of(
    candidate: AddressCandidate,
    *,
    n_candidates: int,
    parent_matched: bool,
    cfg: ResolveConfig,
) -> float:
    """H65: a pure deterministic product of written weights, clipped to [0,1].
    Same inputs => same number, always (no randomness, no model, no clock)."""
    level_w = cfg.w_level.get(candidate.level, 0.0)
    match_w = cfg.w_match.get(candidate.match_kind, 0.0)
    penalty = 1.0 / (1.0 + 0.3 * max(0, n_candidates - 1))
    parent = cfg.parent_bonus if parent_matched else 1.0
    return _clamp01(level_w * match_w * penalty * parent)


def resolve_address(
    candidates: list[AddressCandidate],
    *,
    pin: tuple[float, float] | None = None,
    pin_in_coverage: bool = True,
    model_has_coords: bool = False,
    parent_matched: tuple[int, ...] = (),
    cfg: ResolveConfig | None = None,
) -> AddressDecision:
    """The decision table (PROMPT §6). Deterministic and pure."""
    cfg = cfg or ResolveConfig()
    if model_has_coords:
        return AddressDecision("rejected", 0.0, None, None, "coord_from_model")
    if pin is not None:
        if pin_in_coverage:
            return AddressDecision("accepted", 1.0, "pin", None, "pin")
        return AddressDecision("rejected", 0.0, None, None, "pin_outside_coverage")

    deliverable = [c for c in candidates if c.level in DELIVERABLE_LEVELS]
    if not deliverable:
        return AddressDecision("ask_for_pin", 0.0, None, None, "no_match")

    n = len(deliverable)
    pm = frozenset(parent_matched)
    scored = sorted(
        (
            (confidence_of(c, n_candidates=n, parent_matched=(c.gazetteer_id in pm or c.parent_id in pm), cfg=cfg), c)
            for c in deliverable
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )

    if n == 1:
        conf, c = scored[0]
        if conf >= cfg.accept_threshold:
            return AddressDecision("accepted", conf, "gazetteer_centroid", c.gazetteer_id, "gazetteer_match")
        return AddressDecision("confirm_with_customer", conf, None, c.gazetteer_id, "insufficient_confidence", (c.name_norm,))

    top = [c for _, c in scored[: cfg.max_candidates]]
    return AddressDecision("disambiguate", scored[0][0], None, None, "ambiguous", tuple(c.name_norm for c in top))
