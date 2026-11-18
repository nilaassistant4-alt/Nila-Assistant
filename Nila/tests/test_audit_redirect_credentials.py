"""Regression tests: a credential must never follow a redirect.

The defect: policy (host allow-list, pinned provider host) was checked against
the *declared* URL, then ``urllib.request.urlopen`` followed 3xx automatically
and re-sent ``Authorization`` / ``x-api-key``. A 302 from an allow-listed host
delivered a dummy bearer token to a different host. Reproduced in the
integration gateway, the OpenAI-compatible provider and the sync blob store.

These tests run against a local server that records every request it sees, so
they prove the *destination* never receives the secret - not merely that an
exception was raised.
"""
from __future__ import annotations

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from brain.util.http import RedirectRefused, open_once

SECRET = "DUMMY-AUDIT-TOKEN"


class _Recorder(BaseHTTPRequestHandler):
    """``/start`` 302s to ``/landing``; every request is recorded."""
    seen: list = []

    def log_message(self, *args) -> None:  # silence
        pass

    def _record(self):
        _Recorder.seen.append((self.path, self.headers.get("Authorization"),
                               self.headers.get("x-api-key")))

    def do_GET(self):            # noqa: N802
        self._record()
        if self.path.startswith("/start"):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/landing")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):           # noqa: N802
        self._record()
        if self.path.startswith("/start"):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/landing")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def recordings():
    _Recorder.seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield _Recorder.seen, srv.server_port
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


# --------------------------------------------------------------------------- #
# the primitive
# --------------------------------------------------------------------------- #
def test_open_once_refuses_and_never_reaches_the_destination(recordings):
    seen, port = recordings
    req = urllib.request.Request(f"http://127.0.0.1:{port}/start/one")
    req.add_header("Authorization", f"Bearer {SECRET}")
    with pytest.raises(RedirectRefused):
        open_once(req, timeout=3)
    assert [p for p, _, _ in seen] == ["/start/one"], "the redirect target was contacted"


# --------------------------------------------------------------------------- #
# provider: _post_json
# --------------------------------------------------------------------------- #
def test_provider_post_does_not_follow_a_redirect(recordings):
    from brain.agent.providers import ProviderError, _post_json

    seen, port = recordings
    with pytest.raises(ProviderError) as info:
        _post_json(f"http://127.0.0.1:{port}/start/provider", {"prompt": "hi"},
                   {"Authorization": f"Bearer {SECRET}"}, timeout=3)
    assert "redirect" in str(info.value).lower()
    assert all(p.startswith("/start") for p, _, _ in seen)


# --------------------------------------------------------------------------- #
# sync blob store: HttpBlobStore
# --------------------------------------------------------------------------- #
def test_sync_blob_store_does_not_follow_a_redirect(recordings):
    from brain.cloud.protocol import HttpBlobStore

    seen, port = recordings
    store = HttpBlobStore(f"http://127.0.0.1:{port}", token=SECRET, verify_tls=False)
    status, payload = store._req("GET", "/start/sync")
    assert status == 0
    assert "redirect" in payload.get("error", "").lower()
    assert all(p.startswith("/start") for p, _, _ in seen)


# --------------------------------------------------------------------------- #
# integrations gateway
# --------------------------------------------------------------------------- #
class _AllowAllBroker:
    """A broker that permits everything: we are testing redirect handling,
    not the permission model (which has its own tests).

    It implements the same gate the real Broker does, including ``consume()``.
    The gateway must call the gate that can burn a one-shot approval - a stub
    without it would let the redirect tests pass while the real replay hole
    (audit B10) went unexercised.
    """

    def peek(self, capability, scope=""):
        from brain.security.permission import Decision
        return Decision(allowed=True, capability=capability, scope=scope, reason="test")

    def consume(self, capability, scope="", purpose=""):
        return self.peek(capability, scope)

    def request(self, capability, scope="*", purpose="", **kw):
        return self.peek(capability, scope)


def _gateway(tmp_path, port: int, monkeypatch):
    """A real IntegrationGateway loaded from a one-integration config file."""
    import json as _json

    from brain.agent.integrations import IntegrationGateway

    monkeypatch.setenv("AUDIT_SECRET", SECRET)
    cfg = {"integrations": [{
        "name": "audit", "base_url": f"http://127.0.0.1:{port}", "enabled": True,
        "allow_hosts": ["127.0.0.1"], "auth": {"$env": "AUDIT_SECRET"},
        "operations": [{"name": "ping", "method": "GET", "path": "/start/integration"}],
    }]}
    path = tmp_path / "integrations.json"
    path.write_text(_json.dumps(cfg))
    from brain.config import Config
    gw = IntegrationGateway(Config(home=tmp_path / "home"), path=path)
    gw.integrations["audit"].auth = {"$env": "AUDIT_SECRET"}
    gw._test_broker = _AllowAllBroker()
    return gw


class _Ctx:
    """Minimal ToolContext carrying a permissive broker."""

    def __init__(self, broker):
        self.permissions = broker
        self.audit = lambda event, data: None


@pytest.fixture()
def gateway(recordings, monkeypatch, tmp_path):
    seen, port = recordings
    return _gateway(tmp_path, port, monkeypatch), seen, port


def test_integration_with_a_secret_refuses_the_redirect(gateway):
    from brain.agent.integrations import IntegrationError

    gw, seen, _ = gateway
    with pytest.raises(IntegrationError) as info:
        gw.call("audit", "ping", ctx=_Ctx(gw._test_broker))
    assert "redirect" in str(info.value).lower()
    assert [p for p, _, _ in seen] == ["/start/integration"]


def test_redirect_guard_checks_the_destination_host(gateway):
    """No credential: following is allowed, but the landing URL is re-checked."""
    from brain.agent.integrations import IntegrationError

    gw, _, _ = gateway
    integ = gw.integrations["audit"]
    integ.allow_hosts = ["127.0.0.1"]

    class _Resp:
        def __init__(self, url):
            self.url = url
            self._url = url

        def geturl(self):
            return self._url

    # same host: fine
    gw._redirect_guard(integ, _Resp("http://127.0.0.1:1/landing"), "http://127.0.0.1:1/start")
    # different host: refused even though the original URL was allow-listed
    with pytest.raises(IntegrationError):
        gw._redirect_guard(integ, _Resp("http://evil.example/landing"),
                           "http://127.0.0.1:1/start")


def test_credential_free_calls_still_follow_redirects(recordings):
    """A health-style GET with no headers may follow; the guard catches the rest."""
    from brain.util.http import open_follow

    seen, port = recordings
    req = urllib.request.Request(f"http://127.0.0.1:{port}/start/plain")
    with open_follow(req, timeout=3) as resp:
        assert resp.status == 200
    assert [p for p, _, _ in seen] == ["/start/plain", "/landing"]
