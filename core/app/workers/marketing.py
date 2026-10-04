"""core/app/workers/marketing.py - P3.4 operator-side coordinator (H100-H102).

Not a poller and not a sender: the three things the operator CLI needs that are
more than one repo call -

  preflight()      the checks `enable` must pass (H100). Pure reads; returns the
                   list of FAILED check names (closed vocabulary) - empty = go.
  disable_tenant() H101 level 2: ONE transaction that flips the tenant off, drops
                   its pending marketing outbox rows, gives their reserved slots
                   back (H85) and cancels its pending cart_reminder jobs.
                   utility/service are never touched.
  preview()        a dry run - COUNTS and a synthetic-data render (H20/H48).

No network, no clock of its own (`now` injected), no phone, no customer text.
`set_enabled(enabled=True)` is deliberately NOT called here - only app/cli.py
may enable (S29-b): the enable is a human act, the rollback is not.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Any

from app import marketing_ops
from app.db import repos_marketing
from app.workers import cart_reminder, config, proactive
from app.workers.config import WorkerSettings

TEMPLATE_ID = cart_reminder.TEMPLATE_ID

# Re-exported so existing callers (app/cli.py, tests) keep one import path.
PREFLIGHT_FAILURES = marketing_ops.PREFLIGHT_FAILURES
confirm_phrase = marketing_ops.confirm_phrase


def preflight(
    conn: Any, *, tenant_id: uuid.UUID, settings: WorkerSettings,
    now: datetime, footer_explicit: bool,
) -> list[str]:
    """The worker-layer wrapper: injects the facts only this layer can see (is the
    template registered? is the footer non-empty?) into the neutral implementation
    in app.marketing_ops, which the superadmin API shares."""
    return marketing_ops.preflight(
        conn, tenant_id=tenant_id, now=now,
        template_registered=config.proactive_template(TEMPLATE_ID) is not None,
        footer_explicit=bool(footer_explicit and settings.marketing_footer_ar.strip()),
        min_warmup_days=settings.marketing_min_warmup_days,
    )


disable_tenant = marketing_ops.disable_tenant


def build_env() -> marketing_ops.MarketingEnv:
    """The API-side injection (H104). Read from the ENVIRONMENT of the API process -
    the same variables the workers read - so MARKETING_FOOTER_AR must be set in the
    API's environment too, or `enable` is refused with footer_not_explicit."""
    raw_footer = os.environ.get("MARKETING_FOOTER_AR", "").strip()
    footer = raw_footer or config.DEFAULT_MARKETING_FOOTER_AR
    days_raw = os.environ.get("MARKETING_MIN_WARMUP_DAYS", "").strip()
    try:
        min_days = int(days_raw) if days_raw else 3
    except ValueError:
        min_days = 3
    registered = config.proactive_template(TEMPLATE_ID) is not None
    sample = ""
    if registered:
        text, _meta = proactive.render_text(
            TEMPLATE_ID, {"items_phrase": cart_reminder.items_phrase("عنوان تجريبي", 3)},
        )
        sample = f"{text}\n{footer}"
    return marketing_ops.MarketingEnv(
        template_registered=registered, footer_explicit=bool(raw_footer),
        min_warmup_days=min_days, footer_text=footer, sample_text=sample,
    )


def preview(
    conn: Any, *, tenant_id: uuid.UUID, settings: WorkerSettings,
) -> dict[str, object]:
    """Dry run: counts + the template rendered with SYNTHETIC data. Writes nothing."""
    counts = repos_marketing.preview_cart_counts(conn, tenant_id=tenant_id)
    phrase = cart_reminder.items_phrase("عنوان تجريبي", 3)
    text, _meta = proactive.render_text(TEMPLATE_ID, {"items_phrase": phrase})
    return {**counts, "sample": f"{text}\n{settings.marketing_footer_ar}"}
