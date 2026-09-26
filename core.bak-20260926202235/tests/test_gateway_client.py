"""Real HTTP server (not a mocked transport) exercising GatewayClient's
timeout + circuit breaker for real."""
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.channels.gateway_client import GatewayClient
from app.config import GatewayConfig

_fail_count = {"n": 0}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            if _fail_count["n"] > 0:
                _fail_count["n"] -= 1
                self.send_response(500)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def stub_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def test_healthz_success(stub_server):
    _fail_count["n"] = 0
    client = GatewayClient(GatewayConfig(base_url=stub_server, timeout_s=1.0), api_key="k")
    assert client.healthz() is True


def test_breaker_opens_after_threshold_and_fast_fails(stub_server):
    _fail_count["n"] = 10  # always fail for a while
    cfg = GatewayConfig(base_url=stub_server, timeout_s=1.0, breaker_fail_threshold=3, breaker_reset_s=1.0)
    client = GatewayClient(cfg, api_key="k")

    assert client.healthz() is False
    assert client.healthz() is False
    assert client.healthz() is False  # 3rd failure trips the breaker

    # Breaker is now open: the 4th call must fast-fail WITHOUT even reaching
    # the (still-failing) server - prove this by making the server always
    # succeed from now on; if the breaker is really open, healthz() still
    # reports False (fast-fail), not True.
    _fail_count["n"] = 0
    assert client.healthz() is False, "breaker should still be open, refusing the network call entirely"


def test_breaker_recovers_after_reset_window(stub_server):
    _fail_count["n"] = 10
    cfg = GatewayConfig(base_url=stub_server, timeout_s=1.0, breaker_fail_threshold=2, breaker_reset_s=0.3)
    client = GatewayClient(cfg, api_key="k")
    client.healthz()
    client.healthz()  # trips breaker
    _fail_count["n"] = 0  # server recovers
    time.sleep(0.4)  # past the reset window
    assert client.healthz() is True, "breaker should allow a half-open trial call after the reset window"
