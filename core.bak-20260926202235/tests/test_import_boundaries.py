"""Structural test (H2/H14-adjacent architectural discipline): no module
outside app.db may import psycopg directly - every other layer must go
through tenant_tx()/system_tx(). Runs the REAL import-linter CLI against the
REAL package graph (not a hand-rolled AST check that could drift from what
import-linter actually enforces in CI)."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent.parent
# The actual console-script entry point (a click command, not runnable via
# `python -m importlinter.cli`) - resolved relative to the running
# interpreter so this works in whatever venv pytest itself is using.
_LINT_IMPORTS = str(Path(sys.executable).parent / "lint-imports")


def test_import_linter_contracts_are_kept():
    result = subprocess.run(
        [_LINT_IMPORTS], cwd=CORE_DIR, capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        "import-linter found a boundary violation "
        f"(no module outside app.db may import psycopg directly):\n{result.stdout}\n{result.stderr}"
    )


def test_contract_actually_detects_a_real_violation(tmp_path):
    """Proves the contract has real teeth (H7: don't trust a passing gate you
    haven't seen fail) - temporarily injects a genuine direct psycopg import
    into a forbidden module (a throwaway copy of the whole core/ tree, never
    the real files) and confirms lint-imports actually rejects it."""
    scratch = tmp_path / "core"
    shutil.copytree(CORE_DIR, scratch, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    violating_file = scratch / "app" / "api" / "routes_health.py"
    violating_file.write_text(violating_file.read_text() + "\nimport psycopg\n")

    result = subprocess.run(
        [_LINT_IMPORTS], cwd=scratch, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "app.api.routes_health -> psycopg" in result.stdout
