"""Acceptance tests: the model pack (small brain for small jobs, big for big).

The owner's words: "connect the models in the background, ask only when needed,
small answers for small things, the big workbench only for the big work".

Run:  python -m pytest tests/test_model_pack_acceptance.py -q
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from brain.agent import models as pack


# --------------------------------------------------------------------------- #
# the registry itself
# --------------------------------------------------------------------------- #
def test_every_model_says_what_it_costs_and_what_it_weighs():
    for model_id, spec in pack.PACK.items():
        for field in ("label", "kind", "runtime", "tag", "weight_gb", "load",
                      "tamil", "jobs", "free", "needs_net", "why", "install"):
            assert field in spec, f"{model_id} is missing {field}"
        assert spec["kind"] in ("local", "online")
        assert isinstance(spec["free"], bool)
        # A local model is free by definition; a check stops a mistake here
        # becoming a wrong bill or a wrong privacy claim.
        if spec["kind"] == "local":
            assert spec["free"] is True and spec["needs_net"] is False


def test_the_owner_s_models_are_in_the_pack():
    ids = set(pack.PACK)
    for wanted in ("qwen2.5:3b", "qwen2.5:7b", "llama3.2:3b", "phi3.5:mini",
                   "whisper-small", "piper-ta"):
        assert wanted in ids, wanted


def test_nothing_is_downloaded_or_installed_by_the_pack():
    """The pack DESCRIBES models; it never fetches one.

    "install" strings are advice for the owner to run, so the check is about
    behaviour, not text: no process is started and no file is written.
    """
    src = open("brain/agent/models.py", encoding="utf-8").read()
    for forbidden in ("subprocess", "os.system", "urlretrieve", "os.exec"):
        assert forbidden not in src, forbidden
    # and the only network call it makes is a localhost Ollama probe
    assert "127.0.0.1:11434" in src


# --------------------------------------------------------------------------- #
# choosing a brain
# --------------------------------------------------------------------------- #
def test_a_small_job_uses_the_small_brain():
    ch = pack.choose("small", ["builtin", "qwen2.5:3b", "qwen2.5:7b"])
    assert ch["ok"] and ch["model"] == "builtin"          # cheapest first


def test_a_big_job_prefers_the_big_local_brain():
    ch = pack.choose("big", ["builtin", "qwen2.5:3b", "qwen2.5:7b"])
    assert ch["model"] == "qwen2.5:7b"


def test_a_model_that_is_not_present_is_never_chosen():
    ch = pack.choose("big", ["builtin"])
    assert ch["ok"] is False and ch["model"] is None
    assert any("not present" in s["why"] for s in ch["skipped"])


def test_cloud_is_only_used_when_it_is_allowed():
    present = ["builtin", "qwen2.5:7b", "gemini-flash"]
    assert pack.choose("big", present, allow_cloud=True)["model"] == "qwen2.5:7b"
    assert pack.choose("big", ["gemini-flash"], allow_cloud=False)["ok"] is False


def test_free_only_mode_never_picks_a_paid_model():
    ch = pack.choose("big", ["gemini-pro", "claude"], free_only=True)
    assert ch["ok"] is False
    assert all("paid" in s["why"] or "not present" in s["why"] for s in ch["skipped"])


def test_an_unknown_job_is_refused_with_the_known_ones():
    ch = pack.choose("paint_the_house", ["builtin"])
    assert ch["ok"] is False and "small" in ch["reason"]


# --------------------------------------------------------------------------- #
# detecting what is really here
# --------------------------------------------------------------------------- #
class _Ollama(BaseHTTPRequestHandler):
    models = ["qwen2.5:3b", "llama3.2:3b", "phi3.5:mini"]

    def do_GET(self):
        body = json.dumps({"models": [{"name": m} for m in self.models]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture()
def ollama():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Ollama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_a_running_ollama_with_the_model_reports_it_present(ollama):
    m = pack.installed(ollama_url=ollama)
    assert m["ollama_running"] is True
    assert "qwen2.5:3b" in m["present"] and "llama3.2:3b" in m["present"]
    assert "qwen2.5:7b" not in m["present"]
    row = next(r for r in m["models"] if r["id"] == "qwen2.5:7b")
    assert "pull" in row["why_not"]


def test_tags_match_their_quantised_names(ollama):
    _Ollama.models = ["qwen2.5:3b-instruct-q4_K_M"]
    try:
        m = pack.installed(ollama_url=ollama)
        assert "qwen2.5:3b" in m["present"]
    finally:
        _Ollama.models = ["qwen2.5:3b", "llama3.2:3b", "phi3.5:mini"]


def test_no_ollama_is_reported_as_not_running_not_as_empty():
    m = pack.installed(ollama_url="http://127.0.0.1:9")     # nothing listens here
    assert m["ollama_running"] is False
    assert m["present"] == ["builtin"]
    assert any("not running" in r["why_not"] for r in m["models"])


def test_probe_false_needs_no_network_at_all():
    m = pack.installed(probe=False)
    assert m["present"] == ["builtin"] and m["ollama_running"] is False


def test_the_status_screen_explains_what_to_add_and_why(ollama):
    status = pack.pack_status(pack.installed(ollama_url=ollama), ollama_url=ollama)
    assert status["ok"] is True
    ids = [a["id"] for a in status["advice"]]
    assert "whisper-small" in ids and "piper-ta" in ids
    assert all(a["install"] for a in status["advice"])
    assert "downloaded or installed by Nila" in status["note"]


# --------------------------------------------------------------------------- #
# the router's small/big decision
# --------------------------------------------------------------------------- #
@pytest.fixture()
def router(config):
    from brain.agent.ledger import UsageLedger
    from brain.agent.router import Router

    return Router(config, UsageLedger(config.home / "ledger.json"))


@pytest.mark.parametrize("text", [
    "hi", "what is 2+2", "what time is it", "remember milk", "வணக்கம்",
])
def test_short_lookups_are_small_jobs(router, text):
    assert router.job_class(text) == "small"


@pytest.mark.parametrize("text", [
    "plan my week around the shop and two appointments",
    "compare three suppliers step by step",
    "explain in detail how a loan works",
    "என் வாரத்திற்கு ஒரு திட்டம் தயார் செய்",
    "first, summarise the notes\nsecond, draft a reply\nthird, save it",
])
def test_real_work_is_a_big_job(router, text):
    assert router.job_class(text) == "big"


def test_the_pack_plan_never_names_a_brain_that_is_not_here(router):
    plan = router.pack_plan("plan my week")
    assert plan["job"] == "big"
    assert plan["choice"]["ok"] is False or plan["choice"]["model"] in plan["present"]
    assert "builtin" in plan["present"]


def test_a_reply_says_which_brain_answered_and_whether_it_was_free(config, vault, memory):
    from brain.agent.agent import Brain

    turn = Brain(config, vault, memory).handle("hi")
    assert "route" in turn.to_dict()
    # offline turns are answered by the built-in engine, which the pack knows
    assert turn.route["where"] in ("offline", "local")


# --------------------------------------------------------------------------- #
# over HTTP
# --------------------------------------------------------------------------- #
@pytest.fixture()
def srv(config, vault, memory):
    from brain.api.gateway import Gateway, _Handler

    gw = Gateway(config, vault, memory, token="t" * 24)
    _Handler.gateway = gw
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def call(httpd, path, body=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{httpd.server_port}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + "t" * 24,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def test_the_models_screen_over_http(srv):
    s, d = call(srv, "/v1/models")
    assert s == 200 and d["ok"] is True
    assert "present" in d and "advice" in d and "plan" in d


def test_choosing_over_http_never_returns_an_absent_model(srv):
    s, d = call(srv, "/v1/models/choose", {"job": "big", "allow_cloud": False})
    assert s == 200
    assert d["ok"] is False or d["model"] in [r["id"] for r in call(srv, "/v1/models")[1]["present"]]
