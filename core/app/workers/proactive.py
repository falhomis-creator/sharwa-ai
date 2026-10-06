"""core/app/workers/proactive.py - the third (and last) outbox writer (H46/S22).

The ONLY code path that inserts an `origin='automation'` row. It never accepts a
`message_class` - the class is DERIVED from the approved template catalog
(app.workers.config.PROACTIVE_TEMPLATES, H77). Text = approved template + merge
fields from a CLOSED whitelist + the verifier (H83). A single INSERT, no network
- runs inside the caller's open transaction (H40, like the allocation path).
"""
from __future__ import annotations

import uuid
from typing import Any


from app.db import repos_outbox
from app.obs import metrics
from app.workers import config
from app.workers import verify_rules
from app.workers.config import WorkerSettings


def render_text(template_id: str, merge: dict[str, str]) -> tuple[str, config.TemplateMeta]:
    """Produce the text for an approved template + merge (footer for marketing)."""
    entry = config.proactive_template(template_id)
    if entry is None:
        raise ValueError(f"unknown proactive template {template_id!r}")
    meta, text, merge_keys = entry
    extra = set(merge) - set(merge_keys)
    if extra:
        raise ValueError(f"merge keys {sorted(extra)} not allowed for {template_id!r}")
    for key, value in merge.items():
        text = text.replace(f"«{key}»", value)
    return text, meta


def enqueue_proactive(
    conn: Any,
    *,
    settings: WorkerSettings,
    rules: verify_rules.BlocklistSet,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    template_id: str,
    merge: dict[str, str] | None,
    idempotency_key: str,
) -> uuid.UUID:
    """The third outbox writer, origin='automation' literally (S22). No
    message_class parameter - derived from the catalog. Fails closed on a
    verifier violation: verifier_blocks row + no write (H83)."""
    merge = merge or {}
    text, meta = render_text(template_id, merge)
    if meta.footer_required:
        text = f"{text}\n{settings.marketing_footer_ar}"

    verdict = verify_rules.check_text(
        text, rules=rules, max_chars=settings.verify_max_chars,
    )
    if not verdict.ok:
        repos_outbox.insert_verifier_block(
            conn, tenant_id=tenant_id, conversation_id=conversation_id,
            reason=f"proactive_{verdict.rule_id}", draft_excerpt=None,
        )
        metrics.verify_violations_total.labels(verdict.rule_id).inc()
        raise ValueError(f"proactive template {template_id!r} violates {verdict.rule_id}")

    channel_id = repos_outbox.resolve_channel_for_conversation(conn, conversation_id)
    to_wa_id = repos_outbox.wa_id_for_conversation(conn, conversation_id)
    return repos_outbox.insert_outbox(
        conn, tenant_id=tenant_id, conversation_id=conversation_id,
        channel_account_id=channel_id, idempotency_key=idempotency_key,
        origin="automation", message_class=meta.message_class,
        expected_epoch=None, to_wa_id=to_wa_id, template_id=template_id, text=text,
    )


def template_ids_for_scope(scope: str) -> tuple[str, ...]:
    """The template_ids whose consent_scope == `scope`, from the catalog (D4)."""
    return config.template_ids_for_scope(scope)
