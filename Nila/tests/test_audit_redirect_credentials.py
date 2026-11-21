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


# --------------------------------------------------------------------------- #
# the same rule for a credential-FREE fetch: no secret to steal, but no reason to
# let weather/Wikipedia/YouTube/RSS bounce Nila to a private or local address.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8790/v1/blobs",
    "http://localhost:8790/",
    "http://169.254.169.254/latest/meta-data/",       # cloud metadata
    "http://10.0.0.5/admin",
    "http://192.168.1.1/",
    "http://[::1]:8791/",
    "file:///etc/passwd",
    "ftp://example.com/x",
    "https://intranet",                                # a single-label LAN name
])
def test_a_public_fetch_may_not_be_aimed_at_a_private_address(url):
    from brain.util.http import is_public_url

    assert is_public_url(url) is False, url


@pytest.mark.parametrize("url", [
    "https://api.open-meteo.com/v1/forecast",
    "https://ta.wikipedia.org/api/rest_v1/page/summary/X",
    "https://www.youtube.com/watch?v=abc",
    "http://example.com/feed.xml",
])
def test_ordinary_public_fetches_are_allowed(url):
    from brain.util.http import is_public_url

    assert is_public_url(url) is True, url


def test_a_redirect_to_a_private_address_is_refused_before_any_second_request():
    """The destination is checked, not merely the declared URL: a public host that
    302s to 127.0.0.1 must not make Nila call a local service."""
    from brain.util.http import RedirectRefused, _PublicRedirects
    import urllib.request

    handler = _PublicRedirects()
    req = urllib.request.Request("https://example.com/start")
    with pytest.raises(RedirectRefused) as exc:
        handler.redirect_request(req, None, 302, "Found",
                                 {"Location": "http://127.0.0.1:8790/v1/blobs"},
                                 "http://127.0.0.1:8790/v1/blobs")
    assert "127.0.0.1" in str(exc.value)


# --------------------------------------------------------------------------- #
# Disguised IPv4: the forms the C resolver accepts and ``ipaddress`` rejects.
#
# Idea taken from OpenJarvis (Apache-2.0), security/ssrf.py::_disguised_ipv4 --
# https://github.com/open-jarvis/OpenJarvis -- and re-written here for Nila's
# redirect guard. Found by reading their code: our own check said "public" for
# ``http://127.1/``, which the operating system resolves to 127.0.0.1.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("url", [
    "http://0x7f.0.0.1/x",          # dotted hex
    "http://0x7f000001/x",          # packed hex
    "http://0177.0.0.1/x",          # dotted octal
    "http://127.1/x",               # short-dotted loopback
    "http://127.0.1/x",
    "http://0/x",                   # "0" is 0.0.0.0
    "http://2130706433/x",          # decimal
])
def test_a_disguised_loopback_is_refused_like_the_real_one(url):
    from brain.util.http import is_public_url

    assert is_public_url(url) is False, url


def test_a_disguised_private_host_is_refused_as_a_redirect_destination():
    """The same hole through the other door: a public feed 302s to ``127.1``."""
    import urllib.request

    from brain.util.http import RedirectRefused, _PublicRedirects

    handler = _PublicRedirects()
    req = urllib.request.Request("https://example.com/start")
    with pytest.raises(RedirectRefused):
        handler.redirect_request(req, None, 302, "Found",
                                 {"Location": "http://127.1:8790/v1/chat"},
                                 "http://127.1:8790/v1/chat")


def test_a_public_dotted_quad_is_still_allowed_after_normalisation():
    """The fix must not start refusing ordinary public IPv4 hosts."""
    from brain.util.http import is_public_url

    assert is_public_url("http://93.184.216.34/index.html") is True
