"""Dashboard (brain serve): same-origin UI, allowlisted names-only files, single-use consent."""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from brain.api import dashboard
from brain.api.gateway import Gateway, _Handler

TOKEN = "t" * 24


@pytest.fixture()
def srv(config, vault, memory, tmp_path):
    docs = tmp_path / "Documents"; (docs / "sub").mkdir(parents=True); (docs / "a.txt").write_text("x")
    (docs / ".ssh").mkdir(); (docs / ".hidden").write_text("x")
    (tmp_path / "secret.txt").write_text("outside")
    try:
        (docs / "link").symlink_to(tmp_path / "secret.txt")
    except OSError:                      # Windows without Developer Mode cannot create symlinks
        pass
    config.agent["dashboard_roots"] = str(docs)
    gw = Gateway(config, vault, memory, token=TOKEN)
    _Handler.gateway = gw
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd, gw, docs
    httpd.shutdown(); httpd.server_close()


def call(httpd, path, body=None, token=TOKEN, host=None):
    r = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}{path}",
                               data=json.dumps(body).encode() if body is not None else None)
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    r.add_header("Host", host or f"127.0.0.1:{httpd.server_port}")
    try:
        with urllib.request.urlopen(r) as x:
            return x.status, dict(x.headers), x.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def jcall(*a, **k):
    s, h, b = call(*a, **k)
    return s, json.loads(b or b"{}")


