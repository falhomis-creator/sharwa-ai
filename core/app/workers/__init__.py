"""core/app/workers - the `worker-realtime` runtime role (P1.0/P1.1).

Entry point: `python -m app.workers.realtime`.

Modules:
  config.py   - WorkerSettings (frozen dataclass, loaded ONLY by the worker;
                never touches app.config.Settings used by `api`).
  stream.py   - the ONLY place in core/ that runs XREADGROUP/XAUTOCLAIM/XACK/
                XADD-on-DLQ (H17 + hunt_gate rule 9), plus the redis-durable
                client and the per-shard attempts/lag bookkeeping.
  schema.py   - WalEntry (pydantic, extra='ignore') + session resolution cache.
  optout.py   - deterministic opt-out detection (H16: no classifier).
  realtime.py - entry point, per-shard threads, graceful shutdown, /healthz.

This package must never import app.api or app.main (import-linter contract), and
must never import psycopg directly (hunt_gate rule 6 / import-linter contract) -
all SQL goes through app.db.repos_ingest.
"""
