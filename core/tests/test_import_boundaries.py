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

import pytest

# N1 (P1.5 audit): these two tests shell out to the real `lint-imports` binary,
# which is a dev-tooling dependency (import-linter) not present in the minimal
# runtime environment. Mark the module `tools` (same pattern as `db`) so a bare
# `pytest core/tests` SKIPS them and ends green - red must always be news.
pytestmark = pytest.mark.tools

CORE_DIR = Path(__file__).resolve().parent.parent
# The actual console-script entry point (a click command, not runnable via
# `python -m importlinter.cli`) - resolved relative to the running
# interpreter so this works in whatever venv pytest itself is using.
# shutil.which resolves the Windows console script (lint-imports.exe) too; the
# bare sibling path stays as the fallback.
_LINT_IMPORTS = shutil.which("lint-imports") or str(Path(sys.executable).parent / "lint-imports")


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


def test_tools_purity_contract_detects_an_httpx_import(tmp_path):
    """Task 11 teeth: the tools-are-pure contract rejects an httpx import inside
    app/tools (a scratch-tree copy, never the real files)."""
    scratch = tmp_path / "core"
    shutil.copytree(CORE_DIR, scratch, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    tool = scratch / "app" / "tools" / "size_advise.py"
    tool.write_text(tool.read_text() + "\nimport httpx\n")

    result = subprocess.run(
        [_LINT_IMPORTS], cwd=scratch, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "app.tools.size_advise -> httpx" in result.stdout


def test_verify_rules_guard_contract_detects_an_app_fit_import(tmp_path):
    """Task 11 teeth: verify_rules must never import app.fit - the advice always
    arrives as plain data (SizeContext), checked on a scratch-tree copy."""
    scratch = tmp_path / "core"
    shutil.copytree(CORE_DIR, scratch, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    rules = scratch / "app" / "workers" / "verify_rules.py"
    rules.write_text(rules.read_text() + "\nimport app.fit.size_advisor\n")

    result = subprocess.run(
        [_LINT_IMPORTS], cwd=scratch, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "app.workers.verify_rules -> app.fit.size_advisor" in result.stdout
