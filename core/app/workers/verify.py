"""core/app/workers/verify.py - the output-verifier enforcement layer (H46/H47).

The ONLY write point to outbox for anything the bot originates (H46): turn.py
calls insert_verified_outbox() instead of repos_outbox.insert_outbox(). Every bot
text passes check_text() first (H45); a violation never writes the original text
- it writes a verifier_blocks row + a SAFE_FALLBACK_TEMPLATE and hands the
conversation to a human. The verifier FAILS CLOSED (H47): any verifier error
becomes a violation with rule_id="verifier_error".

The staff reply path (routes_inbox.py, origin="human") does NOT pass through here
and is never checked or blocked (H24/H27).
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from app import ws_publish
from app.config import ConfigError
from app.db import repos_inbox
from app.db import repos_outbox
from app.obs import logging as obs_logging
from app.obs import metrics
from app.text import redact
from app.workers import templates
from app.workers import verify_rules
from app.workers.config import PROACTIVE_TEMPLATES, WorkerSettings
from app.workers.stream import TransientError

_log = obs_logging.get_logger("verify")


@dataclass(frozen=True)
class VerifyOutcome:
    ok: bool
    rule_id: str | None
    outbox_id: uuid.UUID
    # True when THIS call transitioned the conversation active -> paused_human.
    paused: bool


def build_rules(settings: WorkerSettings) -> verify_rules.BlocklistSet:
    """Build the BlocklistSet once at boot, then fail-fast on any approved
    template that violates the blocklists (H5): a polluted approved template is a
    policy error that must explode at boot, not become a silent block mid-chat."""
    rules = verify_rules.build_rules(
        profanity=settings.verify_profanity_ar + settings.verify_profanity_en,
        competitor=settings.verify_competitors,
        disclosure=settings.verify_disclosure,
    )
    metrics.verify_blocklist_phrases.labels("profanity").set(len(rules.profanity))
    metrics.verify_blocklist_phrases.labels("competitor").set(len(rules.competitor))
    metrics.verify_blocklist_phrases.labels("disclosure").set(len(rules.disclosure))
    metrics.verify_enabled.set(1 if settings.verify_enabled else 0)
    if not settings.verify_enabled:
        obs_logging.log_event(_log, event="verify.disabled", component="verify", level=logging.WARNING)
    if rules.ignored_empty:
        obs_logging.log_event(
            _log, event="verify.blocklist_ignored_empty", component="verify",
            level=logging.WARNING, count=rules.ignored_empty,
        )
    _self_check(settings, rules)
    return rules


def _self_check(settings: WorkerSettings, rules: verify_rules.BlocklistSet) -> None:
    from app.workers import compose
    samples: list[tuple[str, str]] = [(tid, text) for tid, text in templates.TEMPLATES.items()]
    samples.append(("product_list", compose.PRODUCT_LIST_TEMPLATE.format(items="منتج متوفر")))
    # P3.1 (H83): proactive templates are also self-checked at boot, rendered with
    # a placeholder merge value - a polluted approved template explodes at boot.
    for tid, (meta, text, keys) in PROACTIVE_TEMPLATES.items():
        rendered = text
        for key in keys:
            rendered = rendered.replace(f"«{key}»", "X")
        if meta.footer_required:
            rendered = f"{rendered}\n{settings.marketing_footer_ar}"
        samples.append((f"proactive:{tid}", rendered))
    for name, text in samples:
        verdict = verify_rules.check_text(
            text, rules=rules, max_chars=settings.verify_max_chars,
            window_max=settings.verify_join_window_max,
        )
        if not verdict.ok:
            raise ConfigError(f"approved template {name!r} violates verifier rule {verdict.rule_id}")


def insert_verified_outbox(
    conn: Any,
    *,
    settings: WorkerSettings,
    rules: verify_rules.BlocklistSet,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    channel_account_id: uuid.UUID,
    idempotency_key: str,
    message_class: str,
    expected_epoch: int | None,
    to_wa_id: str,
    template_id: str,
    text: str,
    size_context: verify_rules.SizeContext | None = None,
) -> VerifyOutcome:
    """The single outbox write point for bot-originated text (H46). No `origin`
    parameter: this writes origin="bot" itself. `size_context` (Task 11) hands
    the SizeAdvice outcome to the size_mismatch rule as plain data; None - the
    default, every legacy caller - keeps the verdict identical, and the
    announced-disable path writes as-is without ever consulting it."""
    started = time.monotonic()
    try:
        if not settings.verify_enabled:
            # Announced disable (H4/OQ): write as-is, gauge makes it visible.
            metrics.verify_enabled.set(0)
            # Wrapped in VerifiedText so S10-e (text= must be .value) stays
            # enforceable structurally even on the disable path.
            approved = verify_rules.VerifiedText(value=text)
            return _write_approved(
                conn, tenant_id=tenant_id, conversation_id=conversation_id,
                channel_account_id=channel_account_id, idempotency_key=idempotency_key,
                message_class=message_class, expected_epoch=expected_epoch,
                to_wa_id=to_wa_id, template_id=template_id, approved=approved,
                count_check=False,
            )

        metrics.verify_enabled.set(1)
        try:
            result = verify_rules.approve(
                text, rules=rules, max_chars=settings.verify_max_chars,
                window_max=settings.verify_join_window_max,
                size_context=size_context,
            )
        except Exception as exc:  # noqa: BLE001 - H47: fail closed on verifier error
            obs_logging.log_event(
                _log, event="verify.error", component="verify", level=logging.ERROR,
                conversation_id=str(conversation_id), error=str(exc),
            )
            metrics.verify_errors_total.inc()
            result = verify_rules.RuleVerdict(ok=False, rule_id="verifier_error", category="internal")

        if isinstance(result, verify_rules.VerifiedText):
            return _write_approved(
                conn, tenant_id=tenant_id, conversation_id=conversation_id,
                channel_account_id=channel_account_id, idempotency_key=idempotency_key,
                message_class=message_class, expected_epoch=expected_epoch,
                to_wa_id=to_wa_id, template_id=template_id, approved=result,
                count_check=True,
            )
        return _write_safe(
            conn, settings=settings, verdict=result, tenant_id=tenant_id,
            conversation_id=conversation_id, channel_account_id=channel_account_id,
            idempotency_key=idempotency_key, to_wa_id=to_wa_id, draft_text=text,
        )
    finally:
        metrics.verify_duration_seconds.observe(time.monotonic() - started)


def _write_approved(
    conn: Any,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    channel_account_id: uuid.UUID,
    idempotency_key: str,
    message_class: str,
    expected_epoch: int | None,
    to_wa_id: str,
    template_id: str,
    approved: verify_rules.VerifiedText,
    count_check: bool,
) -> VerifyOutcome:
    outbox_id = repos_outbox.insert_outbox(
        conn, tenant_id=tenant_id, conversation_id=conversation_id,
        channel_account_id=channel_account_id, idempotency_key=idempotency_key,
        origin="bot", message_class=message_class, expected_epoch=expected_epoch,
        to_wa_id=to_wa_id, template_id=template_id, text=approved.value,
    )
    if count_check:
        metrics.verify_checks_total.labels("pass").inc()
    return VerifyOutcome(ok=True, rule_id=None, outbox_id=outbox_id, paused=False)


def _write_safe(
    conn: Any,
    *,
    settings: WorkerSettings,
    verdict: verify_rules.RuleVerdict,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    channel_account_id: uuid.UUID,
    idempotency_key: str,
    to_wa_id: str,
    draft_text: str,
) -> VerifyOutcome:
    rule_id = verdict.rule_id or "verifier_error"

    # Re-read (never trust a stale snapshot - turn.py may have paused the bot in
    # this same transaction already; a second pause on an old version would fail).
    cur = repos_outbox.read_conversation_epoch_status(conn, conversation_id)
    version = repos_outbox.read_conversation_version(conn, conversation_id)
    if cur is None:
        raise TransientError("conversation vanished during verifier violation")

    new_epoch = cur[0]
    paused = False
    if cur[1] == "active":
        if version is None:
            raise TransientError("conversation version unavailable")
        new_epoch = repos_outbox.set_bot_status(
            conn, conversation_id=conversation_id, expected_version=version,
            new_status="paused_human", reason=f"verifier_{rule_id}",
        )
        if new_epoch is None:
            raise TransientError("stale version while pausing bot (verifier)")
        paused = True

    excerpt = redact.mask_phones(draft_text[:settings.verify_excerpt_max_chars])
    repos_outbox.insert_verifier_block(
        conn, tenant_id=tenant_id, conversation_id=conversation_id,
        reason=f"verifier_{rule_id}", draft_excerpt=excerpt,
    )

    safe_template_id = settings.verify_safe_template_id
    outbox_id = repos_outbox.insert_outbox(
        conn, tenant_id=tenant_id, conversation_id=conversation_id,
        channel_account_id=channel_account_id, idempotency_key=f"{idempotency_key}:v1",
        origin="bot", message_class="service", expected_epoch=new_epoch,
        to_wa_id=to_wa_id, template_id=safe_template_id,
        text=templates.template_text(safe_template_id),
    )

    reason = f"verifier_{rule_id}"
    handoff_payload = {"conversation_id": str(conversation_id), "reason": reason}
    handoff_seq = repos_inbox.write_inbox_event(
        conn, tenant_id=tenant_id, event_type="handoff.requested", payload=handoff_payload,
    )
    ws_publish.queue_publish(
        tenant_id=tenant_id, seq=handoff_seq, event_type="handoff.requested", payload=handoff_payload,
    )
    metrics.inbox_events_written_total.labels("handoff.requested").inc()

    # N1 (PROMPT_P1_07 §0.4): announce conversation.updated ONLY when THIS call
    # actually transitioned the bot (paused=True). When it was already paused, the
    # state did not change - and the old payload's handoff_reason=verifier_* would
    # lie to the UI while the DB column still holds turn.py's reason.
    if paused:
        updated_payload = {
            "conversation_id": str(conversation_id), "bot_status": "paused_human",
            "epoch": new_epoch, "version": version + 1, "handoff_reason": reason,
        }
        updated_seq = repos_inbox.write_inbox_event(
            conn, tenant_id=tenant_id, event_type="conversation.updated", payload=updated_payload,
        )
        ws_publish.queue_publish(
            tenant_id=tenant_id, seq=updated_seq, event_type="conversation.updated", payload=updated_payload,
        )
        metrics.inbox_events_written_total.labels("conversation.updated").inc()

    metrics.verify_checks_total.labels("violation").inc()
    metrics.verify_violations_total.labels(rule_id).inc()
    metrics.verify_safe_template_sent_total.inc()
    obs_logging.log_event(
        _log, event="verify.violation", component="verify", level=logging.WARNING,
        rule_id=rule_id, conversation_id=str(conversation_id),
    )
    return VerifyOutcome(ok=False, rule_id=rule_id, outbox_id=outbox_id, paused=paused)


