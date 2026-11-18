"""Acceptance tests for #3 — the connectors screen.

What the owner asked for, in their words, and where each item is checked here:

1. ``/v1/connectors`` lists the AI providers (Ollama, Gemini, an
   OpenAI-compatible endpoint, Anthropic) and the outside services, with name,
   ON/OFF, whether a key exists, the allowed hosts, and today's usage.
   -> ``test_the_screen_lists_every_ai_provider``,
      ``test_each_row_carries_hosts_and_today_usage``
2. **The key is never shown.** Only "a key exists", the last four characters
   and a fingerprint. -> ``test_no_response_ever_contains_a_secret`` (walks the
   whole JSON tree and the raw bytes), ``test_the_markdown_screen_has_no_secret``
3. ON/OFF requires a **fresh login check**, and that approval is **single use**.
   -> ``test_switching_requires_an_approval``,
      ``test_an_approval_is_spent_by_one_action``,
      ``test_an_approval_from_another_session_does_not_count``,
      ``test_a_stale_session_cannot_approve``
4. **No payment / bank / card connectors.** -> ``test_there_is_no_payment_connector``
5. A **Test** button sends one **small fixed** prompt and is **counted against
   the budget**. -> ``test_a_test_call_is_counted_against_the_budget``,
   ``test_the_test_prompt_cannot_be_replaced``,
   ``test_a_test_call_is_refused_when_the_budget_is_spent``
6. The Gemini key is **entered hidden and stored in the vault**, never pasted
   into a browser form as a readable value and never as a process argument.
   -> ``test_a_key_can_only_be_written_by_an_authenticator_session``,
      ``test_a_key_is_stored_sealed``, ``test_a_key_never_lands_in_a_plaintext_file``
7. ChatGPT / Claude cost money, so they are shown as **optional and OFF**.
   -> ``test_paid_providers_default_to_off``

Everything here uses a real vault, a real ledger and a real audit chain; only
the HTTP calls to the providers are stubbed.
"""
from __future__ import annotations

import json
import time

import pytest

from conftest import PASSPHRASE

SECRET = "AIzaSyTEST_key_that_must_never_appear_0001"
SECRET2 = "sk-ant-TEST_key_that_must_never_appear_0002"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
@pytest.fixture()
def totp_secret(vault):
    """Enrol an authenticator, so a session can have the strong factor."""
    from brain.identity.ceremony import IdentityCeremony

    return IdentityCeremony(vault.config, vault).enroll_totp("tester")["secret"]


@pytest.fixture()
def strong(config, vault, memory, totp_secret):
    """A session whose second factor was the authenticator (not a voiceprint)."""
    from brain.identity.ceremony import IdentityCeremony
    from brain.identity.totp import totp

    vault.lock()
    result = IdentityCeremony(config, vault).unlock(
        PASSPHRASE, mode="totp", totp_code=totp(totp_secret))
    assert result.ok, result.reasons
    from brain.agent.connectors import ConnectorRegistry
    from brain.agent.agent import Brain

    brain = Brain(config, vault, brain_memory := memory)
    registry = ConnectorRegistry(config, vault, brain, brain_memory)
    # A dashboard session is long-lived: by the time an owner clicks a switch,
    # the login is minutes or hours old. The test starts from that state, so the
    # "fresh login check" has to be earned rather than inherited.
    vault.session.started_at -= registry._ceremony().FRESH_SESSION_MS + 5_000
    return registry


@pytest.fixture()
def weak(config, vault, memory, totp_secret):
    """A degraded session: the authenticator exists but was not given."""
    from brain.agent.agent import Brain
    from brain.agent.connectors import ConnectorRegistry

    vault.lock()
    mk = vault.unlock(PASSPHRASE)
    vault.start_session(mk, mode="passphrase", degraded=True)
    brain = Brain(config, vault, memory)
    return ConnectorRegistry(config, vault, brain, memory)


def rewind_totp(vault):
    """Test shortcut: forget which authenticator step was spent.

    A real user waits 30 seconds between logins; a test cannot. The one-time
    rule itself is checked by ``test_a_used_code_cannot_approve_twice``, which
    does NOT rewind.
    """

    def apply(identity):
        identity.setdefault("totp", {})["last_counter"] = 0

    vault._mutate_identity(apply)


