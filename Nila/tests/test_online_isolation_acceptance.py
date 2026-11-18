"""Independent acceptance tests for ONLINE ISOLATION.

Written against the *behaviour* (what an online provider can ever receive), not
against any implementation, so they can be run on any candidate fix:

    python -m pytest tests/test_online_isolation_acceptance.py -q

A mock cloud server stands in for Gemini. No real key, no real network.
Rules under test:
  1. An online turn sends no memory, project text, tool schemas or session data.
  2. A tool call the online model asks for is never executed, and nothing is
     sent back to the provider as a tool result.
  3. `Auto` (no explicit Online choice) never reaches the cloud, whatever the
     old settings say (auto_online, route_default, legacy provider).
  4. Messages that look private (identity, credentials, health, finance,
     family) never go online.
"""
from __future__ import annotations

import http.server
import json
import threading

import pytest

from brain.agent.agent import Brain
from brain.projects.store import ProjectStore

MARK = "ZEBRA-PRIVATE-MARKER-7Q"


class _Cloud:
    def __init__(self):
        self.requests = []          # raw request bodies (str)
        self.reply = "a public answer"
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8", "replace")
                outer.requests.append(body)
                payload = json.dumps({"candidates": [{"content": {"parts": [{"text": outer.reply}]}}],
                                      "usageMetadata": {"totalTokenCount": 5}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/v1beta"

    @property
    def blob(self):
        return "\n".join(self.requests)


@pytest.fixture()
def cloud(monkeypatch, config):
    c = _Cloud()
    monkeypatch.setenv("GEMINI_API_KEY", "TEST-KEY")
    config.agent.update({"online_base_url": c.url, "online_daily_requests": 1000, "online_rpm": 1000})
    yield c
    c.srv.shutdown()


@pytest.fixture()
def seeded(memory):
    memory.remember(f"zebra pricing plan {MARK}", title="plan")
    ProjectStore(memory).create("Zebra Venture", f"idea text {MARK}")


def _brain(config, vault, memory):
    return Brain(config, vault, memory)


# ---------------------------------------------------------------- 1. nothing private rides along
def test_online_turn_sends_no_private_state(config, vault, memory, cloud, seeded):
    turn = _brain(config, vault, memory).handle("tell me about zebra pricing", mode="online")
    assert cloud.requests, "the online turn must really have gone online (test would be vacuous otherwise)"
    assert turn.route["where"] == "online"
    blob = cloud.blob
    for forbidden in (MARK, "Zebra Venture", "recall", "remember", "project_create", "run_command",
                      "read_file", "update_memory"):
        assert forbidden not in blob, f"online request leaked or advertised: {forbidden}"


# ---------------------------------------------------------------- 2. unexpected tool calls
def test_unexpected_tool_call_is_not_executed_or_fed_back(config, vault, memory, cloud, seeded):
    cloud.reply = '```json\n{"tool": "recall", "args": {"query": "zebra"}}\n```'
    turn = _brain(config, vault, memory).handle("tell me about zebra pricing", mode="online")
    assert len(cloud.requests) == 1, "no second request: a tool result must never be sent back"
    assert MARK not in cloud.blob and MARK not in (turn.answer or "")
    assert not [c for c in turn.tool_calls if c.get("ok")], "no tool may have run"


# ---------------------------------------------------------------- 3. Auto never reaches the cloud
@pytest.mark.parametrize("settings", [
    {},
    {"auto_online": True},
    {"route_default": "online"},
    {"auto_online": True, "route_default": "online"},
])
@pytest.mark.parametrize("mode", [None, "auto"])
def test_auto_never_goes_online(config, vault, memory, cloud, seeded, settings, mode):
    config.agent.update(settings)
    turn = _brain(config, vault, memory).handle("hello, what can you do", mode=mode)
    assert cloud.requests == [], f"Auto reached the cloud with {settings}"
    assert turn.route["where"] != "online"


def test_legacy_provider_is_used_only_when_online_is_chosen(config, vault, memory, cloud, seeded):
    config.agent.update({"provider": "gemini", "base_url": cloud.url, "api_key_env": "GEMINI_API_KEY",
                         "model": "gemini-flash-latest"})
    _brain(config, vault, memory).handle("hello there")
    assert cloud.requests == [], "legacy provider config must not make Auto go online"
    _brain(config, vault, memory).handle("tell me about zebra pricing", mode="online")
    assert cloud.requests, "an explicit Online choice may use it"
    assert MARK not in cloud.blob and "recall" not in cloud.blob


# ---------------------------------------------------------------- 4. private-looking messages stay home
PRIVATE = {
    "card":            "my card number is 4111 1111 1111 1111",
    "aadhaar":         "my aadhaar is 2345 6789 0123",
    "password":        "my password = hunter2hunter2",
    "api key":         "key AIza" + "Ab1" * 11 + "Ab",
    "health en":       "I was diagnosed with diabetes and take insulin",
    "health ta":       "எனக்கு சர்க்கரை நோய் உள்ளது",
    "finance en":      "my salary is low and my loan debt is large",
    "finance ta":      "என் சம்பளம் குறைவு, கடன் அதிகம்",
    "finance tanglish": "en sambalam kammi, kadan athigam",
    "family en":       "my wife and I are fighting and her mother is sick",
    "family ta":       "என் மனைவிக்கும் எனக்கும் சண்டை",
    "family tanglish": "en manaivi kooda sandai, amma udambu sari illa",
}


@pytest.mark.parametrize("name", sorted(PRIVATE))
def test_private_looking_message_never_goes_online(config, vault, memory, cloud, name):
    turn = _brain(config, vault, memory).handle(PRIVATE[name], mode="online")
    assert cloud.requests == [], f"'{name}' message went online"
    assert turn.route.get("blocked") or turn.route["where"] != "online"
    assert turn.route.get("notice"), "the owner must be told why it stayed local"


def test_clean_message_does_go_online_so_the_blocks_are_not_vacuous(config, vault, memory, cloud):
    turn = _brain(config, vault, memory).handle("give me three names for a tea shop", mode="online")
    assert cloud.requests and turn.route["where"] == "online"
