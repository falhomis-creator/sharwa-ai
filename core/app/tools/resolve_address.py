"""app/tools/resolve_address.py - the resolve_address tool (H50: pure).

Same discipline as track_order: no DB, no network, no `.execute()`, no app.db
import. It receives PRE-READ candidates (read by the coordinator) and returns a
frozen AddressDecision through the pure app.geo.resolve layer. Coordinates never
enter this module from a model (H64).
"""
from __future__ import annotations

from app.geo import resolve


def run(
    candidates: list[resolve.AddressCandidate],
    *,
    pin: tuple[float, float] | None = None,
    pin_in_coverage: bool = True,
    model_has_coords: bool = False,
    parent_matched: tuple[int, ...] = (),
    cfg: resolve.ResolveConfig | None = None,
) -> resolve.AddressDecision:
    """The pure decision (delegates to app.geo.resolve.resolve_address)."""
    return resolve.resolve_address(
        candidates,
        pin=pin,
        pin_in_coverage=pin_in_coverage,
        model_has_coords=model_has_coords,
        parent_matched=parent_matched,
        cfg=cfg,
    )