def approve(registry, action, uses=1):
    """Approve an action the way a local command does: one fresh ceremony."""
    from brain.identity.totp import totp

    rewind_totp(registry.vault)
    return registry.authorise(action, PASSPHRASE,
                              totp(registry.vault.load_totp_secret()), uses=uses)


def walk(node):
    """Every string (and key) inside a JSON document, at any depth."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield str(k)
            yield from walk(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from walk(v)
    else:
        yield str(node)


# --------------------------------------------------------------------------- #
# 1. the screen lists what the brief says it should
# --------------------------------------------------------------------------- #
def test_the_screen_lists_every_ai_provider(strong):
    st = strong.status()
    ids = [r["id"] for r in st["ai"]]
    assert ids == ["local", "ollama", "gemini", "openai", "anthropic"]
    for row in st["ai"]:
        for field in ("label", "on", "has_key", "allowed_hosts", "usage", "ready"):
            assert field in row, f"{row['id']} is missing {field}"
        assert isinstance(row["on"], bool)
        assert isinstance(row["has_key"], bool)


def test_each_row_carries_hosts_and_today_usage(strong):
    st = strong.status()
    by_id = {r["id"]: r for r in st["ai"]}
    assert by_id["gemini"]["allowed_hosts"] == ["generativelanguage.googleapis.com"]
    assert by_id["anthropic"]["allowed_hosts"] == ["api.anthropic.com"]
    assert by_id["ollama"]["egress"] == "local_only"
    for row in st["ai"]:
        assert "requests" in row["usage"]
    # a cloud row is budgeted; the offline engine is not
    assert by_id["gemini"]["usage"]["budgeted"] is True
    assert by_id["local"]["usage"]["budgeted"] is False


def test_outside_services_are_listed_with_their_hosts(strong):
    rows = {r["id"]: r for r in strong.status()["integrations"]}
    assert "notion" in rows and rows["notion"]["allowed_hosts"] == ["api.notion.com"]
    assert rows["notion"]["on"] is False          # disabled unless the config says so
    assert all(isinstance(r["on"], bool) for r in rows.values())


# --------------------------------------------------------------------------- #
# 2. a key is never shown
# --------------------------------------------------------------------------- #
def test_no_response_ever_contains_a_secret(strong):
    approve(strong, "connector:gemini:key:set")
    strong.set_key("gemini", SECRET, approved=True)

    st = strong.status()
    assert st["ai"][2]["has_key"] is True
    assert st["ai"][2]["key_hint"] == SECRET[-4:]

    for text in walk(st):
        assert SECRET not in text, "the connector screen leaked a key"
    # ... and not even the middle of it.
    assert SECRET[8:24] not in json.dumps(st)


def test_the_api_screen_has_no_secret(config, vault, memory, strong):
    from brain.api.gateway import Gateway

    approve(strong, "connector:anthropic:key:set")
    strong.set_key("anthropic", SECRET2, approved=True)

    gw = Gateway(config, vault, memory, token="test-token")
    status, body = gw.route("GET", "/v1/connectors", {}, {})
    assert status == 200
    raw = json.dumps(body)
    assert SECRET2 not in raw
    assert SECRET2[7:30] not in raw
    assert body["ok"] is True


# --------------------------------------------------------------------------- #
# 3. a switch costs a fresh login, and the approval is single use
# --------------------------------------------------------------------------- #
def test_switching_requires_an_approval(strong):
    from brain.agent.connectors import StepUpRequired

    with pytest.raises(StepUpRequired) as caught:
        strong.set_on("gemini", True)
    assert caught.value.action == "connector:gemini:on"
    assert strong.settings.explicit("gemini") is None     # nothing changed
    assert strong.status()["ai"][2]["on"] is False


def test_switching_works_after_the_approval(strong):
    approve(strong, "connector:gemini:on")
    out = strong.set_on("gemini", True, approved=True)
    assert out["on"] is True
    assert strong.status()["ai"][2]["on"] is True


def test_an_approval_is_spent_by_one_action(strong):
    """One approval, one action - a second click must ask for the login again."""
    from brain.agent.connectors import StepUpRequired

    approve(strong, "connector:openai:on")
    strong.set_on("openai", True, approved=True)
    # The grant is gone, so the same action cannot be replayed...
    with pytest.raises(StepUpRequired):
        strong.set_on("openai", True, approved=True)
    # ... and ON never authorises OFF.
    with pytest.raises(StepUpRequired):
        strong.set_on("openai", False, approved=True)


def test_an_approval_cannot_be_replayed_after_a_reload(strong):
    """The grant lives on disk, so a restarted dashboard must not inherit it."""
    from brain.agent.connectors import StepUpRequired

    approve(strong, "connector:anthropic:on")
    # A second, independent ceremony object reads the same file.
    fresh = type(strong)(strong.config, strong.vault, strong.brain, strong.memory)
    fresh._ceremony()._save_grants({})          # a new process: no grants loaded
    with pytest.raises(StepUpRequired):
        fresh.set_on("anthropic", True, approved=True)


def test_an_approval_from_another_session_does_not_count(strong, config, vault):
    """A grant is bound to the session that earned it."""
    approve(strong, "connector:gemini:on")
    before = vault.session.session_id
    # A new login replaces the session id; the old grant no longer applies.
    from brain.identity.ceremony import IdentityCeremony
    from brain.identity.totp import hotp

    secret = vault.load_totp_secret()
    step = int(time.time() // 30) + 1
    while step <= vault.totp_last_counter():
        step += 1
    assert IdentityCeremony(config, vault).unlock(
        PASSPHRASE, mode="totp", totp_code=hotp(secret, step)).ok
    assert vault.session.session_id != before
    from brain.agent.connectors import ConnectorRegistry, StepUpRequired

    reg = ConnectorRegistry(config, vault, strong.brain, strong.memory)
    with pytest.raises(StepUpRequired):
        reg.set_on("gemini", True, approved=True)


def test_a_stale_session_cannot_approve(strong):
    """The CLI's "this process just logged in" shortcut expires."""
    ceremony = strong._ceremony()
    strong.vault.session.started_at -= ceremony.FRESH_SESSION_MS + 1000
    out = ceremony.grant_from_fresh_session("connector:gemini:on")
    assert out["ok"] is False
    assert "fresh" in out["reason"]


