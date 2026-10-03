"""core/app/workers/dispatch.py - the outbox dispatcher (T4/T5/T8, H22/H24).

One thread, short cycle: app.claim_outbox (system_tx) -> CORE_DISPATCH_MAX_ATTEMPTS
cap (D2) -> final pre-send checks (epoch/bot_status re-read + per-capability
kill-switch + suppression) -> send via core/app/channels (the ONLY network exit)
-> update status per the §5.1 table. `sent` here = "handed to the gateway"; the
real `sent` event from evt:{shard} writes the outbound message row.

D1: deterministic service templates (POLICY_EXEMPT_TEMPLATES) are NOT gated by
ai_reply - §4.10: off = "route to staff + 'we got your message'".
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any

from app.channels.gateway_client import GatewayClient, GatewayUnavailableError
from app.db import repos_outbox
from app.db.context import system_tx, tenant_tx
from app.obs import logging as obs_logging
from app.obs import metrics
from app.workers import templates
from app.workers.config import WorkerSettings

_log = obs_logging.get_logger("dispatch")


def _backoff_s(attempts: int) -> float:
    return min(300.0, 2 ** attempts) * (0.5 + random.random())


def dispatch_cycle(settings: WorkerSettings, client: GatewayClient) -> None:
    with system_tx() as conn:
        stats = repos_outbox.outbox_stats(conn)
    total = 0
    oldest = 0.0
    for message_class, depth, oldest_pending in stats:
        metrics.outbox_depth.labels(message_class).set(depth)
        metrics.outbox_oldest_pending_seconds.labels(message_class).set(oldest_pending)
        total += depth
        oldest = max(oldest, oldest_pending)
    metrics.dispatch_queue_depth.set(total)
    metrics.dispatch_oldest_pending_seconds.set(oldest)

    with system_tx() as conn:
        rows = repos_outbox.claim_outbox(
            conn, limit=settings.core_dispatch_batch, lease_s=settings.core_dispatch_lease_s,
        )
    for row in rows:
        if row.attempts > settings.core_dispatch_max_attempts:
            _fail_and_escalate(settings, row)
            continue
        try:
            session_id = _pre_send_checks(settings, row)
            if session_id is None:
                continue
            started = time.monotonic()
            resp = _send_one(client, row, session_id)
            metrics.dispatch_duration_seconds.observe(time.monotonic() - started)
            _handle_response(settings, row, resp)
        except GatewayUnavailableError:
            metrics.dispatch_attempts_total.labels("retry").inc()
        except Exception as exc:
            obs_logging.log_event(
                _log, event="dispatch.error", component="dispatch",
                level=logging.ERROR, outbox_id=str(row.id), error=str(exc),
            )


def _fail_and_escalate(settings: WorkerSettings, row: repos_outbox.OutboxRow) -> None:
    """D2: past the attempts cap, mark failed + log loudly + hand off to a human.

    F-P3-07: only a BOT reply failure pauses the customer's conversation (send_failed).
    An `automation` failure must NOT stop a customer who is chatting right now -
    it is failed + counted + logged, with no set_bot_status call."""
    with tenant_tx(row.tenant_id) as conn:
        repos_outbox.mark_outbox_status(conn, outbox_id=row.id, status="failed")
        if row.origin == "bot" and row.conversation_id is not None:
            cur = repos_outbox.read_conversation_epoch_status(conn, row.conversation_id)
            version = repos_outbox.read_conversation_version(conn, row.conversation_id)
            if cur is not None and version is not None and cur[1] == "active":
                repos_outbox.set_bot_status(
                    conn, conversation_id=row.conversation_id, expected_version=version,
                    new_status="paused_human", reason="send_failed",
                )
    metrics.dispatch_attempts_total.labels("failed").inc()
    obs_logging.log_event(
        _log, event="dispatch.gave_up", component="dispatch",
        level=logging.ERROR, outbox_id=str(row.id), attempts=row.attempts,
    )



def _pre_send_checks(settings: WorkerSettings, row: repos_outbox.OutboxRow) -> str | None:
    """Final pre-send checks (H22 'the last check wins'). Returns session_id to
    send to, or None when the row was dropped (status already updated)."""
    template_id = str(row.payload.get("template", ""))
    policy_exempt = template_id in templates.POLICY_EXEMPT_TEMPLATES
    with tenant_tx(row.tenant_id) as conn:
        if row.origin == "bot" and row.conversation_id is not None:
            cur = repos_outbox.read_conversation_epoch_status(conn, row.conversation_id)
            # F-P1-09 (PROMPT_P1_06 §0.2/§3, migration 0011): a bot row is stale iff
            # its epoch no longer matches OR the conversation is CLOSED. 'paused_human'
            # alone is NOT a drop reason - the old `cur[1] != "active"` dropped the very
            # handoff_notice/safe_ack written by the transition that paused the bot.
            if cur is None or cur[1] == "closed" or (
                row.expected_epoch is not None and cur[0] != row.expected_epoch
            ):
                repos_outbox.mark_outbox_status(conn, outbox_id=row.id, status="dropped_stale")
                metrics.dispatch_attempts_total.labels("dropped_stale").inc()
                return None
        # D4: capability gate is independent of origin.
        if row.message_class == "marketing":
            switch = repos_outbox.effective_switch(
                conn, tenant_id=row.tenant_id,
                channel_account_id=row.channel_account_id, capability="marketing",
            )
            if switch == "off":
                repos_outbox.mark_outbox_status(conn, outbox_id=row.id, status="dropped_policy")
                metrics.dispatch_attempts_total.labels("dropped_policy").inc()
                return None
        elif row.origin == "bot" and not policy_exempt:
            switch = repos_outbox.effective_switch(
                conn, tenant_id=row.tenant_id,
                channel_account_id=row.channel_account_id, capability="ai_reply",
            )
            if switch == "off":
                repos_outbox.mark_outbox_status(conn, outbox_id=row.id, status="dropped_policy")
                metrics.dispatch_attempts_total.labels("dropped_policy").inc()
                return None
        if row.origin != "human" and row.message_class != "service" and row.conversation_id is not None:
            customer_id = repos_outbox.customer_id_for_conversation(conn, row.conversation_id)
            if customer_id is not None and repos_outbox.has_suppression(
                conn, row.tenant_id, customer_id, row.message_class,
            ):
                repos_outbox.mark_outbox_status(conn, outbox_id=row.id, status="dropped_policy")
                metrics.dispatch_attempts_total.labels("dropped_policy").inc()
                return None
        session_id = repos_outbox.read_channel_session_id(conn, row.channel_account_id)
        if session_id is None:
            repos_outbox.mark_outbox_status(conn, outbox_id=row.id, status="failed")
            metrics.dispatch_attempts_total.labels("failed").inc()
            return None
        return session_id


def _send_one(client: GatewayClient, row: repos_outbox.OutboxRow, session_id: str) -> Any:
    kind = "marketing" if row.message_class == "marketing" else "interactive"
    text = str(row.payload.get("text", ""))
    return client.send(
        session_id=session_id, to=row.to_wa_id, text=text,
        client_msg_id=row.idempotency_key, kind=kind,
    )


def _handle_response(settings: WorkerSettings, row: repos_outbox.OutboxRow, resp: Any) -> None:
    if resp.status_code == 202:
        with tenant_tx(row.tenant_id) as conn:
            repos_outbox.mark_outbox_sent(conn, outbox_id=row.id)
        metrics.dispatch_attempts_total.labels("sent").inc()
        return
    if resp.status_code == 423:
        with tenant_tx(row.tenant_id) as conn:
            repos_outbox.mark_outbox_status(conn, outbox_id=row.id, status="dropped_policy")
        metrics.dispatch_attempts_total.labels("dropped_policy").inc()
        return
    if resp.status_code == 429:
        with tenant_tx(row.tenant_id) as conn:
            repos_outbox.requeue_with_backoff(conn, outbox_id=row.id, delay_s=_backoff_s(row.attempts))
        metrics.dispatch_attempts_total.labels("retry").inc()
        return
    if resp.status_code in (400, 404):
        with tenant_tx(row.tenant_id) as conn:
            repos_outbox.mark_outbox_status(conn, outbox_id=row.id, status="failed")
        metrics.dispatch_attempts_total.labels("failed").inc()
        obs_logging.log_event(
            _log, event="dispatch.failed", component="dispatch",
            level=logging.ERROR, outbox_id=str(row.id), status=resp.status_code,
        )
        return
    metrics.dispatch_attempts_total.labels("retry").inc()
