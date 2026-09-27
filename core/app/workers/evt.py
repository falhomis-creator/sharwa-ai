"""core/app/workers/evt.py - the evt:{shard} consumer (T6).

Consumes the gateway's outbound events (queued/sent/failed) via group
ai-core-evt and reconciles them into outbox + the outbound messages row. Only a
real `sent` event (with wa_message_id) writes the outbound message row - never
`delivered`/`read` (not produced by the gateway today, OQ-P1-07).
"""
from __future__ import annotations

from typing import Any

from app.db import repos_inbox
from app.db import repos_outbox
from app.db.context import tenant_tx
from app.obs import metrics
from app import ws_publish
from app.workers import schema
from app.workers.stream import StreamReader, parse_data_field

_ORIGIN_TO_SENT_BY = {"bot": "bot", "automation": "automation", "human": "staff"}


def process_event(
    reader: StreamReader,
    stream: str,
    entry_id: str,
    fields: dict[str, str],
) -> None:
    obj, _raw = parse_data_field(fields)
    entry_type = obj.get("type")
    session_id = str(obj.get("session_id", ""))
    client_msg_id = obj.get("client_msg_id")
    wa_message_id = obj.get("wa_message_id")

    if entry_type not in ("queued", "sent", "failed"):
        metrics.evt_unknown_type_total.inc()
        metrics.evt_processed_total.labels(str(entry_type), "unknown_type").inc()
        reader.ack(stream, entry_id)
        return

    resolution = schema.resolve_session(session_id)
    if resolution is None or resolution.engine != "ai_core":
        metrics.evt_processed_total.labels(str(entry_type), "skipped").inc()
        reader.ack(stream, entry_id)
        return

    if entry_type == "queued":
        metrics.evt_processed_total.labels("queued", "ok").inc()
        reader.ack(stream, entry_id)
        return

    if entry_type == "failed":
        _handle_failed(resolution.tenant_id, client_msg_id, obj.get("error_class"))
        metrics.evt_processed_total.labels("failed", "ok").inc()
        reader.ack(stream, entry_id)
        return

    # entry_type == "sent"
    _handle_sent(resolution.tenant_id, client_msg_id, wa_message_id)
    metrics.evt_processed_total.labels("sent", "ok").inc()
    reader.ack(stream, entry_id)


def _handle_sent(tenant_id: Any, client_msg_id: Any, wa_message_id: Any) -> None:
    if not client_msg_id or not wa_message_id:
        return
    ws_publish.reset_pending()
    with tenant_tx(tenant_id) as conn:
        if not repos_outbox.outbox_exists(conn, str(client_msg_id)):
            metrics.evt_unknown_client_msg_total.inc()
            return
        row = repos_outbox.claim_sent_transition(
            conn, idempotency_key=str(client_msg_id), wa_message_id=str(wa_message_id),
        )
        if row is None:
            # already transitioned to sent (idempotent redelivery).
            return
        sent_by = _ORIGIN_TO_SENT_BY.get(row.origin, "bot")
        body = str(row.payload.get("text", ""))
        if row.conversation_id is not None:
            message_id = repos_outbox.insert_outbound_message(
                conn, tenant_id=row.tenant_id, conversation_id=row.conversation_id,
                sent_by=sent_by, body=body, provider_message_id=str(wa_message_id),
            )
            status_payload = {
                "conversation_id": str(row.conversation_id),
                "message_id": str(message_id), "status": "sent",
                "provider_message_id": str(wa_message_id),
            }
            inbox_seq = repos_inbox.write_inbox_event(
                conn, tenant_id=row.tenant_id, event_type="message.status",
                payload=status_payload,
            )
            ws_publish.queue_publish(
                tenant_id=row.tenant_id, seq=inbox_seq, event_type="message.status",
                payload=status_payload,
            )
            metrics.inbox_events_written_total.labels("message.status").inc()
    ws_publish.flush_publishes()


def _handle_failed(tenant_id: Any, client_msg_id: Any, error_class: Any) -> None:
    if not client_msg_id:
        return
    # error_class='blocked' means the gateway's own kill-switch blocked it (not
    # a technical failure) -> dropped_policy; else failed.
    status = "dropped_policy" if error_class == "blocked" else "failed"
    with tenant_tx(tenant_id) as conn:
        repos_outbox.mark_outbox_by_idempotency(conn, idempotency_key=str(client_msg_id), status=status)
