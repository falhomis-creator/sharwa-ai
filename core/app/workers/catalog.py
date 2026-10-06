"""core/app/workers/catalog.py - the periodic catalog reconciliation (C3, P1.4).

The architecture doc places catalog reconciliation in worker-batch; we integrate
it as a thread in the existing worker-realtime process instead (recorded as a
deviation: one more VPS process vs one thread). The thread wakes every
CATALOG_RECONCILE_INTERVAL_S and, for each tenant, pulls new platform events via
CommercePort.get_changes and applies them with the same H37 source_version guard
as the webhook. Staleness is measured as now() - last_reconcile_ok (risk #9):
a tenant whose catalog has not reconciled for too long is untrustworthy, and
that must be KNOWN rather than discovered from a wrong answer to a customer.
"""
from __future__ import annotations

import datetime
import logging
import time

from app import db as core_db
from app.commerce.port import CommercePort
from app.db import repos_catalog
from app.obs import logging as obs_logging
from app.obs import metrics
from app.workers.config import WorkerSettings

_log = obs_logging.get_logger("catalog")


def reconcile_once(
    port: CommercePort, *, settings: WorkerSettings, seen_unknown_types: set[str],
) -> str:
    """One full reconciliation cycle. Returns 'ok' | 'failed' | 'partial'.

    Bounded (H4): at most CATALOG_RECONCILE_MAX_TENANTS tenants and at most
    CATALOG_RECONCILE_MAX_EVENTS_PER_TENANT events are processed per cycle.
    A page larger than the limit is a contract violation: not applied, cursor not advanced (F-P4-03).
    """
    started = time.monotonic()
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        with core_db.system_tx() as conn:
            tenants = repos_catalog.list_catalog_sync_state(conn)
    except Exception as exc:  # noqa: BLE001 - a transient DB error must not crash the thread
        obs_logging.log_event(
            _log, event="catalog.reconcile.list_failed", component="catalog",
            level=logging.ERROR, error=str(exc),
        )
        metrics.catalog_reconcile_duration_seconds.observe(time.monotonic() - started)
        metrics.catalog_reconcile_runs_total.labels("failed").inc()
        return "failed"

    tenants = tenants[: settings.catalog_reconcile_max_tenants]
    total = len(tenants)
    failed_tenants = 0
    max_staleness = 0.0
    for tenant_id, platform_ref, cursor, last_ok in tenants:
        try:
            events, next_cursor = port.get_changes(platform_ref, cursor)
        except Exception as exc:  # noqa: BLE001 - one tenant's failure must not stop the rest
            failed_tenants += 1
            obs_logging.log_event(
                _log, event="catalog.reconcile.tenant_failed", component="catalog",
                tenant_id=str(tenant_id), level=logging.WARNING, error=str(exc),
            )
            if last_ok is not None and last_ok < now:
                max_staleness = max(max_staleness, (now - last_ok).total_seconds())
            continue
        limit = settings.catalog_reconcile_max_events_per_tenant
        if len(events) > limit:
            # F-P4-03: a page larger than the contract allows is NOT applied and its
            # cursor is NOT advanced - truncating and advancing would silently drop
            # every event past the limit (docs/PLATFORM_COMMERCE_CONTRACT.md §5).
            failed_tenants += 1
            obs_logging.log_event(
                _log, event="catalog.reconcile.contract_violation", component="catalog",
                tenant_id=str(tenant_id), level=logging.ERROR,
                page_events=len(events), limit=limit,
            )
            if last_ok is not None and last_ok < now:
                max_staleness = max(max_staleness, (now - last_ok).total_seconds())
            continue
        with core_db.tenant_tx(tenant_id) as conn:
            repos_catalog.apply_catalog_events(
                conn, tenant_id=tenant_id,
                events=events[: settings.catalog_reconcile_max_events_per_tenant],
                logger=_log, seen_unknown_types=seen_unknown_types,
            )
            repos_catalog.set_catalog_sync_cursor(
                conn, tenant_id=tenant_id, cursor=next_cursor, last_reconcile_ok=now,
            )

    if total == 0 or failed_tenants == 0:
        result = "ok"
    elif failed_tenants < total:
        result = "partial"
    else:
        result = "failed"
    metrics.catalog_staleness_seconds.set(max_staleness)
    metrics.catalog_reconcile_duration_seconds.observe(time.monotonic() - started)
    metrics.catalog_reconcile_runs_total.labels(result).inc()
    return result
