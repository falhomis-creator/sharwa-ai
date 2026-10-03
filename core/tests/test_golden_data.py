"""Pure validation of the P2.4 data files (no DB): the golden address set and the
gazetteer data file must be present, well-formed, and cover every decision. These
run in the pure suite (H75: a missing/empty data file must never go unnoticed)."""
from __future__ import annotations

import json
from pathlib import Path

from app.geo.resolve import DECISIONS

ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN = Path(__file__).resolve().parent / "golden" / "addresses.jsonl"
SEED_DATA = ROOT / "data" / "geo" / "yemen_admin.json"


def _golden_cases() -> list[dict]:
    return [
        json.loads(line)
        for line in GOLDEN.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_golden_set_has_at_least_40_cases():
    assert len(_golden_cases()) >= 40


def test_golden_set_expected_decisions_are_closed():
    for case in _golden_cases():
        assert case["expected_decision"] in DECISIONS, case


def test_golden_set_covers_every_decision_category():
    decisions = {c["expected_decision"] for c in _golden_cases()}
    assert {"accepted", "ask_for_pin", "disambiguate", "rejected"} <= decisions


def test_seed_data_has_22_governorates():
    data = json.loads(SEED_DATA.read_text(encoding="utf-8"))
    governorates = [r for r in data["rows"] if r["level"] == "governorate"]
    assert len(governorates) == 22


def test_seed_data_levels_are_closed():
    data = json.loads(SEED_DATA.read_text(encoding="utf-8"))
    levels = {r["level"] for r in data["rows"]}
    assert levels <= set(("country", "governorate", "district", "area", "neighborhood", "landmark"))