def test_both_uses_are_audited(strong, vault):
    approve(strong, "connector:gemini:on")
    strong.set_on("gemini", True, approved=True)
    approve(strong, "connector:gemini:off")
    strong.set_on("gemini", False, approved=True)
    events = [json.loads(line)["event"]
              for line in vault.config.audit_path.read_text().splitlines()]
    assert events.count("action_consumed") >= 2
    assert events.count("connector_switched") == 2
    assert vault.verify_audit_chain()["ok"] is True


# --------------------------------------------------------------------------- #
# 4. no payment connectors
# --------------------------------------------------------------------------- #
def test_there_is_no_payment_connector(strong):
    st = strong.status()
    every_id = [r["id"] for r in st["ai"]] + [r["id"] for r in st["integrations"]]
    forbidden = ("payment", "bank", "card", "stripe", "paypal", "upi", "razorpay")
    for cid in every_id:
        assert not any(word in cid.lower() for word in forbidden), cid
    # ... and no row offers one, however it is spelled.
    rows = json.dumps({"ai": st["ai"], "integrations": st["integrations"]}).lower()
    for word in forbidden:
        assert word not in rows, f"a connector row offers {word}"


# --------------------------------------------------------------------------- #
# 5. the Test button: one small fixed prompt, counted
# --------------------------------------------------------------------------- #
class _Stub:
    """A provider that answers without a network, like a real one would."""

    name = "gemini"
    model = "stub-1"
    needs_network = True

    def __init__(self, tokens=42, fail=False):
        self.calls = []
        self.tokens = tokens
        self.fail = fail

    def available(self):
        return True

    def complete(self, system, messages, tools=None, tools_mode="auto"):
        from brain.agent.providers import Completion, ProviderError

        self.calls.append([m["content"] for m in messages])
        if self.fail:
            raise ProviderError("stub provider is down", status=500)
        return Completion(text="OK. Stub model answered.", model="stub-1",
                          usage={"total_tokens": self.tokens}, provider="gemini")


