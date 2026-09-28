"""core/app/api/routes_inbox.py - the P1.3 REST inbox (PROMPT §5).

  GET  /v1/conversations                    keyset-cursor list (no OFFSET)
  GET  /v1/conversations/{id}               detail + local customer card
  GET  /v1/conversations/{id}/messages      messages (seq DESC, before_seq)
  POST /v1/conversations/{id}/messages      employee reply (I2 - the critical path)
  POST /v1/conversations/{id}/handoff       state transitions (I3)
  POST /v1/conversations/{id}/resume
  POST /v1/conversations/{id}/close
  POST /v1/conversations/{id}/assign        assignment (I4)
  POST /v1/conversations/{id}/claim
  POST /v1/conversations/{id}/transfer
  GET  /v1/conversations/{id}/notes         internal notes (I5)
  POST /v1/conversations/{id}/notes
  GET  /v1/inbox/events                     REST resync (I8)

Every tenant-scoped path runs under tenant_tx() so RLS is the real backstop; a
foreign resource returns zero rows -> NOT_FOUND, never FORBIDDEN (H29). The
employee reply relies on the DB trigger (sent_by='staff') to pause the bot - it
never calls set_bot_status itself (G3).
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel

from app import db as core_db
from app import ws_publish
from app.api.errors import ApiError
from app.api.rate_limit import rate_limited
from app.db import repos_inbox
from app.db import repos_outbox
from app.obs import metrics
from app.security.permissions import StaffContext

router = APIRouter()

CONVERSATIONS_LIMIT_DEFAULT = 25
CONVERSATIONS_LIMIT_MAX = 100
MESSAGES_LIMIT_MAX = 100
NOTES_LIMIT_MAX = 100
EVENTS_LIMIT_MAX = 500
MAX_STAFF_REPLY_CHARS = 4096
MAX_NOTE_CHARS = 4096

_BOT_STATUSES = ("active", "paused_human", "closed")


# --- request/response bodies -------------------------------------------------


class ReplyBody(BaseModel):
    text: str


class TransitionBody(BaseModel):
    version: int | None = None


class AssignBody(BaseModel):
    staff_id: uuid.UUID
    version: int | None = None


class ClaimBody(BaseModel):
    version: int | None = None


class TransferBody(BaseModel):
    staff_id: uuid.UUID
    version: int | None = None


class NoteBody(BaseModel):
    body: str


# --- helpers -----------------------------------------------------------------


def _hash_body(body: dict[str, Any]) -> str:
    normalized = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def _clamp_limit(value: int | None, default: int, maximum: int) -> int:
    if value is None:
        return default
    if value < 1:
        raise ApiError("VALIDATION_FAILED")
    return min(value, maximum)


def _encode_cursor(last_message_at: datetime.datetime, conversation_id: uuid.UUID) -> str:
    payload = json.dumps(
        {"t": last_message_at.isoformat(), "id": str(conversation_id)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime.datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        return datetime.datetime.fromisoformat(payload["t"]), uuid.UUID(payload["id"])
    except Exception as exc:  # corrupt cursor => VALIDATION_FAILED, never a 500
        raise ApiError("VALIDATION_FAILED") from exc


# --- list / detail / messages (I1) -------------------------------------------

_CONVERSATION_SUMMARY_FIELDS = (
    "id", "bot_status", "assigned_staff_id", "last_message_at", "customer_id",
    "channel_account_id", "epoch", "version",
)


def _conversation_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in _CONVERSATION_SUMMARY_FIELDS if k in row}


def _parse_assigned(assigned: str | None) -> bool | uuid.UUID | None:
    if assigned is None:
        return None
    if assigned == "true":
        return True
    if assigned == "false":
        return False
    try:
        return uuid.UUID(assigned)
    except ValueError as exc:
        raise ApiError("VALIDATION_FAILED") from exc


@router.get("/v1/conversations")
def list_conversations(
    status: str | None = Query(default=None),
    assigned: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int | None = Query(default=None),
    staff: StaffContext = Depends(rate_limited("read", "conversation.read")),
) -> dict[str, Any]:
    if status is not None and status not in _BOT_STATUSES:
        raise ApiError("VALIDATION_FAILED")
    assigned_filter = _parse_assigned(assigned)
    limit = _clamp_limit(limit, CONVERSATIONS_LIMIT_DEFAULT, CONVERSATIONS_LIMIT_MAX)

    cursor_time: datetime.datetime | None = None
    cursor_id: uuid.UUID | None = None
    if cursor is not None:
        cursor_time, cursor_id = _decode_cursor(cursor)

    with core_db.tenant_tx(staff.tenant_id) as conn:
        rows = repos_inbox.list_conversations(
            conn, tenant_id=staff.tenant_id, status=status, assigned=assigned_filter,
            cursor_time=cursor_time, cursor_id=cursor_id, limit=limit + 1,
        )

    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        if last["last_message_at"] is not None:
            next_cursor = _encode_cursor(last["last_message_at"], last["id"])
    return {
        "items": [_conversation_summary(r) for r in rows],
        "next_cursor": next_cursor,
    }


@router.get("/v1/conversations/{conversation_id}")
def get_conversation(
    conversation_id: uuid.UUID,
    staff: StaffContext = Depends(rate_limited("read", "conversation.read")),
) -> dict[str, Any]:
    with core_db.tenant_tx(staff.tenant_id) as conn:
        conv = repos_inbox.fetch_conversation(
            conn, tenant_id=staff.tenant_id, conversation_id=conversation_id,
        )
        if conv is None:
            raise ApiError("NOT_FOUND")
        card = repos_inbox.fetch_customer_card(
            conn, tenant_id=staff.tenant_id, customer_id=conv["customer_id"],
        )
    conv["customer"] = card
    return conv


@router.get("/v1/conversations/{conversation_id}/messages")
def list_messages(
    conversation_id: uuid.UUID,
    before_seq: int | None = Query(default=None),
    limit: int | None = Query(default=None),
    staff: StaffContext = Depends(rate_limited("read", "conversation.read")),
) -> dict[str, Any]:
    limit = _clamp_limit(limit, MESSAGES_LIMIT_MAX, MESSAGES_LIMIT_MAX)
    with core_db.tenant_tx(staff.tenant_id) as conn:
        exists = repos_inbox.fetch_conversation(
            conn, tenant_id=staff.tenant_id, conversation_id=conversation_id,
        )
        if exists is None:
            raise ApiError("NOT_FOUND")
        rows = repos_inbox.list_messages(
            conn, tenant_id=staff.tenant_id, conversation_id=conversation_id,
            before_seq=before_seq, limit=limit,
        )
    return {"items": rows, "next_before_seq": rows[-1]["seq"] if len(rows) == limit else None}




# --- employee reply (I2) -----------------------------------------------------


@router.post("/v1/conversations/{conversation_id}/messages", status_code=201)
def reply_to_conversation(
    conversation_id: uuid.UUID,
    body: ReplyBody,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    staff: StaffContext = Depends(rate_limited("write", "conversation.reply")),
) -> dict[str, Any]:
    if not idempotency_key:
        raise ApiError("IDEMPOTENCY_KEY_REQUIRED")
    text = body.text.strip()
    ws_publish.reset_pending()
    if not text or len(text) > MAX_STAFF_REPLY_CHARS:
        raise ApiError("VALIDATION_FAILED")
    request_hash = _hash_body({"text": text})
    result_body = {"message_id": "", "seq": 0, "epoch": 0, "bot_status": ""}

    with core_db.tenant_tx(staff.tenant_id) as conn:
        existing = repos_inbox.fetch_idempotency(
            conn, tenant_id=staff.tenant_id, idempotency_key=idempotency_key,
        )
        if existing is not None:
            existing_hash, existing_status, existing_body = existing
            if existing_hash != request_hash:
                metrics.idempotency_hits_total.labels("conflict").inc()
                raise ApiError("IDEMPOTENCY_KEY_REUSED")
            metrics.idempotency_hits_total.labels("replay").inc()
            response.status_code = existing_status
            return existing_body

        conv = repos_inbox.fetch_conversation_reply_view(
            conn, tenant_id=staff.tenant_id, conversation_id=conversation_id,
        )
        if conv is None:
            raise ApiError("NOT_FOUND")
        if conv.bot_status == "closed":
            raise ApiError("CONVERSATION_CLOSED")

        # The trigger pauses the bot here (sent_by='staff', direction='out') in
        # this same transaction - NOT set_bot_status (G3).
        message_id, seq = repos_inbox.insert_staff_message(
            conn, tenant_id=staff.tenant_id, conversation_id=conversation_id,
            staff_id=staff.staff_id, body=text,
        )
        state = repos_inbox.read_conversation_state(conn, conversation_id=conversation_id)
        epoch, bot_status = state if state is not None else (conv.version, conv.bot_status)

        # origin='human' + expected_epoch=NULL: a staff message is never dropped
        # by the ai_reply gate (H27). idempotency_key is deterministic from the
        # message itself.
        repos_outbox.insert_outbox(
            conn, tenant_id=staff.tenant_id, conversation_id=conversation_id,
            channel_account_id=conv.channel_account_id,
            idempotency_key=f"staff:{message_id}",
            origin="human", message_class="service", expected_epoch=None,
            to_wa_id=conv.to_wa_id, template_id="", text=text,
        )
        reply_payload = {
            "conversation_id": str(conversation_id), "message_id": str(message_id),
            "seq": seq, "direction": "out", "sent_by": "staff", "type": "text",
        }
        inbox_seq = repos_inbox.write_inbox_event(
            conn, tenant_id=staff.tenant_id, event_type="message.new", payload=reply_payload,
        )
        ws_publish.queue_publish(
            tenant_id=staff.tenant_id, seq=inbox_seq, event_type="message.new",
            payload=reply_payload,
        )
        metrics.inbox_events_written_total.labels("message.new").inc()
        metrics.staff_messages_total.inc()

        result_body = {
            "message_id": str(message_id), "seq": seq, "epoch": epoch,
            "bot_status": bot_status,
        }
        repos_inbox.store_idempotency(
            conn, tenant_id=staff.tenant_id, idempotency_key=idempotency_key,
            request_hash=request_hash, response_status=201, response_body=result_body,
        )

    ws_publish.flush_publishes()
    return result_body


# --- state transitions (I3) --------------------------------------------------


def _transition(
    conversation_id: uuid.UUID, body: TransitionBody, action: str, new_status: str,
    reason: str, staff: StaffContext,
) -> dict[str, Any]:
    ws_publish.reset_pending()
    with core_db.tenant_tx(staff.tenant_id) as conn:
        conv = repos_inbox.fetch_conversation(
            conn, tenant_id=staff.tenant_id, conversation_id=conversation_id,
        )
        if conv is None:
            raise ApiError("NOT_FOUND")
        version = body.version if body.version is not None else conv["version"]
        new_epoch = repos_outbox.set_bot_status(
            conn, conversation_id=conversation_id, expected_version=version,
            new_status=new_status, reason=reason, staff_id=staff.staff_id,
        )
        if new_epoch is None:
            metrics.state_transitions_total.labels(action, "conflict_version").inc()
            raise ApiError("CONFLICT_VERSION")
        transition_payload = {
            "conversation_id": str(conversation_id), "bot_status": new_status,
            "epoch": new_epoch, "version": version + 1, "handoff_reason": reason,
        }
        inbox_seq = repos_inbox.write_inbox_event(
            conn, tenant_id=staff.tenant_id, event_type="conversation.updated",
            payload=transition_payload,
        )
        ws_publish.queue_publish(
            tenant_id=staff.tenant_id, seq=inbox_seq, event_type="conversation.updated",
            payload=transition_payload,
        )
        metrics.inbox_events_written_total.labels("conversation.updated").inc()
        metrics.state_transitions_total.labels(action, "ok").inc()
    ws_publish.flush_publishes()
    return {"conversation_id": str(conversation_id), "bot_status": new_status,
            "epoch": new_epoch, "version": version + 1}


@router.post("/v1/conversations/{conversation_id}/handoff")
def handoff_conversation(
    conversation_id: uuid.UUID, body: TransitionBody,
    staff: StaffContext = Depends(rate_limited("write", "conversation.handoff")),
) -> dict[str, Any]:
    return _transition(conversation_id, body, "handoff", "paused_human", "manual_handoff", staff)


@router.post("/v1/conversations/{conversation_id}/resume")
def resume_conversation(
    conversation_id: uuid.UUID, body: TransitionBody,
    staff: StaffContext = Depends(rate_limited("write", "conversation.handoff")),
) -> dict[str, Any]:
    return _transition(conversation_id, body, "resume", "active", "staff_resumed", staff)


@router.post("/v1/conversations/{conversation_id}/close")
def close_conversation(
    conversation_id: uuid.UUID, body: TransitionBody,
    staff: StaffContext = Depends(rate_limited("write", "conversation.handoff")),
) -> dict[str, Any]:
    return _transition(conversation_id, body, "close", "closed", "staff_closed", staff)



# --- assignment (I4) ---------------------------------------------------------


def _assign(
    conversation_id: uuid.UUID, version: int | None, target_staff_id: uuid.UUID | None,
    staff: StaffContext,
) -> dict[str, Any]:
    with core_db.tenant_tx(staff.tenant_id) as conn:
        conv = repos_inbox.fetch_conversation(
            conn, tenant_id=staff.tenant_id, conversation_id=conversation_id,
        )
        if conv is None:
            raise ApiError("NOT_FOUND")
        v = version if version is not None else conv["version"]
        if not repos_inbox.assign_conversation(
            conn, tenant_id=staff.tenant_id, conversation_id=conversation_id,
            staff_id=target_staff_id, expected_version=v,
        ):
            raise ApiError("CONFLICT_VERSION")
    return {"conversation_id": str(conversation_id),
            "assigned_staff_id": str(target_staff_id) if target_staff_id else None}


@router.post("/v1/conversations/{conversation_id}/claim")
def claim_conversation(
    conversation_id: uuid.UUID, body: ClaimBody,
    staff: StaffContext = Depends(rate_limited("write", "conversation.assign")),
) -> dict[str, Any]:
    return _assign(conversation_id, body.version, staff.staff_id, staff)


@router.post("/v1/conversations/{conversation_id}/assign")
def assign_conversation(
    conversation_id: uuid.UUID, body: AssignBody,
    staff: StaffContext = Depends(rate_limited("write", "conversation.assign")),
) -> dict[str, Any]:
    if body.staff_id != staff.staff_id and "conversation.assign_others" not in staff.permissions:
        raise ApiError("FORBIDDEN_PERMISSION")
    return _assign(conversation_id, body.version, body.staff_id, staff)


@router.post("/v1/conversations/{conversation_id}/transfer")
def transfer_conversation(
    conversation_id: uuid.UUID, body: TransferBody,
    staff: StaffContext = Depends(rate_limited("write", "conversation.assign_others")),
) -> dict[str, Any]:
    return _assign(conversation_id, body.version, body.staff_id, staff)


# --- internal notes (I5) -----------------------------------------------------


@router.get("/v1/conversations/{conversation_id}/notes")
def list_notes(
    conversation_id: uuid.UUID,
    limit: int | None = Query(default=None),
    staff: StaffContext = Depends(rate_limited("write", "note")),
) -> dict[str, Any]:
    limit = _clamp_limit(limit, NOTES_LIMIT_MAX, NOTES_LIMIT_MAX)
    with core_db.tenant_tx(staff.tenant_id) as conn:
        if repos_inbox.fetch_conversation(
            conn, tenant_id=staff.tenant_id, conversation_id=conversation_id,
        ) is None:
            raise ApiError("NOT_FOUND")
        rows = repos_inbox.list_notes(
            conn, tenant_id=staff.tenant_id, conversation_id=conversation_id, limit=limit,
        )
    return {"items": rows}


@router.post("/v1/conversations/{conversation_id}/notes", status_code=201)
def add_note(
    conversation_id: uuid.UUID, body: NoteBody,
    staff: StaffContext = Depends(rate_limited("write", "note")),
) -> dict[str, Any]:
    text = body.body.strip()
    if not text or len(text) > MAX_NOTE_CHARS:
        raise ApiError("VALIDATION_FAILED")
    with core_db.tenant_tx(staff.tenant_id) as conn:
        if repos_inbox.fetch_conversation(
            conn, tenant_id=staff.tenant_id, conversation_id=conversation_id,
        ) is None:
            raise ApiError("NOT_FOUND")
        note_id = repos_inbox.insert_note(
            conn, tenant_id=staff.tenant_id, conversation_id=conversation_id,
            staff_id=staff.staff_id, body=text,
        )
    return {"note_id": str(note_id)}


# --- inbox events resync (I8) ------------------------------------------------


@router.get("/v1/inbox/events")
def list_inbox_events(
    since: int = Query(default=0),
    limit: int | None = Query(default=None),
    staff: StaffContext = Depends(rate_limited("read", "conversation.read")),
) -> dict[str, Any]:
    if since < 0:
        raise ApiError("VALIDATION_FAILED")
    limit = _clamp_limit(limit, EVENTS_LIMIT_MAX, EVENTS_LIMIT_MAX)
    with core_db.tenant_tx(staff.tenant_id) as conn:
        rows, latest_seq = repos_inbox.list_inbox_events(
            conn, tenant_id=staff.tenant_id, since_seq=since, limit=limit,
        )
    return {"items": rows, "latest_seq": latest_seq}

