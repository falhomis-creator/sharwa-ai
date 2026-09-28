"""Pure address-worker write-path tests (P2.2 §9) with a recording double."""
from __future__ import annotations

import uuid

from app.geo import resolve as geo_resolve
from app.workers import address


def _decision(**kw):
    defaults = dict(
        decision="accepted", confidence=1.0, source="pin",
        gazetteer_id=None, reason_code="pin",
    )
    defaults.update(kw)
    return geo_resolve.AddressDecision(**defaults)


def _patch(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        address.repos_geo, "insert_address_resolution",
        lambda conn, **kw: (calls.append(kw) or uuid.uuid4()),
    )
    return calls


def test_accepted_writes_one_row_with_location(monkeypatch):
    calls = _patch(monkeypatch)
    address.persist_decision(
        None, tenant_id=uuid.uuid4(), conversation_id=uuid.uuid4(),
        input={}, candidates=[], decision=_decision(), location=(15.3, 44.2),
    )
    assert len(calls) == 1
    assert calls[0]["location"] == (15.3, 44.2)
    assert calls[0]["decision"] == "accepted"


def test_non_accepted_writes_null_location(monkeypatch):
    calls = _patch(monkeypatch)
    decision = _decision(decision="ask_for_pin", source=None, confidence=0.0, reason_code="no_match")
    address.persist_decision(
        None, tenant_id=uuid.uuid4(), conversation_id=uuid.uuid4(),
        input={}, candidates=[], decision=decision, location=(15.3, 44.2),
    )
    assert len(calls) == 1
    assert calls[0]["location"] is None  # even though a location was passed in


def test_tenant_id_is_set_not_none(monkeypatch):
    calls = _patch(monkeypatch)
    tid = uuid.uuid4()
    address.persist_decision(
        None, tenant_id=tid, conversation_id=uuid.uuid4(),
        input={}, candidates=[], decision=_decision(), location=(15.3, 44.2),
    )
    assert calls[0]["tenant_id"] == tid
    assert calls[0]["tenant_id"] is not None


def test_metric_labels_are_bounded_no_address():
    # H67: no address/coordinates ever appear as a metric label - the family has
    # exactly one bounded label ('decision').
    assert address.metrics.address_resolutions_total._labelnames == ("decision",)


def test_build_candidates_exact_match():
    rows = [{"gazetteer_id": 1, "level": "district", "name_norm": "التحرير", "parent_id": None, "tenant_scoped": False}]
    cs = address.build_candidates(["التحرير"], {}, rows)
    assert len(cs) == 1
    assert cs[0].match_kind == "exact"
    assert cs[0].gazetteer_id == 1