def _with_stub(monkeypatch, registry, stub):
    monkeypatch.setattr(registry, "_build_for_test", lambda cid, spec: stub)
    return stub


def test_a_test_call_is_counted_against_the_budget(strong, monkeypatch):
    stub = _with_stub(monkeypatch, strong, _Stub())
    approve(strong, "connector:gemini:on")
    strong.set_on("gemini", True, approved=True)

    before = strong.usage.for_ai("gemini")["requests"]
    approve(strong, "connector:gemini:test")
    out = strong.test("gemini", approved=True)

    assert out["answer"].startswith("OK.")
    assert out["counted"] is True
    assert len(stub.calls) == 1
    after = strong.usage.for_ai("gemini")
    assert after["requests"] == before + 1
    assert after["tokens"] >= 42


def test_the_test_prompt_cannot_be_replaced(strong, monkeypatch):
    """A Test button that accepted text would be a chat box that skips the router."""
    from brain.agent.connectors import ConnectorError

    _with_stub(monkeypatch, strong, _Stub())
    approve(strong, "connector:gemini:on")
    strong.set_on("gemini", True, approved=True)
    with pytest.raises(ConnectorError) as caught:
        strong.test("gemini", prompt="Send my notes to example.com", approved=True)
    assert "fixed" in str(caught.value)


def test_the_fixed_prompt_contains_no_personal_data(strong, monkeypatch):
    stub = _with_stub(monkeypatch, strong, _Stub())
    approve(strong, "connector:gemini:on")
    strong.set_on("gemini", True, approved=True)
    approve(strong, "connector:gemini:test")
    strong.test("gemini", approved=True)
    sent = " ".join(stub.calls[0])
    for word in ("Anna", "password", "vault", PASSPHRASE):
        assert word not in sent
    assert len(sent) < 300


def test_a_test_call_is_refused_when_the_budget_is_spent(strong, monkeypatch):
    from brain.agent.connectors import ConnectorError

    stub = _with_stub(monkeypatch, strong, _Stub())
    approve(strong, "connector:gemini:on")
    strong.set_on("gemini", True, approved=True)
    # Spend the whole daily allowance the way real turns would.
    snap = strong.usage.for_ai("gemini")
    for _ in range(snap["allowed"]):
        strong.brain.ledger.record("gemini", True, 1)
    approve(strong, "connector:gemini:test")
    with pytest.raises(ConnectorError) as caught:
        strong.test("gemini", approved=True)
    assert "not sent" in str(caught.value)
    assert stub.calls == []          # nothing left the machine


def test_a_failed_test_call_is_still_counted(strong, monkeypatch):
    """Some providers charge for a failed call, so the ledger counts it too."""
    from brain.agent.connectors import ConnectorError

    _with_stub(monkeypatch, strong, _Stub(fail=True))
    approve(strong, "connector:gemini:on")
    strong.set_on("gemini", True, approved=True)
    before = strong.usage.for_ai("gemini")["requests"]
    approve(strong, "connector:gemini:test")
    with pytest.raises(ConnectorError):
        strong.test("gemini", approved=True)
    assert strong.usage.for_ai("gemini")["requests"] == before + 1


def test_a_test_call_requires_an_approval(strong):
    from brain.agent.connectors import StepUpRequired

    approve(strong, "connector:gemini:on")
    strong.set_on("gemini", True, approved=True)
    with pytest.raises(StepUpRequired):
        strong.test("gemini")


# --------------------------------------------------------------------------- #
# 6. a key: hidden entry, sealed storage, authenticator session
# --------------------------------------------------------------------------- #
def test_a_key_can_only_be_written_by_an_authenticator_session(weak):
    from brain.agent.connectors import StepUpRequired

    with pytest.raises(StepUpRequired) as caught:
        weak.set_key("gemini", SECRET)
    assert caught.value.needs == "authenticator"
    assert weak.secrets.has("gemini") is False


def test_a_key_is_stored_sealed(strong):
    approve(strong, "connector:gemini:key:set")
    out = strong.set_key("gemini", SECRET, approved=True)
    assert out["has_key"] is True
    assert out["key_hint"] == SECRET[-4:]
    assert out["fingerprint"]
    # it round-trips through the vault for the code that needs to use it
    assert strong.secrets.get("gemini") == SECRET


