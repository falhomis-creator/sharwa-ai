"""core/app/obs - observability layer for the realtime worker (P1.0.2).

Three small, dependency-light modules:

  logging.py  - stdlib `logging` + a custom JSON formatter (no new library, H11),
                fixed H12 field set on every line, phone-digit masking (H5/H20).
  metrics.py  - the worker's own prometheus_client CollectorRegistry + every
                family P1.1.6 mandates (no high-cardinality labels, H20).
  http.py     - a minimal stdlib http.server for GET /healthz (open) and
                GET /metrics (Bearer METRICS_TOKEN, hmac.compare_digest,
                refuses to boot on an empty secret - H5).

This package is imported only by app.workers (never by app.api/app.main); the
`api` process keeps its own metrics registry in app.main as before.
"""
