"""core/tests/test_golden_addresses.py - the golden address set (P2.4 §3.2, the
missing P2 exit criterion). Loads the REAL gazetteer via seed_gazetteer, runs
every case through the real resolver, and prints a match/mismatch matrix.

The acceptable outcome is NOT 100%: a golden set that passes every case was
written to please the code. The set includes realistically hard cases (street
name overlap, colloquial synonyms, dropped alef/definite article) so some fail -
that is the number that closes the P2 exit criterion.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import pytest

from app import db as core_db
from app.db import testsupport as db_testsupport
from app.geo import resolve as geo_resolve
from app.workers import address

pytestmark = pytest.mark.db

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import seed_gazetteer  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "golden" / "addresses.jsonl"

# §1.3 (F-P2-08): the SEVEN known mismatches, pinned by their input string. A NEW
# mismatch => fail; a known mismatch that DISAPPEARS (an improvement) => warn only.
KNOWN_MISMATCHES = frozenset({
    "التحرير، صنعاء",
    "المكلا",
    "الجولة كنتاكي",
    "جولة الرياض",
    "سبعين",
    "صنعاء - شعوب",
    "تقاطع شارع حدة",
})


@pytest.fixture(scope="module")
def loaded_gazetteer():
    rc = seed_gazetteer.main([str(seed_gazetteer.DEFAULT_DATA)])
    assert rc == 0
    yield


@pytest.fixture()
def golden_tenant():
    dsn = os.environ["CORE_MIGRATION_DATABASE_URL"]
    tenant_id = db_testsupport.insert_tenant_returning_id(
        dsn, platform_ref=f"golden-{uuid.uuid4()}", name="Golden Tenant",
    )
    yield tenant_id
    db_testsupport.delete_tenant_full(dsn, tenant_id)


def _cases() -> list[dict]:
    out: list[dict] = []
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def test_golden_address_set(loaded_gazetteer, golden_tenant):
    cfg = geo_resolve.ResolveConfig()
    matches = mismatches = 0
    table: list[tuple[str, str, str]] = []

    for case in _cases():
        with core_db.tenant_tx(golden_tenant) as conn:
            d = address.resolve_only(conn, tenant_id=golden_tenant, query=case["input"], cfg=cfg)
        if d.decision == case["expected_decision"]:
            matches += 1
        else:
            mismatches += 1
            table.append((case["input"], case["expected_decision"], d.decision))

    total = matches + mismatches
    print(f"\nGOLDEN ADDRESS SET: {matches}/{total} matched, {mismatches} mismatched")
    for inp, exp, got in table:
        print(f"  MISMATCH input={inp!r} expected={exp} got={got}")

    mismatched_inputs = {inp for inp, _, _ in table}
    new_mismatches = mismatched_inputs - KNOWN_MISMATCHES
    improved = KNOWN_MISMATCHES - mismatched_inputs
    if improved:
        print(f"  IMPROVEMENT: {len(improved)} known mismatches now pass: {sorted(improved)}")

    assert total == len(_cases())
    assert not new_mismatches, f"new golden mismatches (not in the known list): {sorted(new_mismatches)}"
    assert matches >= 30, f"only {matches} matched - the resolver is too weak to close P2"
