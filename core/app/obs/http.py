"""core/app/obs/http.py - minimal observability HTTP server for the worker.

Serves two routes on CORE_WORKER_METRICS_PORT (default 4003 - 4001 gateway,
4002 forwarder, 8080 api):

  GET /healthz  - open. 200 only while the consume loop is alive and Redis+PG
                  are reachable (the worker passes a callable health check).
  GET /metrics  - Bearer METRICS_TOKEN, compared with hmac.compare_digest
                  (timing-safe, same posture as gateway/src/forwarder.js).

H5: an empty/missing METRICS_TOKEN refuses worker startup before this server
is ever constructed, so /metrics is never accidentally served unauthenticated.

stdlib http.server in a daemon thread - the worker needs exactly two routes, so
pulling in a framework would be its own unrelated deviation (same reasoning as
gateway/src/forwarder.js's raw http.Server).
"""
from __future__ import annotations

import hmac
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.obs.metrics import registry


def _constant_time_equal(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


class ObservabilityServer:
    """Owns the http.server thread; `health_ok` is a no-arg -> bool the worker
    wires to its live liveness check (threads alive + connections healthy)."""

    def __init__(
        self,
        *,
        port: int,
        metrics_token: str,
        health_ok: Callable[[], bool],
    ) -> None:
        if not metrics_token:
            # H5 - reached defensively; the worker already refuses an empty
            # token at config load, so this is belt-and-braces.
            raise ValueError("metrics_token must be non-empty (H5)")
        self._port = port
        self._metrics_token = metrics_token
        self._health_ok = health_ok
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        server = self

        class _Handler(BaseHTTPRequestHandler):
            def _send(self, code: int, body: bytes, content_type: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                if self.path == "/healthz":
                    healthy = server._health_ok()
                    body = b'{"status":"ok"}' if healthy else b'{"status":"degraded"}'
                    code = 200 if healthy else 503
                    self._send(code, body, "application/json")
                    return
                if self.path == "/metrics":
                    auth = self.headers.get("Authorization", "")
                    provided = auth[7:] if auth.startswith("Bearer ") else ""
                    if not _constant_time_equal(provided, server._metrics_token):
                        self._send(401, b'{"error":"unauthorized"}', "application/json")
                        return
                    body = generate_latest(registry)
                    self._send(200, body, CONTENT_TYPE_LATEST)
                    return
                self._send(404, b'{"error":"not found"}', "application/json")

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                # Silence the default per-request stderr line (H12: structured
                # logs only, and http.server's format is not our JSON shape).
                return

        self._server = ThreadingHTTPServer(("0.0.0.0", self._port), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self._thread = None
