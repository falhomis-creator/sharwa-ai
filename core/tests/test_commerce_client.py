"""Real HTTP server (not a mocked transport) exercising CommerceClient's
HMAC signing (OQ-P4-03): the server recomputes the signature from the shared
secret + timestamp + raw request target and rejects a mismatch with 401."""
import hashlib
import hmac
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.channels.commerce_client import CommerceClient, CommerceClientError

SERVER_SECRET = b"test-secret"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        ts = self.headers.get("X-Sharwa-AI-Timestamp")
        sig = self.headers.get("X-Sharwa-AI-Signature")
        if not ts or not sig:
            self._reject()
            return
        try:
            ts_int = int(ts)
        except ValueError:
            self._reject()
            return
        if abs(int(time.time()) - ts_int) > 300:
            self._reject()
            return
        expected = hmac.new(
            SERVER_SECRET, ts.encode() + b".GET " + self.path.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, sig):
            self._reject()
            return
        if self.path.startswith("/changes"):
            body = b'{"events": [], "cursor": "c1"}'
        elif self.path.startswith("/order_lookup"):
            body = b'{"order": {"status": "x"}}'
        else:
            body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def _reject(self):
        self.send_response(401)
        self.end_headers()

    def log_message(self, *a):
        return None


@pytest.fixture(scope="module")
def stub_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def test_signed_request_is_accepted(stub_server):
    client = CommerceClient(stub_server, secret="test-secret")
    assert client.get_changes("t1", None) == {"events": [], "cursor": "c1"}


def test_signature_covers_raw_query(stub_server):
    client = CommerceClient(stub_server, secret="test-secret")
    card = client.lookup_order("t1", "order-1", ("a", "b", "c"), "same_number")
    assert card == {"status": "x"}


def test_wrong_secret_is_rejected(stub_server):
    client = CommerceClient(stub_server, secret="wrong-secret")
    with pytest.raises(CommerceClientError):
        client.get_changes("t1", None)


def test_empty_secret_refused():
    with pytest.raises(ValueError):
        CommerceClient("http://127.0.0.1:1", secret="")
    with pytest.raises(ValueError):
        CommerceClient("http://127.0.0.1:1", secret="   ")
