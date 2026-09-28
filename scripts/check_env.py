#!/usr/bin/env python3
"""scripts/check_env.py - verify every `_required("X")` config key is bootable.

Reads core/app/config.py and core/app/workers/config.py, extracts the
environment keys the app reads through the shared `_required`/`_optional`/
`_int`/`_float`/`_bool`/`_csv`/`_json_table` helpers (AST, not text), and
reports:

  * a REQUIRED key not provided by docker-compose.yml  -> violation (the
    container would refuse to load config, H5);
  * a REQUIRED key that compose reads from the root .env via ${X:?...} but is
    NOT documented in .env.example -> violation (the operator would have no
    way to provide it, H60);
  * an OPTIONAL key missing from both -> a note only (it has a written default).

Exit 0 = bootable; exit 1 = at least one required key is not bootable. This is
the standalone mirror of the S15 static-gate stage (scripts/static_gate.py).
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "docker-compose.yml"
ENV_EXAMPLE = ROOT / ".env.example"

CONFIG_MODULES = ("core/app/config.py", "core/app/workers/config.py")
REQUIRED_HELPERS = frozenset({"_required"})
OPTIONAL_HELPERS = frozenset({"_optional", "_int", "_float", "_bool", "_csv", "_json_table"})


def _extract_keys() -> tuple[set[str], set[str]]:
    required: set[str] = set()
    optional: set[str] = set()
    for rel in CONFIG_MODULES:
        path = ROOT / rel
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                key = node.args[0].value
                if node.func.id in REQUIRED_HELPERS:
                    required.add(key)
                elif node.func.id in OPTIONAL_HELPERS:
                    optional.add(key)
    return required, optional


def _word(text: str, key: str) -> bool:
    return re.search(r"\b" + re.escape(key) + r"\b", text) is not None


def main() -> int:
    compose_text = COMPOSE.read_text(encoding="utf-8")
    env_text = ENV_EXAMPLE.read_text(encoding="utf-8")
    required, optional = _extract_keys()

    violations = 0
    print(f"required keys: {len(required)}")
    for key in sorted(required):
        if not _word(compose_text, key):
            print(f"MISS  {key:28s} not provided by docker-compose.yml (container would refuse boot)")
            violations += 1
        elif re.search(r"\$\{" + re.escape(key) + r":\?", compose_text) and not _word(env_text, key):
            print(f"MISS  {key:28s} compose reads it from .env via ${{{key}:?...}} but .env.example omits it")
            violations += 1
        else:
            print(f"OK    {key}")

    print(f"optional keys: {len(optional)}")
    missing_optional = [
        k for k in sorted(optional)
        if not _word(compose_text, k) and not _word(env_text, k)
    ]
    if missing_optional:
        print("note: optional keys absent from both compose and .env.example (they have written defaults):")
        for key in missing_optional:
            print(f"  note {key}")

    if violations:
        print(f"ENV CHECK FAILED — {violations} required key(s) not bootable.")
        return 1
    print("ENV CHECK PASSED — 0 required keys missing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
