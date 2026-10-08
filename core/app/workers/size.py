"""core/app/workers/size.py - the size-advice coordinator (P4 Task 18b-2b).

Deterministic end to end (H45/H51): a code rule decides that a turn is a size
question (owner decision 2026-10-08 - no router intent, no model; narrowed by
F-P4-10 to plausible body measurements only); the product
is the ONE product the customer was last shown (slots.last_shown_product_ids,
written by code - F-P4-07); the chart is read under RLS (repos_size); the inputs
are extracted by size_extract; the size is computed by the size_advise tool.
The reply is composed by compose.compose_size_reply and checked by the Verifier
with the advice's own labels. Runs on the CALLER's open connection (inside the
turn write-phase transaction) - DB reads only, no network.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.db import repos_size, repos_stock
from app.fit.size_advisor import (
    HEIGHT_CM_MAX,
    HEIGHT_CM_MIN,
    WEIGHT_KG_MAX,
    WEIGHT_KG_MIN,
    SizeAdvice,
)
from app.tools import size_advise
from app.tools.size_extract import extract_size_inputs

# F-P4-10: the body-measurement window (cm) the advisor itself accepts.
_BODY_CM_MIN, _BODY_CM_MAX = Decimal("30"), Decimal("250")


def is_size_question(bodies: tuple[str, ...]) -> bool:
    """The code rule (no model). F-P4-10: a turn is a size question ONLY when it
    carries at least one PLAUSIBLE body measurement (height 100-230 cm, weight
    30-250 kg, or chest / waist / hips 30-250 cm - the advisor's own bounds). The
    word «مقاس» alone never qualifies: «منتج قميص مقاس L» is a product search
    and stays with the router."""
    inputs = extract_size_inputs(bodies)
    if inputs.height_cm is not None and HEIGHT_CM_MIN <= inputs.height_cm <= HEIGHT_CM_MAX:
        return True
    if inputs.weight_kg is not None and WEIGHT_KG_MIN <= inputs.weight_kg <= WEIGHT_KG_MAX:
        return True
    return any(_BODY_CM_MIN <= cm <= _BODY_CM_MAX for _dim, cm in inputs.body_cm)


@dataclass(frozen=True)
class SizeTurn:
    """advice None => no single shown product (the turn hands off). labels are
    the chart's labels, passed to the Verifier's SizeContext."""
    advice: SizeAdvice | None
    labels: tuple[str, ...]


def advise_for_turn(
    conn: Any, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID, bodies: tuple[str, ...],
) -> SizeTurn:
    slots = repos_stock.read_conversation_slots(conn, conversation_id=conversation_id)
    shown = [str(x) for x in (slots.get("last_shown_product_ids") or [])]
    if len(shown) != 1:
        # Zero or several products on screen: never guess which one (H52).
        return SizeTurn(None, ())
    try:
        chart = repos_size.read_size_chart(conn, platform_product_id=shown[0])
    except repos_size.SizeChartDataError:
        # H47: a malformed stored chart is invalid_chart (fail closed), never a guess.
        return SizeTurn(SizeAdvice(None, None, "low", ("invalid_chart",), False), ())
    inputs = extract_size_inputs(bodies)
    advice = size_advise.run(
        chart,
        height_cm=inputs.height_cm,
        weight_kg=inputs.weight_kg,
        body_cm=dict(inputs.body_cm) or None,
        fit_pref=inputs.fit_pref or "regular",
    ).advice
    labels = tuple(r.label for r in chart.rows) if chart is not None else ()
    return SizeTurn(advice, labels)
