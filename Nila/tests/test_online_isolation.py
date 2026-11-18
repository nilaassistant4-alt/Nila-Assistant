"""Online turns are public-message-only and never receive vault context/tools."""
from __future__ import annotations

import copy
import json
import os
from contextlib import contextmanager

import pytest

from brain.agent.agent import Brain
from brain.agent.providers import Completion, LLMProvider, OllamaProvider
from brain.agent.router import Router
from brain.agent.ledger import UsageLedger
from brain.security.classify import CLASS_RESTRICTED, CLASS_SENSITIVE
from brain.security.redact import scan


FINANCE_AND_FAMILY = [
    ("My salary is 90000", "finance"),
    ("என் சம்பளம் 90000", "finance"),
    ("en sambalam 90000", "finance"),
    ("My daughter is moving next month", "family"),
    ("என் மகள் அடுத்த மாதம் செல்கிறாள்", "family"),
    ("en magal adutha maasam pora", "family"),
]


@contextmanager
def _environment(name: str, value: str):
    old = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old


class _CaptureProvider(LLMProvider):
    name = "fake-online"
    needs_network = True

    def __init__(self, tool_call=None):
        self.calls = []
        self.tool_call = tool_call

    def available(self):
        return True

    def complete(self, system, messages, tools=None, tools_mode="auto"):
        self.calls.append({"system": system,
                           "messages": copy.deepcopy(messages),
                           "tools": copy.deepcopy(tools)})
        calls = [self.tool_call] if self.tool_call else []
        return Completion(text="A public answer.", tool_calls=calls,
                          model="fake-online", provider=self.name)


def test_local_ollama_transport_is_not_treated_as_cloud_egress():
    assert OllamaProvider().needs_network is False


@pytest.mark.parametrize("text,tag", FINANCE_AND_FAMILY)
def test_finance_and_family_lexicons_cover_english_tamil_tanglish(text, tag):
    result = scan(text)
    assert tag in result.keyword_tags
    assert result.max_class in (CLASS_SENSITIVE, CLASS_RESTRICTED)


@pytest.mark.parametrize("text", [row[0] for row in FINANCE_AND_FAMILY])
def test_finance_and_family_messages_are_refused_for_online(text, config, tmp_path):
    key_env = "NILA_TEST_ONLINE_KEY"
    config.agent.update({"online_provider": "gemini", "online_key_env": key_env})
    with _environment(key_env, "synthetic-key-not-used"):
        router = Router(config, UsageLedger(tmp_path / "usage.json"))
        plan = router.plan(text, "online")
    assert plan.where != "online"
    assert plan.blocked is True
    assert "private" in plan.notice.lower()


def test_private_notice_takes_priority_even_when_online_key_is_missing(config, tmp_path):
    key_env = "NILA_TEST_MISSING_ONLINE_KEY"
    config.agent.update({"online_provider": "gemini", "online_key_env": key_env})
    with _environment(key_env, ""):
        plan = Router(config, UsageLedger(tmp_path / "usage.json")).plan(
            "My salary is 90000", "online")
    assert plan.where != "online" and plan.blocked is True
    assert "private" in plan.notice.lower()


def test_private_user_message_gets_local_only_notice_and_never_calls_online(
        config, vault, memory):
    from brain.agent import router as router_module

    fake = _CaptureProvider()
    real_build_provider = router_module.build_provider

    def build(cfg):
        if str(cfg.get("type", "")).lower() == "gemini":
            return fake
        return real_build_provider(cfg)

    router_module.build_provider = build
    try:
        config.agent.update({"online_provider": "gemini"})
        brain = Brain(config, vault, memory)
        turn = brain.handle("My salary is 90000", mode="online")
    finally:
        router_module.build_provider = real_build_provider

    assert fake.calls == []
    assert turn.route["where"] in ("local", "offline")
    assert turn.route["blocked"] is True
    assert "private" in turn.route["notice"].lower()


def test_injected_cloud_provider_cannot_bypass_auto(config, vault, memory):
    spy = _CaptureProvider()
    turn = Brain(config, vault, memory, provider=spy).handle(
        "Give me three public cafe names")
    assert spy.calls == []
    assert turn.route["where"] in ("local", "offline")


def test_auto_never_uses_cloud_even_if_old_auto_online_setting_is_true(
        config, tmp_path):
    key_env = "NILA_TEST_ONLINE_KEY"
    config.agent.update({"online_provider": "gemini", "online_key_env": key_env,
                         "auto_online": True})
    with _environment(key_env, "synthetic-key-not-used"):
        plan = Router(config, UsageLedger(tmp_path / "usage.json")).plan(
            "Give me three public cafe names", "auto")
    assert plan.where != "online"


