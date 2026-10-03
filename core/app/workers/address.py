"""app/workers/address.py - the address-resolution coordinator (P2.2, H64/H68).

Read (gazetteer candidates) -> decide (pure app.geo.resolve) -> write
(address_resolutions). The reply is composed in turn.py (compose_address_options),
never here. Coordinates come from pin / geocoder / gazetteer-centroid - never
from the model (H64); the model's place names are normalized here and matched
against the gazetteer.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from app.db import repos_geo
from app.geo import normalize as geo_normalize
from app.geo import resolve as geo_resolve
from app.obs import metrics
from app.workers.config import WorkerSettings


def build_candidates(
    places: list[str],
    synonyms: dict[str, str],
    gazetteer_rows: list[dict[str, Any]],
) -> list[geo_resolve.AddressCandidate]:
    """Pure: normalize each model place, apply the synonym layer, and pair with
    gazetteer rows into AddressCandidate (match_kind exact | synonym; no fuzzy
    matching this batch)."""
    candidates: list[geo_resolve.AddressCandidate] = []
    seen: set[int] = set()
    for place in places:
        norm = geo_normalize.normalize_name(place)
        canon = geo_normalize.apply_synonyms(norm, synonyms)
        for row in gazetteer_rows:
            if row["name_norm"] != canon:
                continue
            gid = int(row["gazetteer_id"])
            if gid in seen:
                continue
            seen.add(gid)
            match_kind = "exact" if norm == canon else "synonym"
            candidates.append(geo_resolve.AddressCandidate(
                gazetteer_id=gid, level=row["level"], name_norm=row["name_norm"],
                parent_id=row["parent_id"], match_kind=match_kind,
                tenant_scoped=bool(row["tenant_scoped"]),
                lat=row.get("lat"), lng=row.get("lng"),
            ))
    return candidates


def resolve_address(
    settings: WorkerSettings,
    *,
    candidates: list[geo_resolve.AddressCandidate],
    pin: tuple[float, float] | None = None,
    pin_in_coverage: bool = True,
    model_has_coords: bool = False,
    parent_matched: tuple[int, ...] = (),
) -> geo_resolve.AddressDecision:
    """The pure decision with the worker's config weights (H65)."""
    cfg = geo_resolve.ResolveConfig(
        accept_threshold=settings.address_accept_threshold,
        max_candidates=settings.address_max_candidates,
        w_level=settings.address_w_level,
        w_match=settings.address_w_match,
        parent_bonus=settings.address_parent_bonus,
    )
    return geo_resolve.resolve_address(
        candidates, pin=pin, pin_in_coverage=pin_in_coverage,
        model_has_coords=model_has_coords, parent_matched=parent_matched, cfg=cfg,
    )


def persist_decision(
    conn: Any,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    input: dict[str, Any],
    candidates: list[dict[str, Any]],
    decision: geo_resolve.AddressDecision,
    location: tuple[float, float] | None,
) -> uuid.UUID:
    """The one write to address_resolutions. `location` is (lat, lng) only when
    the decision is accepted; any other decision writes NULL (and the SQL CHECK
    would reject an accepted row with no point - H64)."""
    metrics.address_resolutions_total.labels(decision.decision).inc()
    return repos_geo.insert_address_resolution(
        conn,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        input=input,
        candidates=candidates,
        decision=decision.decision,
        confidence=decision.confidence,
        source=decision.source,
        location=location if decision.decision == "accepted" else None,
    )


def split_places(query: str) -> list[str]:
    """Split the model's place-name query on the separators a customer writes."""
    return [p.strip() for p in re.split(r"[,،/]+", query) if p.strip()]


def resolve_and_persist(
    conn: Any,
    settings: WorkerSettings,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    query: str,
    pin: tuple[float, float] | None = None,
    pin_in_coverage: bool = True,
) -> geo_resolve.AddressDecision:
    """Read (gazetteer) -> decide (pure) -> write (address_resolutions). No
    external geocoder this batch (OQ-P2-05) - source is pin or gazetteer_centroid
    only. Coordinates never come from the model (H64)."""
    places = split_places(query)
    synonyms = repos_geo.fetch_synonyms(conn)
    rows: list[dict[str, Any]] = []
    for term in {geo_normalize.normalize_name(p) for p in places}:
        rows.extend(repos_geo.search_gazetteer(
            conn, tenant_id=tenant_id, name_norm=term,
            limit=settings.address_max_candidates,
        ))
    candidates = build_candidates(places, synonyms, rows)
    decision = resolve_address(
        settings, candidates=candidates, pin=pin, pin_in_coverage=pin_in_coverage,
        model_has_coords=geo_normalize.detect_model_coords(query),
    )
    location = _location_for(decision, rows, pin)
    persist_decision(
        conn, tenant_id=tenant_id, conversation_id=conversation_id,
        input={"query": query}, candidates=rows, decision=decision,
        location=location,
    )
    return decision


def _location_for(
    decision: geo_resolve.AddressDecision,
    rows: list[dict[str, Any]],
    pin: tuple[float, float] | None,
) -> tuple[float, float] | None:
    """The (lat, lng) to write for an accepted decision (F-P2-04). A pin is the
    customer's own point; a `gazetteer_centroid` acceptance carries the matched
    row's computed centroid - never None, so the `accepted ⇒ location IS NOT
    NULL` CHECK is satisfied from either source."""
    if decision.decision != "accepted":
        return None
    if decision.source == "pin":
        return pin
    gid = decision.gazetteer_id
    if gid is None:
        return None
    for row in rows:
        if int(row.get("gazetteer_id")) == gid and row.get("lat") is not None and row.get("lng") is not None:
            return (float(row["lat"]), float(row["lng"]))
    return None
