"""core/app/tools/size_advise.py - the size_advise tool (Task 11, H50).

Pure: explicit args only (chart, height, weight, body measurements, fit
preference) and a frozen SizeAdviseResult wrapping the pure SizeAdvice verdict
from app.fit.size_advisor (docs/01_FIVE_TASKS_DESIGN.md §5.1). No DB, no
network, no `.execute()`, no model, no module state: the coordinator (a future
workers module, deliberately outside Task 11's scope) reads the chart and the
customer's numbers and owns the write side. Registered in the literal TOOLS
dict (S11-c).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.fit.size_advisor import SizeAdvice, SizeChart, advise


@dataclass(frozen=True)
class SizeAdviseResult:
    """The frozen tool result. The tool adds nothing and hides nothing: what
    advise() decided (size, alt_size, confidence, reasons, out_of_range) is
    exactly what the caller reads back through `advice`."""
    advice: SizeAdvice


def run(
    chart: SizeChart | None,
    *,
    height_cm: Decimal | None = None,
    weight_kg: Decimal | None = None,
    body_cm: dict[str, Decimal] | None = None,
    fit_pref: str = "regular",
) -> SizeAdviseResult:
    """Delegate to the pure advisor and freeze the verdict (H50)."""
    return SizeAdviseResult(advice=advise(
        chart,
        height_cm=height_cm, weight_kg=weight_kg,
        body_cm=body_cm, fit_pref=fit_pref,
    ))
