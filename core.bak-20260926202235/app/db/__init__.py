"""core/app/db - the ONLY package in core/ allowed to import psycopg (enforced
by the import-linter structural test in core/tests/test_import_boundaries.py,
per spec: "اختبار بنيوي يفرض أن لا وحدة خارج core/app/db تستورد مشغّل PostgreSQL").

Everything else in core/ talks to Postgres exclusively through tenant_tx()
or system_tx() below (H2).
"""
from .context import close_pool, init_pool, system_tx, tenant_tx

__all__ = ["close_pool", "init_pool", "system_tx", "tenant_tx"]
