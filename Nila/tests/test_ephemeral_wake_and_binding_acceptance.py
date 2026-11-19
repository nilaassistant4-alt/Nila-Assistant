"""Acceptance tests: stage 13 - the four rows that were not finished.

Each test here exists because the row it covers was reported as built while a
part of it was not, and a note in a document is not the same as a check that
fails when the note becomes untrue.

1. ``test_ephemeral_*`` - a value held for one use is never written down and is
   gone after one use (mandate 1's do-not-store tier).
2. ``test_wake_*`` - the wake word works, and cannot become the always-on
   microphone this build refuses.
3. ``test_activation_*`` - mandate 5's "strictly on demand" is a mode the owner
   can switch on, and in that mode no standing grant survives a command.
4. ``test_account_*`` - one integration speaks for one account (mandate 3), and
   an unbound or wrong-account call is refused before the network.

Run:  python -m pytest tests/test_ephemeral_wake_and_binding_acceptance.py -q
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from brain.agent import ephemeral, wake
from brain.agent.integrations import IntegrationGateway
from brain.security.permission import CAP_FILE_READ, Broker
from brain.util.audio import write_wav


# --------------------------------------------------------------------------- #
# a synthetic two-syllable "Nila", so the matcher can be scored with no hardware
# --------------------------------------------------------------------------- #
RATE = 16000


def _syllable(seconds: float, pitch: float) -> list[float]:
    n = int(RATE * seconds)
    out = []
    for i in range(n):
        envelope = math.sin(math.pi * (i / n)) ** 0.7
        t = i / RATE
        out.append(0.5 * envelope * (math.sin(2 * math.pi * pitch * t)
                                     + 0.4 * math.sin(4 * math.pi * pitch * t)))
    return out


def name_audio() -> list[float]:
    return _syllable(0.28, 190) + [0.0] * int(RATE * 0.06) + _syllable(0.30, 170)


# --------------------------------------------------------------------------- #
# 1. the use-once store
# --------------------------------------------------------------------------- #
def test_a_held_value_is_never_written_down(tmp_path):
    store = ephemeral.EphemeralStore()
    slot = store.keep("9845123789", purpose="call amma tonight", ttl=120)
    # everything the vault is ever allowed to record about this slot
    row = store.vault_slot(slot.id)
    assert "9845123789" not in json.dumps(row)
    assert row["value_recorded"] is False
    assert row["digest"] and len(row["digest"]) == 32
    # and the live report - the thing `brain temp` prints - has no value either
    assert "9845123789" not in json.dumps(store.report())


def test_a_held_value_can_be_used_exactly_once():
    store = ephemeral.EphemeralStore()
    slot = store.keep("9845123789", purpose="call amma")
    assert store.take(slot.id)["value"] == "9845123789"
    with pytest.raises(ephemeral.EphemeralError, match="already used"):
        store.take(slot.id)
    assert store.report()["held"] == 0


def test_a_held_value_expires_and_purging_empties_everything():
    now = [1000.0]
    store = ephemeral.EphemeralStore(clock=lambda: now[0])
    short = store.keep("1111", purpose="one", ttl=30)
    long = store.keep("2222", purpose="two", ttl=600)
    now[0] += 31
    with pytest.raises(ephemeral.EphemeralError, match="expired"):
        store.take(short.id)
    assert [s["id"] for s in store.slots()] == [long.id]
    assert store.purge("lock")["purged"] == 1
    assert store.report()["held"] == 0


def test_the_store_has_a_ceiling_instead_of_growing_forever():
    store = ephemeral.EphemeralStore()
    for i in range(ephemeral.MAX_SLOTS):
        store.keep(f"value-{i}", purpose="filler")
    with pytest.raises(ephemeral.EphemeralError, match="already being held"):
        store.keep("one more", purpose="overflow")


def test_the_agent_tools_hand_the_value_over_and_then_forget_it():
    from brain.agent.tools import ToolContext, build_default_registry

    class Vault:
        def is_unlocked(self):
            return True

    reg = build_default_registry()
    ctx = ToolContext(memory=None, vault=Vault(), config=None)
    held = reg.call("hold_ephemeral", {"value": "9845123789", "purpose": "call amma"}, ctx)
    assert held["ok"] and held["has_value"] is True
    assert "9845123789" not in json.dumps(held)
    used = reg.call("use_ephemeral", {"purpose": "amma"}, ctx)
    assert used["value"] == "9845123789"
    again = reg.call("use_ephemeral", {"purpose": "amma"}, ctx)
    assert again["ok"] is False and "nothing held" in again["error"]


# --------------------------------------------------------------------------- #
# 2. the wake word, and the window it may not leave
# --------------------------------------------------------------------------- #
def test_the_wake_word_is_heard_in_a_two_syllable_utterance():
    found = wake.score(name_audio(), RATE)
    assert found.phrase == "nila"
    assert found.confidence > 0.8
    assert found.detail["syllables"] == 2


def test_a_click_a_hiss_and_a_sentence_are_not_the_wake_word():
    click = [0.9] * 40 + [0.0] * 4000
    hiss = [math.sin(i * 12.9898) * 0.5 for i in range(int(RATE * 0.5))]
    gap = [0.0] * int(RATE * 0.09)          # 90 ms: a real gap between words
    sentence = (_syllable(0.2, 180) + gap + _syllable(0.2, 180) + gap
                + _syllable(0.2, 180) + gap + _syllable(0.2, 180))
    for clip, why in ((click, "click"), (hiss, "hiss"), (sentence, "sentence")):
        assert wake.score(clip, RATE).phrase == "", why


def test_a_wake_word_outside_its_window_is_refused():
    window = wake.WakeWindow(seconds=30, clock=lambda: 100.0)
    with pytest.raises(wake.WakeError, match="no window is open"):
        window.feed(name_audio(), RATE)
    window.open()
    assert window.feed(name_audio(), RATE) is not None


def test_the_window_closes_itself_and_an_all_day_window_is_refused():
    now = [0.0]
    window = wake.WakeWindow(seconds=10, clock=lambda: now[0])
    window.open()
    now[0] = 11.0
    assert window.open_() is False
    with pytest.raises(wake.WakeError, match="no window is open"):
        window.feed(name_audio(), RATE)
    with pytest.raises(wake.WakeError, match="ceiling"):
        wake.WakeWindow(seconds=wake.MAX_WINDOW_SECONDS + 1)


def test_opening_the_window_asks_the_microphone_capability(tmp_path):
    broker = Broker(path=tmp_path / "permissions.json")
    window = wake.WakeWindow(seconds=30, broker=broker)
    with pytest.raises(wake.WakeError, match="not affirmed"):
        window.open()
    assert window.report()["open"] is False


def test_a_recording_can_be_scored_with_no_microphone(tmp_path):
    path = tmp_path / "nila.wav"
    write_wav(path, name_audio(), RATE)
    out = wake.probe(str(path))
    assert out["heard"] is True
    assert out["confidence"] > 0.8
    assert "nila" in [p.lower() for p in wake.WAKE_PHRASES]


# --------------------------------------------------------------------------- #
# 3. strictly on demand
# --------------------------------------------------------------------------- #
def test_command_mode_stops_honouring_a_standing_grant(tmp_path):
    broker = Broker(path=tmp_path / "permissions.json")
    assert broker.grant_durable(CAP_FILE_READ, "/tmp/notes").allowed
    assert broker.check(CAP_FILE_READ, "/tmp/notes").allowed
    broker.set_mode("command")
    assert broker.check(CAP_FILE_READ, "/tmp/notes").allowed is False
    # and the peek must agree, or a caller skips the affirmation it forces
    assert broker.peek(CAP_FILE_READ, "/tmp/notes").allowed is False


def test_command_mode_refuses_to_create_a_new_standing_grant(tmp_path):
    broker = Broker(path=tmp_path / "permissions.json")
    broker.set_mode("command")
    assert broker.grant_durable(CAP_FILE_READ, "/tmp/notes").allowed is False
    # the old grant comes back the moment the owner switches back
    broker.set_mode("standing")
    assert broker.grant_durable(CAP_FILE_READ, "/tmp/notes").allowed
    assert broker.check(CAP_FILE_READ, "/tmp/notes").allowed


def test_an_unknown_mode_is_refused_instead_of_defaulting(tmp_path):
    broker = Broker(path=tmp_path / "permissions.json")
    with pytest.raises(ValueError, match="unknown mode"):
        broker.set_mode("sometimes")


# --------------------------------------------------------------------------- #
# 4. one integration, one account
# --------------------------------------------------------------------------- #
def test_an_account_integration_with_no_account_is_refused_before_the_network():
    gateway = IntegrationGateway(config=None, path=Path("/nonexistent/integrations.json"))
    assert "gmail" in gateway.integrations
    problem = gateway.account_problem("gmail", {})
    assert "no account bound" in problem
    assert "brain integrations bind gmail" in problem


def test_a_call_naming_another_account_is_refused(tmp_path):
    gateway = IntegrationGateway(config=None, path=tmp_path / "integrations.json")
    (tmp_path / "integrations.json").write_text(json.dumps({"integrations": [
        {"name": "gmail", "base_url": "https://bridge.example.internal",
         "enabled": True, "account": "santhosh@gmail.com",
         "operations": [{"name": "list", "method": "GET", "path": "/v1/messages"}]}]}))
    gateway.load()
    assert gateway.account_problem("gmail", {"account": "santhosh@gmail.com"}) == ""
    assert gateway.account_problem("gmail", {"account": "SANTHOSH@gmail.com"}) == ""
    wrong = gateway.account_problem("gmail", {"account": "other@gmail.com"})
    assert "is bound to santhosh@gmail.com" in wrong
    with pytest.raises(Exception):
        gateway.call("gmail", "list", {"account": "other@gmail.com"})


def test_binding_an_account_is_written_and_rebinding_is_flagged(tmp_path):
    path = tmp_path / "integrations.json"
    path.write_text(json.dumps({"integrations": [
        {"name": "drive", "base_url": "https://bridge.example.internal",
         "enabled": False}]}))
    gateway = IntegrationGateway(config=None, path=path)
    first = gateway.bind_account("drive", "santhosh@gmail.com")
    assert first["rebound"] is False
    second = gateway.bind_account("drive", "second@gmail.com")
    assert second["rebound"] is True
    assert json.loads(path.read_text())["integrations"][0]["account"] == "second@gmail.com"
    assert gateway.bindings()["unbound"] == []


def test_a_device_ceiling_refuses_the_extra_device(tmp_path):
    from brain.config import Config
    from brain.memory.vault import Vault

    def fresh(name: str) -> tuple[Config, Vault]:
        cfg = Config.load(tmp_path / name)
        cfg.fast_kdf = True
        return cfg, Vault(cfg)

    _, host = fresh("pc1")
    host.initialize("passphrase-for-test", "Owner", "dev-1")
    host.set_device_limit(2)
    bundle = host.export_bundle("laptop")
    _, second = fresh("pc2")
    second.import_bundle(bundle, "passphrase-for-test")
    # one slot left, and a third device needs a bundle the second PC cannot mint
    assert second.enrolment_room()["left"] == 0
    with pytest.raises(PermissionError, match="maximum"):
        second.export_bundle("phone")