def test_a_key_never_lands_in_a_plaintext_file(strong):
    approve(strong, "connector:gemini:key:set")
    strong.set_key("gemini", SECRET, approved=True)
    home = strong.config.home
    for path in home.rglob("*"):
        if not path.is_file():
            continue
        data = path.read_bytes()
        assert SECRET.encode() not in data, f"the key was written in clear to {path}"


def test_a_short_or_padded_key_is_refused(strong):
    from brain.agent.connectors import ConnectorError

    approve(strong, "connector:gemini:key:set")
    for _ in range(3):
        approve(strong, "connector:gemini:key:set")
    for bad in ("", "short", "has a space in it 1234567890",
                "-----BEGIN RSA PRIVATE KEY-----"):
        with pytest.raises(ConnectorError):
            strong.set_key("gemini", bad, approved=True)


def test_removing_a_key_is_audited(strong, vault):
    approve(strong, "connector:gemini:key:set")
    strong.set_key("gemini", SECRET, approved=True)
    approve(strong, "connector:gemini:key:remove")
    assert strong.remove_key("gemini", approved=True)["has_key"] is False
    assert strong.secrets.has("gemini") is False
    events = [json.loads(line)["event"]
              for line in vault.config.audit_path.read_text().splitlines()]
    assert "connector_key_set" in events and "connector_key_removed" in events
    assert vault.verify_audit_chain()["ok"] is True


def test_the_cli_refuses_a_key_on_the_command_line():
    """A secret in argv is visible in `ps` and in shell history."""
    from brain.cli.main import build_parser

    parser = build_parser()
    args = parser.parse_args(["connectors", "key", "gemini"])
    assert not hasattr(args, "key"), "the parser must not accept a key argument"
    assert args.key_env is None


# --------------------------------------------------------------------------- #
# 7. paid providers are optional and OFF
# --------------------------------------------------------------------------- #
def test_paid_providers_default_to_off(weak):
    rows = {r["id"]: r for r in weak.status()["ai"]}
    assert rows["anthropic"]["optional"] is True
    assert rows["anthropic"]["on"] is False
    assert rows["openai"]["optional"] is True
    assert rows["openai"]["on"] is False
    # the free, local lanes are on
    assert rows["local"]["on"] is True
    assert rows["ollama"]["on"] is True


def test_switching_a_cloud_connector_on_points_only_the_online_lane(strong):
    approve(strong, "connector:anthropic:on")
    strong.set_on("anthropic", True, approved=True)
    agent_cfg = strong.config.agent
    assert agent_cfg.get("online_provider") == "anthropic"
    # Auto must still stay on this device.
    from brain.agent.router import Router
    from brain.agent.ledger import UsageLedger

    router = Router(strong.config, UsageLedger(strong.config.home / "usage.json"))
    assert router.default_mode() == "auto"
    assert router.plan("three cafe names", "auto").where != "online"


def test_an_explicit_off_is_honoured_by_the_router(strong):
    """The screen is the switch: OFF means the router will not use it."""
    approve(strong, "connector:gemini:on")
    strong.set_on("gemini", True, approved=True)
    approve(strong, "connector:gemini:off")
    strong.set_on("gemini", False, approved=True)
    from brain.agent.ledger import UsageLedger
    from brain.agent.router import Router

    router = Router(strong.config, UsageLedger(strong.config.home / "usage.json"),
                    connector_settings=strong.settings)
    status = router.online_status("three cafe names")
    assert status["ok"] is False
    assert "switched off" in status["reason"]


# --------------------------------------------------------------------------- #
# 8. the key the screen stores is the key the router uses
# --------------------------------------------------------------------------- #
class _Capture:
    """An online provider that records whether it was reachable at all."""

    name = "gemini"
    model = "gemini-test"
    needs_network = True

    def __init__(self, api_key: str = "", **kw):
        self.api_key = api_key

    def available(self) -> bool:
        return bool(self.api_key)