def test_page_served_with_strict_csp_and_no_inline_code(srv):
    s, h, b = call(srv[0], "/", token=None)
    assert s == 200 and b"<script src=\"/dashboard.js\">" in b
    csp = h["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "frame-ancestors 'none'" in csp
    assert h["X-Frame-Options"] == "DENY" and h["Cache-Control"] == "no-store"
    html = (dashboard.STATIC / "dashboard.html").read_text()
    assert not re.search(r"\s(style|on\w+)\s*=", html) and "<style" not in html
    for name in ("dashboard.js", "dashboard.css"):
        text = (dashboard.STATIC / name).read_text()
        assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(", text)
    assert h["Permissions-Policy"].startswith("microphone=()")


def test_rebinding_host_refused_everywhere(srv):
    assert call(srv[0], "/", token=None, host="evil.example")[0] == 403
    assert call(srv[0], "/v1/metrics", host="evil.example")[0] == 403


def test_api_still_needs_token(srv):
    assert call(srv[0], "/v1/metrics", token=None)[0] == 401
    assert call(srv[0], "/v1/files", token="bad")[0] == 401


def test_metrics_shape(srv):
    s, j = jcall(srv[0], "/v1/metrics")
    assert s == 200 and "disk_pct" in j["metrics"] and "platform" in j["metrics"]


def test_files_names_only_allowlist(srv):
    httpd, gw, docs = srv
    s, top = jcall(httpd, "/v1/files")
    assert s == 200 and [e["path"] for e in top["entries"]] == [str(docs.resolve())]
    s, j = jcall(httpd, f"/v1/files?path={docs}")
    names = {e["name"] for e in j["entries"]}
    assert s == 200 and {"sub", "a.txt"} <= names
    assert ".ssh" not in names and ".hidden" not in names and "link" not in names
    assert all("content" not in e and "body" not in e for e in j["entries"])


def test_files_refuses_outside_and_credential_paths(srv):
    httpd, gw, docs = srv
    assert call(httpd, "/v1/files?path=/etc")[0] == 403
    assert call(httpd, f"/v1/files?path={docs}/../")[0] == 403
    assert call(httpd, f"/v1/files?path={docs}/.ssh")[0] == 403
    assert call(httpd, "/v1/read?path=/etc/passwd")[0] == 404


def test_consent_single_use_and_never_standing(srv):
    httpd, gw, docs = srv
    d = gw.brain.permissions.request("fs.list", str(docs), "test")
    pid = d.prompt_id
    s, j = jcall(httpd, "/v1/consent")
    assert s == 200 and [p["id"] for p in j["pending"]] == [pid]
    s, j = jcall(httpd, "/v1/consent/affirm", {"id": pid, "remember": True})
    assert s == 200 and j["decision"]["allowed"]
    kinds = [g.kind for g in gw.brain.permissions.grants()]
    assert kinds == ["one_shot"]
    assert jcall(httpd, "/v1/consent")[1]["pending"] == []


def test_consent_refused_in_degraded_session(srv):
    httpd, gw, docs = srv
    pid = gw.brain.permissions.request("fs.list", str(docs), "t").prompt_id
    gw.vault.session.degraded = True
    assert call(httpd, "/v1/consent/affirm", {"id": pid})[0] == 403
    assert gw.brain.permissions.grants() == []


def test_deny_is_not_standing(srv):
    httpd, gw, docs = srv
    pid = gw.brain.permissions.request("fs.list", str(docs), "t").prompt_id
    s, j = jcall(httpd, "/v1/consent/deny", {"id": pid})
    assert s == 200 and not j["decision"]["allowed"]
    assert [g for g in gw.brain.permissions.grants() if g.kind == "denied"] == []


def test_every_dashboard_route_needs_the_token(srv):
    httpd = srv[0]
    for path, body in (("/v1/consent", None), ("/v1/consent/affirm", {"id": "x"}),
                       ("/v1/consent/deny", {"id": "x"}), ("/v1/files", None)):
        assert call(httpd, path, body, token=None)[0] == 401, path
        assert call(httpd, path, body, token="wrong")[0] == 401, path


def test_files_refused_in_degraded_session(srv):
    httpd, gw, docs = srv
    gw.vault.session.degraded = True
    assert call(httpd, "/v1/files")[0] == 403


def test_listing_is_capped(tmp_path):
    for i in range(dashboard.MAX_ENTRIES + 25):
        (tmp_path / f"f{i:04}.txt").write_text("x")
    out = dashboard.list_names([tmp_path.resolve()], str(tmp_path), lambda p: None)
    assert len(out["entries"]) == dashboard.MAX_ENTRIES


def test_forbidden_is_the_projects_shared_list():
    from brain.agent import host_tools
    assert dashboard.list_names.__module__ == "brain.api.dashboard"
    assert host_tools.forbidden_reason is host_tools._forbidden


def test_missing_roots_are_explained_not_silent(srv):
    httpd, gw, docs = srv
    gw.config.agent["dashboard_roots"] = f"{docs},{docs}/Nope"
    s, j = jcall(httpd, "/v1/files")
    assert s == 200 and len(j["entries"]) == 1
    assert [r["exists"] for r in j["roots"]] == [True, False]
    gw.config.agent["dashboard_roots"] = f"{docs}/Nope"
    s, j = jcall(httpd, "/v1/files")
    assert s == 200 and j["entries"] == [] and j["roots"][0]["exists"] is False


def test_dashboard_js_tells_the_owner_how_to_fix_empty_roots():
    js = (dashboard.STATIC / "dashboard.js").read_text()
    assert "folder not found" in js and "agent.dashboard_roots" in js


def test_dashboard_js_shows_session_countdown_and_expiry_hint():
    js = (dashboard.STATIC / "dashboard.js").read_text()
    assert "expires in" in js and "session expired" in js
    assert "absolute_expires_in_s" in js and "maximum" in js


def test_the_dashboard_offers_the_three_ways_out_of_a_dead_end():
    """Everything a first-time owner needs when a session ends or a key is
    refused, asserted on the shipped JS: without these the screen is a cul-de-sac."""
    js = (dashboard.STATIC / "dashboard.js").read_text()
    html = (dashboard.STATIC / "dashboard.html").read_text()
    # 1. the 30-minute warning and its one explicit click
    assert "/v1/session/touch" in js and "I am still here" in html
    # 2. unlock again in the browser instead of "restart Nila"
    assert "/v1/auth/verify" in js and "session_token" in js
    # 3. a clean stop, so ctrl-c does not leave the session in the identity file
    assert "/v1/lock" in js and "stopnila" in html


def test_the_connector_panel_shows_how_to_fix_a_refusal():
    js = (dashboard.STATIC / "dashboard.js").read_text()
    assert "e.body.how" in js and "brain totp" in js
