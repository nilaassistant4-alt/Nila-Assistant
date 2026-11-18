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


def test_polling_is_split_and_pauses_when_the_tab_is_hidden():
    js = dashboard.asset("/dashboard.js")[0].decode()
    assert "setInterval(() => { if (!document.hidden) tick(); }, 5000)" in js
    assert "setInterval(() => { if (!document.hidden) slow(); }, 20000)" in js
    assert 'addEventListener("visibilitychange"' in js
    assert "setInterval(tick, 2000)" not in js
    assert "prefers-reduced-motion" in js
    html = (dashboard.STATIC / "dashboard.html").read_text()
    assert not re.search(r"\s(style|on\w+)\s*=", html) and "<style" not in html
    for name in ("dashboard.js", "dashboard.css"):
        text = (dashboard.STATIC / name).read_text()
        assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(", text)


def test_page_served_with_strict_csp_and_no_inline_code(srv):
    s, h, b = call(srv[0], "/", token=None)
    assert s == 200 and b"<script src=\"/dashboard.js\">" in b
    csp = h["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "frame-ancestors 'none'" in csp
    assert h["X-Frame-Options"] == "DENY" and h["Cache-Control"] == "no-cache"
    assert h["ETag"].startswith('"')
    assert h["Permissions-Policy"].startswith("microphone=()")


def test_repeat_visit_is_revalidated_not_re_downloaded(srv):
    """30 KB of script on every open was the old behaviour; a hash turns that
    into an empty 304."""
    s, h, b = call(srv[0], "/dashboard.js", token=None)
    assert s == 200 and len(b) > 10_000
    r = urllib.request.Request(f"http://127.0.0.1:{srv[0].server_port}/dashboard.js")
    r.add_header("If-None-Match", h["ETag"])
    r.add_header("Host", f"127.0.0.1:{srv[0].server_port}")
    try:
        with urllib.request.urlopen(r) as x:
            assert x.status == 304 and x.read() == b""
    except urllib.error.HTTPError as e:      # 304 arrives here for urllib
        assert e.code == 304 and e.read() == b""


def test_head_answers_with_the_same_status_and_no_body(srv):
    """base handler replied 501 to HEAD, which made health checks look dead."""
    r = urllib.request.Request(f"http://127.0.0.1:{srv[0].server_port}/v1/status", method="HEAD")
    r.add_header("Host", f"127.0.0.1:{srv[0].server_port}")
    try:
        with urllib.request.urlopen(r) as x:
            assert x.status == 401 and x.read() == b""
    except urllib.error.HTTPError as e:
        assert e.code == 401 and e.read() == b""
    r.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(r) as x:
        assert x.status == 200 and x.read() == b""


def test_every_panel_says_it_is_loading_before_its_answer_arrives(srv):
    """An empty box used to be indistinguishable from 'nothing found'."""
    _s, _h, b = call(srv[0], "/", token=None)
    page = b.decode()
    for box in ("bud", "cx", "instr", "late", "phone", "v2", "hl", "caps", "log", "files", "plist"):
        assert f'id="{box}"' in page, box
    assert page.count('class="skel"') >= 10


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
    """Two different problems: a folder that is configured but missing (named),
    and no folder configured at all (with the one command that adds one)."""
    js = (dashboard.STATIC / "dashboard.js").read_text()
    assert "folder not found" in js
    assert "config add-folder" in js


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


def test_the_hint_the_dashboard_gives_is_a_command_that_runs(tmp_path, monkeypatch):
    """Found by pasting the hint: it printed a `config set` with JSON quotes
    escaped for JavaScript (`[\\"…\\"]`), which reached config WITH the
    backslashes and left a folder that did not exist. The hint now names a
    command that takes a plain path, and that command is exercised here."""
    from brain.cli.main import build_parser

    js = (dashboard.STATIC / "dashboard.js").read_text()
    hint = [line for line in js.splitlines() if "add-folder" in line]
    assert hint, "the files hint disappeared"
    assert 'config set agent.dashboard_roots' not in js
    assert "\\\\" not in hint[0]           # no escaping left for the owner to undo

    # the named subcommand exists with the argument it shows
    parser = build_parser()
    args = parser.parse_args(["config", "add-folder", str(tmp_path)])
    assert args.action == "add-folder" and args.key == str(tmp_path)

    from brain.config import Config
    from brain.cli.main import _config_add_folder

    cfg = Config.load(tmp_path / "home")
    class UI:
        def __init__(self): self.said = []
        def ok(self, m): self.said.append(m)
        def err(self, m): self.said.append("ERR " + m); return 1
    ui = UI()
    assert _config_add_folder(cfg, str(tmp_path), ui) == 0
    again = Config.load(tmp_path / "home")
    assert str(tmp_path.resolve()) in str(again.agent.get("dashboard_roots"))
    assert _config_add_folder(again, str(tmp_path), ui) == 0      # idempotent
    assert "already shared" in ui.said[-1]
    assert _config_add_folder(again, str(tmp_path / "missing"), ui) == 1
    assert "not a folder" in ui.said[-1]
