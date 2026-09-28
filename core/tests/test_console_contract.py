"""Contract-freeze tests (P1.8 §4/§8, H56): the frozen contract lock matches the
actual code, and the error-code table matches ERROR_CODES bidirectionally."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import static_gate  # noqa: E402
from app.api.errors import ERROR_CODES  # noqa: E402

LOCK = ROOT / "docs" / "console_api.lock.json"
CONTRACT = ROOT / "docs" / "CONSOLE_API_CONTRACT.md"


def test_actual_routes_equal_contract_lock():
    actual = static_gate._console_routes(static_gate._module_files())
    locked = {
        (r["method"], r["path"], r["permission"])
        for r in json.loads(LOCK.read_text(encoding="utf-8"))["routes"]
    }
    assert actual == locked


def test_error_code_table_matches_error_codes():
    text = CONTRACT.read_text(encoding="utf-8")
    contract_codes = set(re.findall(r"^\|\s*`([A-Z_]+)`\s*\|\s*\d+\s*\|", text, flags=re.M))
    assert contract_codes == set(ERROR_CODES)