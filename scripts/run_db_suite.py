#!/usr/bin/env python3
"""scripts/run_db_suite.py - run the db-marked suite twice consecutively (P3.1 §1.5).

One command for the owner to run when the implementer's environment lacks a real
database (the constraint has repeated for four phases). It checks the three
CORE_*_DATABASE_URL vars and that the database answers BEFORE starting, then runs
`pytest tests -q -m db` twice and prints the last line of each run verbatim.
Exits 1 if the two numbers differ or either run errored - a differing number means
the tests poison each other (the exact F-P2-06 defect).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "core"

_REQUIRED = ("CORE_DATABASE_URL", "CORE_SYSTEM_DATABASE_URL", "CORE_MIGRATION_DATABASE_URL")

# N-5: the db-marked suite includes Redis-dependent tests. It needs a Redis
# instance on 127.0.0.1:6390 with password `test-redis-pw` (the test container's
# docker-compose.test.yml). Without it a chunk of the suite fails, not the code.
_REDIS_NOTE = (
    "the db suite also needs Redis on 127.0.0.1:6390 with password 'test-redis-pw'"
)


def _last_line(out: str) -> str:
    lines = [l for l in out.splitlines() if l.strip()]
    return lines[-1] if lines else ""


def main() -> int:
    for name in _REQUIRED:
        if not os.environ.get(name):
            print(f"DB SUITE: NOT RUN (missing {name})", file=sys.stderr)
            print(_REDIS_NOTE, file=sys.stderr)
            return 2

    try:
        with psycopg.connect(os.environ["CORE_MIGRATION_DATABASE_URL"], connect_timeout=5) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001 - the reason is printed, never a fake number
        print(f"DB SUITE: NOT RUN (database unreachable: {exc})", file=sys.stderr)
        print(_REDIS_NOTE, file=sys.stderr)
        return 2

    results: list[tuple[int, str]] = []
    for run in (1, 2):
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "-q", "-m", "db"],
            cwd=str(CORE), capture_output=True, text=True,
        )
        tail = _last_line(proc.stdout) or _last_line(proc.stderr)
        print(f"[run {run}] {tail}")
        results.append((proc.returncode, tail))

    (rc1, t1), (rc2, t2) = results
    num1 = re.search(r"(\d+) passed", t1)
    num2 = re.search(r"(\d+) passed", t2)
    if rc1 != 0 or rc2 != 0:
        print("DB SUITE: FAILED (a run errored)", file=sys.stderr)
        return 1
    if num1 is None or num2 is None or num1.group(1) != num2.group(1):
        print(f"DB SUITE: UNSTABLE ({num1 and num1.group(1)} vs {num2 and num2.group(1)})", file=sys.stderr)
        return 1
    print(f"DB SUITE: STABLE ({num1.group(1)} passed twice)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
