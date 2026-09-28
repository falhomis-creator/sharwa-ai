#!/usr/bin/env python3
"""scripts/generate_console_lock.py - regenerate docs/console_api.lock.json from
docs/CONSOLE_API_CONTRACT.md (the single frozen source of truth, H56).

S13 in static_gate.py compares the actual `@router.<method>` routes in app/api/**
against this lock file, so the contract is a rule, not an intention. Re-run this
script whenever the contract's route table changes.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "docs" / "CONSOLE_API_CONTRACT.md"
LOCK = ROOT / "docs" / "console_api.lock.json"

# Route-table rows: | METHOD | /path | permission | (the leading 3 columns of the
# §1 route table). The header row's first cell is "Method", not an HTTP method,
# so it is skipped by the startswith("/") path check.
_ROW = re.compile(r"^\|\s*([A-Z]+)\s*\|\s*([^|\s][^|]*?)\s*\|\s*([^|\s][^|]*?)\s*\|")


def main() -> int:
    text = CONTRACT.read_text(encoding="utf-8")
    routes: list[dict[str, str]] = []
    for line in text.splitlines():
        m = _ROW.match(line.strip())
        if not m:
            continue
        method, path, perm = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        path = path.strip("`").strip()
        perm = perm.strip("`").strip()
        if not path.startswith("/"):
            continue  # header / non-route row
        routes.append({"method": method, "path": path, "permission": perm})
    routes.sort(key=lambda r: (r["method"], r["path"], r["permission"]))
    LOCK.write_text(json.dumps({"routes": routes}, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(routes)} routes to {LOCK.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