def test_persisted_online_default_is_ignored_for_safety(config, tmp_path):
    key_env = "NILA_TEST_ONLINE_KEY"
    config.agent.update({"online_provider": "gemini", "online_key_env": key_env,
                         "route_default": "online", "auto_online": True})
    with _environment(key_env, "synthetic-key-not-used"):
        router = Router(config, UsageLedger(tmp_path / "usage.json"))
        plan = router.plan("Give me three public cafe names")
    assert router.default_mode() == "auto"
    assert plan.where != "online"


def test_legacy_cloud_provider_is_routed_only_when_online_is_selected(
        config, tmp_path):
    key_env = "NILA_TEST_LEGACY_GEMINI_KEY"
    config.agent.update({"provider": "gemini", "api_key_env": key_env,
                         "model": "gemini-test-model"})
    with _environment(key_env, "synthetic-key-not-used"):
        router = Router(config, UsageLedger(tmp_path / "usage.json"))
        automatic = router.plan("Give me three public cafe names", "auto")
        explicit = router.plan("Give me three public cafe names", "online")
    assert automatic.where != "online"
    assert explicit.where == "online"
    assert explicit.provider.name == "gemini"
    assert explicit.provider.model == "gemini-test-model"


def test_legacy_provider_config_only_calls_fake_cloud_after_explicit_online(
        config, vault, memory):
    from brain.agent import router as router_module

    key_env = "NILA_TEST_LEGACY_GEMINI_KEY"
    config.agent.update({"provider": "gemini", "api_key_env": key_env,
                         "model": "gemini-legacy-test"})
    fake = _CaptureProvider()
    real_build_provider = router_module.build_provider

    def build(cfg):
        if str(cfg.get("type", "")).lower() == "gemini":
            return fake
        return real_build_provider(cfg)

    router_module.build_provider = build
    try:
        brain = Brain(config, vault, memory)
        auto_turn = brain.handle("Give me three public cafe names")
        assert fake.calls == []
        assert auto_turn.route["where"] in ("local", "offline")
        online_turn = brain.handle("Give me three public cafe names", mode="online")
    finally:
        router_module.build_provider = real_build_provider

    assert online_turn.route["where"] == "online"
    assert len(fake.calls) == 1
    assert fake.calls[0]["messages"] == [
        {"role": "user", "content": "Give me three public cafe names"}]


def test_online_turn_does_not_search_memory_or_read_vault_stats(config, vault, memory):
    marker = "PRIVATE_ONLINE_CANARY_71d3"
    memory.remember(f"my family salary note {marker}", title="canary")
    from brain.projects.store import ProjectStore
    ProjectStore(memory).create("Canary project", f"project-only marker {marker}")
    config.agent["allow_cloud_context"] = True  # legacy consent must not widen Online
    spy = _CaptureProvider()
    brain = Brain(config, vault, memory, provider=spy)

    def forbidden(*args, **kwargs):
        raise AssertionError("online turn touched saved memory or vault statistics")

    old_search, old_stats = memory.search, memory.stats
    memory.search = forbidden
    memory.stats = forbidden
    try:
        turn = brain.handle("Suggest a name for a public cafe", use_tools=True,
                            mode="online")
    finally:
        memory.search, memory.stats = old_search, old_stats

    assert turn.ok
    assert turn.memories == []
    assert len(spy.calls) == 1
    assert spy.calls[0]["messages"] == [
        {"role": "user", "content": "Suggest a name for a public cafe"}]
    payload = json.dumps(spy.calls[0], ensure_ascii=False)
    assert marker not in payload
    assert "Canary project" not in payload
    assert "password" not in payload.lower()
    assert spy.calls[0]["tools"] is None
    assert "Vault:" not in spy.calls[0]["system"]
    assert "Session:" not in spy.calls[0]["system"]
    assert "tester" not in payload
    assert "memory and tools are disabled" in spy.calls[0]["system"].lower()


def test_online_provider_cannot_execute_or_forward_a_tool_result(config, vault, memory):
    marker = "FORBIDDEN_TOOL_RESULT_83a2"
    before = memory.stats()["records"]
    spy = _CaptureProvider({"id": "untrusted-call", "name": "remember",
                            "args": {"text": marker}})
    brain = Brain(config, vault, memory, provider=spy)
    turn = brain.handle("Answer this public question", use_tools=True, mode="online")

    assert len(spy.calls) == 1, "the tool result must never trigger a follow-up request"
    assert spy.calls[0]["tools"] is None
    assert memory.stats()["records"] == before, "the model must not store a memory"
    assert marker not in json.dumps(spy.calls[0], ensure_ascii=False)
    assert marker not in turn.answer
    assert turn.tool_calls == []
    assert marker not in str(vault.read_audit(20))
    assert turn.memories == []
    assert "disabled" in turn.answer.lower()
