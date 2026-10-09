"""P4 Task 11: the size_advise tool (H50) - pure delegation, frozen result.

The tool adds nothing and hides nothing: what advise() decides is what the
caller reads back. Two calls share no state, and the result (and the advice
inside it) are frozen dataclasses.
"""
from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

from app.fit.size_advisor import SizeChart, SizeRow, advise
from app.tools import size_advise
from app.tools.registry import TOOLS

D = Decimal


def _r(lo: str, hi: str) -> tuple[Decimal, Decimal]:
    return (D(lo), D(hi))


S = SizeRow("S", 1, height_cm=_r("160", "170"), weight_kg=_r("50", "62"))
M = SizeRow("M", 2, height_cm=_r("165", "178"), weight_kg=_r("60", "75"))
CHART = SizeChart(rows=(S, M))

# H50, in-suite mirror of static_gate S11 + the new tools-are-pure contract:
# no forbidden import and no SQL call anywhere under app/tools (AST-based, so a
# docstring MENTIONING .execute() is not a false positive).
_FORBIDDEN_ROOTS = ("app.db", "psycopg", "httpx", "redis", "app.llm", "app.channels", "app.workers")
_SQL_ATTRS = frozenset({"execute", "executemany", "fetchone", "fetchall", "cursor"})


def test_returns_exactly_what_advise_returns():
    calls = (
        {"height_cm": D("168"), "weight_kg": D("55")},
        {"height_cm": D("172"), "weight_kg": D("70")},
        {"height_cm": D("172"), "weight_kg": D("70"), "body_cm": {"chest": D("96")}},
        {"height_cm": D("500"), "weight_kg": D("70")},  # implausible - advise refuses, not the tool
    )
    for kw in calls:
        assert size_advise.run(CHART, **kw).advice == advise(CHART, **kw)


def test_no_shared_state_between_calls():
    first = size_advise.run(CHART, height_cm=D("168"), weight_kg=D("55"))
    empty = size_advise.run(None)
    again = size_advise.run(CHART, height_cm=D("168"), weight_kg=D("55"))
    assert first.advice == again.advice
    assert first.advice != empty.advice
    assert empty.advice.size is None and empty.advice.reasons == ("no_chart",)


def test_result_is_frozen():
    result = size_advise.run(CHART, height_cm=D("168"), weight_kg=D("55"))
    with pytest.raises(FrozenInstanceError):
        result.advice = advise(CHART, height_cm=D("172"), weight_kg=D("70"))  # type: ignore[misc]


def test_registered_in_the_literal_tools_dict():
    assert "size_advise" in TOOLS
    assert TOOLS["size_advise"].run is size_advise.run


def test_tools_sources_have_no_forbidden_import_or_execute():
    root = Path(__file__).resolve().parent.parent / "app" / "tools"
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                targets = [node.module] if node.module else []
            else:
                targets = []
            for target in targets:
                for forbidden in _FORBIDDEN_ROOTS:
                    assert target != forbidden and not target.startswith(forbidden + "."), \
                        f"{path.name} imports {target} (H50)"
            if isinstance(node, ast.Attribute) and node.attr in _SQL_ATTRS:
                pytest.fail(f"{path.name} calls .{node.attr} (H50: no SQL in the tools layer)")