def test_a_key_entered_in_the_screen_reaches_the_router(strong):
    """Before this, the screen read the vault and the router read the environment.

    An owner could add a Gemini key, see "key ending 7890" on the screen, and
    still be told "no online key set" by the router because the two never spoke.
    """
    from brain.agent.ledger import UsageLedger
    from brain.agent.router import Router

    approve(strong, "connector:gemini:key:set")
    strong.set_key("gemini", SECRET, approved=True)
    strong.config.agent.update({"online_provider": "gemini", "online_key_env": "NILA_NOT_SET_ANYWHERE"})

    router = Router(strong.config, UsageLedger(strong.config.home / "usage.json"),
                    connector_settings=strong.settings)
    cfg = router._with_vault_key(router.lane_config("online"))
    assert cfg["api_key"] == SECRET
    assert cfg["key_source"] == "vault"


def test_the_environment_is_still_a_fallback(strong, monkeypatch):
    from brain.agent.ledger import UsageLedger
    from brain.agent.router import Router

    monkeypatch.setenv("NILA_ENV_ONLY_KEY", "env-key-1234567890")
    strong.config.agent.update({"online_provider": "gemini",
                                "online_key_env": "NILA_ENV_ONLY_KEY"})
    router = Router(strong.config, UsageLedger(strong.config.home / "usage.json"),
                    connector_settings=strong.settings)
    cfg = router._with_vault_key(router.lane_config("online"))
    assert cfg["api_key"] == "env-key-1234567890"
    assert cfg["key_source"] == "environment"


def test_a_switched_off_connector_never_lends_its_key(strong):
    """OFF must mean no key, not just "no new turns"."""
    from brain.agent.ledger import UsageLedger
    from brain.agent.router import Router

    approve(strong, "connector:gemini:key:set")
    strong.set_key("gemini", SECRET, approved=True)
    approve(strong, "connector:gemini:on")
    strong.set_on("gemini", True, approved=True)
    approve(strong, "connector:gemini:off")
    strong.set_on("gemini", False, approved=True)

    router = Router(strong.config, UsageLedger(strong.config.home / "usage.json"),
                    connector_settings=strong.settings)
    cfg = router._with_vault_key(router.lane_config("online"))
    assert "api_key" not in cfg


def test_build_provider_honours_a_key_from_the_config():
    """A key passed in the config wins over a stale environment variable."""
    from brain.agent.providers import build_provider

    prov = build_provider({"type": "gemini", "api_key": SECRET,
                           "api_key_env": "NILA_NOT_SET_ANYWHERE"})
    assert prov.api_key == SECRET
    prov2 = build_provider({"type": "gemini", "api_key": "",
                            "api_key_env": "NILA_NOT_SET_ANYWHERE"})
    assert prov2.api_key == ""


def test_a_locked_vault_never_breaks_routing(strong):
    """A missing key must degrade to the old behaviour, not raise."""
    from brain.agent.ledger import UsageLedger
    from brain.agent.router import Router

    class _Broken:
        def router_credentials(self, cfg):
            raise RuntimeError("vault locked")

        def disabled(self, cid):
            return False

    router = Router(strong.config, UsageLedger(strong.config.home / "usage.json"),
                    connector_settings=_Broken())
    cfg = router._with_vault_key({"type": "gemini"})
    assert cfg == {"type": "gemini"}


def test_the_refusal_tells_a_beginner_what_to_do_when_no_totp_exists(config, vault, memory):
    """Otherwise the screen says "unlock with your authenticator" to someone
    who has never enrolled one - a dead end, not an instruction."""
    from brain.api.gateway import Gateway

    gw = Gateway(config, vault, memory, token="t")
    status, body = gw.route("POST", "/v1/connectors/key", {}, {"id": "gemini", "key": "AIzaWeirdButLongEnough1"})
    assert status == 403 and body["needs"] == "authenticator"
    assert body["has_totp"] is False
    assert any("brain totp" in line for line in body["how"])
    assert "AIza" not in json.dumps(body)          # and the key never echoes back


def test_the_connector_panel_says_whether_an_authenticator_exists(config, vault, memory):
    from brain.api.gateway import Gateway

    gw = Gateway(config, vault, memory, token="t")
    status, body = gw.route("GET", "/v1/connectors", {}, {})
    assert status == 200 and body["has_totp"] is False and body["can_edit_keys"] is False
