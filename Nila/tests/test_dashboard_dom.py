"""The dashboard, actually executed: every panel must leave its loading state.

Static checks cannot see this class of bug. Two were found only by running the
page in a real DOM against the real server:

* a refused 2D canvas context threw at the top of `dashboard.js` and **every**
  panel stayed on "loading" - the animation is decoration and must never be able
  to break the page;
* the connectors and budget panels drew their contents *inside* the loading
  placeholder, so a panel that had already answered still read "checking...".

This test runs the page with jsdom when node and jsdom are available (the build
images install them for exactly this), and skips - with a reason - when they are
not, so a machine without node is not failed for a missing tool.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from brain.api.gateway import Gateway, _Handler

TOKEN = "t" * 24
SCRIPT = Path(__file__).with_name("_dom_check.js")


def _jsdom_path() -> str:
    for base in ("/tmp/jsd/node_modules", str(Path.home() / "node_modules"),
                 str(Path.cwd() / "node_modules")):
        if (Path(base) / "jsdom").is_dir():
            return f"{base}/jsdom"
    return ""


@pytest.fixture()
def srv(config, vault, memory, tmp_path):
    docs = tmp_path / "Documents"; docs.mkdir()
    config.agent["dashboard_roots"] = str(docs)
    gw = Gateway(config, vault, memory, token=TOKEN)
    _Handler.gateway = gw
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown(); httpd.server_close()


def test_every_panel_leaves_its_loading_state(srv):
    node = shutil.which("node")
    jsdom = _jsdom_path()
    if not node or not jsdom:
        pytest.skip("node + jsdom are not installed here; the page was run by hand")
    assert SCRIPT.exists()
    proc = subprocess.run(
        [node, str(SCRIPT), f"http://127.0.0.1:{srv.server_port}", TOKEN],
        capture_output=True, text=True, timeout=120,
        env={"JSDOM_PATH": jsdom, "PATH": "/usr/bin:/bin:/usr/local/bin"})
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["stuck"] == [], f"panels still show a loading placeholder: {out['stuck']}"
    assert out["errors"] == [], out["errors"]
    assert out["panels_ok"] is True
    assert out["week_cells"] == 7          # the habit strip is drawn
    assert out["capability_rows"] > 0
    # the audit panel must have been REPLACED by its answer, not still loading:
    # a fresh vault may have nothing to list, which is also a real answer.
    assert "reading the audit log" not in (out.get("log_text") or "")
    # stage 12: the calendar and the other PC are answered by the server too, and
    # "no calendar set" / "not joined" are real answers, not placeholders
    assert "reading the calendar" not in (out.get("calendar_text") or "")
    assert "checking the other PC" not in (out.get("sync_text") or "")
    assert "brain calendar --set" in (out.get("calendar_text") or "")
    assert "sync-join.bat" in (out.get("sync_text") or "")
