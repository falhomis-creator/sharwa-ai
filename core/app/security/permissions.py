"""core/app/security/permissions.py - staff-members RBAC (P1.3 §5.1).

Two role systems are reconciled here:
  - `Principal.role` (JWT) in merchant_admin | staff | platform_admin - the coarse
    require_role gate (already in app.api.deps).
  - `staff_members.role` in owner | manager | agent | viewer - the fine-grained
    permission source.

The rule (PROMPT §5.1): after authenticate(), resolve Principal.sub to a
staff_members row via UNIQUE(tenant_id, platform_user_id). No row / inactive =>
STAFF_NOT_PROVISIONED (403) - never an automatic row creation. A platform_admin
WITHOUT a staff row is read-only (conversation.read) and can never send a message
on behalf of a store: a message reaching a real customer must carry a real staff
identity.

Every route declares its permission with ONE dependency
(`require_permission("conversation.reply")`), never scattered `if` conditions.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends

from app import db as core_db
from app.api.deps import AuthContext, authenticate
from app.api.errors import ApiError
from app.db import repos_inbox

# §5.1 permission matrix (6 permissions). Note: "note" covers note.read + note.write
# as a single row in the matrix - owner/manager/agent have it, viewer does not.
_PERMISSION_NAMES = frozenset({
    "conversation.read", "conversation.reply", "conversation.handoff",
    "conversation.assign", "conversation.assign_others", "note",
})

PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": _PERMISSION_NAMES,
    "manager": _PERMISSION_NAMES,
    "agent": frozenset({
        "conversation.read", "conversation.reply", "conversation.handoff",
        "conversation.assign", "note",
    }),
    "viewer": frozenset({"conversation.read"}),
}

# A platform_admin with no staff_members row is read-only (never replies/assigns).
_PLATFORM_ADMIN_READ_ONLY = frozenset({"conversation.read"})


@dataclass(frozen=True)
class StaffContext:
    tenant_id: uuid.UUID
    staff_id: uuid.UUID | None  # None for a platform_admin with no staff row
    role: str | None            # staff_members.role, or None for platform_admin
    permissions: frozenset[str]


def resolve_staff(ctx: AuthContext) -> StaffContext:
    """Resolve the authenticated Principal to a staff_members row and its
    permission set. No row / inactive => STAFF_NOT_PROVISIONED (403); a
    platform_admin with no row is read-only."""
    with core_db.tenant_tx(ctx.tenant_id) as conn:
        row = repos_inbox.resolve_staff_member(
            conn, tenant_id=ctx.tenant_id, platform_user_id=ctx.principal.sub,
        )
    if row is None or not row.active:
        if ctx.principal.role == "platform_admin":
            return StaffContext(
                tenant_id=ctx.tenant_id, staff_id=None, role=None,
                permissions=_PLATFORM_ADMIN_READ_ONLY,
            )
        raise ApiError("STAFF_NOT_PROVISIONED")
    return StaffContext(
        tenant_id=ctx.tenant_id, staff_id=row.id, role=row.role,
        permissions=PERMISSIONS[row.role],
    )


def require_permission(permission: str) -> Callable[[AuthContext], StaffContext]:
    """A single-dependency permission gate (PROMPT §5.1)."""
    def _check(ctx: AuthContext = Depends(authenticate)) -> StaffContext:
        staff = resolve_staff(ctx)
        if permission not in staff.permissions:
            raise ApiError("FORBIDDEN_PERMISSION")
        return staff
    return _check
