"""core/app/db/context.py

The two - and only two - database entry points core/ may use (H2):

  tenant_tx(tenant_id)  - opens a transaction, immediately issues
                          SET LOCAL app.tenant_id = '<uuid>' (transaction-scoped,
                          so it is safe behind PgBouncer transaction pooling - the
                          setting cannot leak to a later transaction that reuses
                          the same physical server connection), then yields a
                          real psycopg connection for the caller to run queries
                          on. RLS (already defined in schema.sql) does the actual
                          enforcement; this function's only job is to make sure
                          that setting is ALWAYS present before a single query
                          runs under a tenant context.

  system_tx()           - opens a transaction on the system role's connection,
                          with NO app.tenant_id set. Only SECURITY DEFINER
                          functions and tables sharwa_system is explicitly
                          granted may be touched here (schema.sql's own GRANTs
                          are the real boundary; this function does not
                          re-implement that check, it only picks the right role).

psycopg's `prepare_threshold=None` on both pools disables server-side prepared
statements, which is required for correctness behind PgBouncer's transaction
pooling mode (a prepared statement is tied to one physical connection; PgBouncer
reassigns physical connections between transactions, so a prepared statement
from a previous transaction may not exist on the connection this transaction
gets - spec: "psycopg 3 (prepare_threshold=None لتوافق PgBouncer transaction pooling)").
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg_pool import ConnectionPool

from app.config import Settings

_tenant_pool: ConnectionPool | None = None
_system_pool: ConnectionPool | None = None


def init_pool(settings: Settings, *, system_dsn: str | None = None) -> None:
    """Called once at startup. Two separate pools (not one pool with two
    connection strings) so a tenant-role connection can never accidentally be
    reused for a system_tx() call or vice versa."""
    global _tenant_pool, _system_pool
    _tenant_pool = ConnectionPool(
        settings.db.dsn,
        min_size=settings.db.pool_min,
        max_size=settings.db.pool_max,
        kwargs={"prepare_threshold": None, "autocommit": False},
        open=True,
    )
    _system_pool = ConnectionPool(
        system_dsn or settings.db.system_dsn,
        min_size=1,
        max_size=max(2, settings.db.pool_min),
        kwargs={"prepare_threshold": None, "autocommit": False},
        open=True,
    )


def close_pool() -> None:
    global _tenant_pool, _system_pool
    if _tenant_pool is not None:
        _tenant_pool.close()
        _tenant_pool = None
    if _system_pool is not None:
        _system_pool.close()
        _system_pool = None


class TenantContextError(RuntimeError):
    pass


@contextmanager
def tenant_tx(tenant_id: uuid.UUID | str) -> Iterator[psycopg.Connection]:
    if _tenant_pool is None:
        raise TenantContextError("db pool not initialized - call init_pool() at startup")
    tenant_id = str(tenant_id)
    # Defensive: a malformed value here would silently become NULL in Postgres
    # (NULLIF on empty string in app.current_tenant()), which means "no tenant"
    # and RLS then returns zero rows rather than leaking - but we still fail
    # loudly here rather than ever send a plausible-looking non-UUID down to
    # SET LOCAL.
    try:
        uuid.UUID(tenant_id)
    except ValueError as exc:
        raise TenantContextError(f"tenant_id is not a valid UUID: {tenant_id!r}") from exc

    with _tenant_pool.connection() as conn, conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
        yield conn


@contextmanager
def system_tx() -> Iterator[psycopg.Connection]:
    if _system_pool is None:
        raise TenantContextError("db pool not initialized - call init_pool() at startup")
    with _system_pool.connection() as conn, conn.transaction():
        yield conn
