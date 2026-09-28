"""Pure tests for staff resolution (F-P1-11 guard, P2.1 step zero).

The defect that froze P1 was a function whose signature promised
`StaffRow | None` but whose body could only ever return `None` - and no test
named it. These tests name it directly: the recording `conn` double below
returns either a real row or `None` from `fetchone()`, and the
`permissions.resolve_staff` test proves an ACTIVE row resolves to a
StaffContext with permissions instead of raising STAFF_NOT_PROVISIONED.
"""
from __future__ import annotations

import contextlib
import uuid

from app import db as core_db
from app.api.deps import AuthContext
from app.db.repos_inbox import StaffRow, resolve_staff_member
from app.security.jwt import Principal
from app.security.permissions import PERMISSIONS, StaffContext, resolve_staff


class _Row:
    def __init__(self, values: tuple):
        self._values = values

    def __getitem__(self, idx: int):
        return self._values[idx]


class _FetchResult:
    def __init__(self, row: tuple | None):
        self._row = row

    def fetchone(self):
        return self._row


class _RecordingConn:
    """A `conn` double that records the executed query and returns a fixed row."""

    def __init__(self, row: tuple | None):
        self._row = row
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, query: str, params: tuple):
        self.calls.append((query, params))
        return _FetchResult(self._row)


@contextlib.contextmanager
def _fake_tenant_tx(tenant_id: uuid.UUID, row: tuple | None):
    yield _RecordingConn(row)


def test_resolve_staff_member_returns_staff_row_when_present():
    staff_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    conn = _RecordingConn((staff_id, "agent", True))
    result = resolve_staff_member(conn, tenant_id=tenant_id, platform_user_id="sub-1")
    assert result is not None
    assert isinstance(result, StaffRow)
    assert result.id == staff_id
    assert result.role == "agent"
    assert result.active is True


def test_resolve_staff_member_returns_none_when_absent():
    conn = _RecordingConn(None)
    result = resolve_staff_member(conn, tenant_id=uuid.uuid4(), platform_user_id="sub-1")
    assert result is None


def test_resolve_staff_active_row_yields_permissions(monkeypatch):
    staff_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    row = (staff_id, "agent", True)
    monkeypatch.setattr(core_db, "tenant_tx", lambda tid: _fake_tenant_tx(tid, row))
    ctx = AuthContext(
        principal=Principal(sub="sub-1", platform_ref="p1", role="staff"),
        tenant_id=tenant_id,
    )
    staff = resolve_staff(ctx)
    assert isinstance(staff, StaffContext)
    assert staff.staff_id == staff_id
    assert staff.role == "agent"
    assert staff.permissions == PERMISSIONS["agent"]
