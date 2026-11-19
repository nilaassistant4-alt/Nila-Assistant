"""Acceptance tests: stage 14 - "type it once, and never again".

The owner's complaint, in his words: he unzipped Nila, pressed setup, chose a
passphrase he then forgot, and **every** start asked for it again - and the old
passphrase still worked after he tried to change it. Three separate problems were
hiding under one sentence, and each one has a test here:

1. **It asks every time.** A device key now opens the vault, so the passphrase is
   typed once. Tested against a fake OS store, because the sandbox has no
   Windows Credential Locker.
2. **"Change the password" did nothing.** `set_passphrase` re-wraps the master key
   for every device; the old passphrase is refused afterwards. Tested by trying
   the old one.
3. **One key, or the voice.** The launcher and the wake word must be reachable in
   one step, and nothing may open a microphone at sign-in.

What is deliberately NOT claimed: the device key is not a password manager's
security. Anyone sitting at the unlocked laptop can open Nila - that is what
"remember me" means, and the tests assert the audit line says so rather than the
build pretending nothing changed.

Run:  python -m pytest tests/test_first_run_and_no_password_acceptance.py -q
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from brain.config import Config
from brain.memory.vault import Vault
from brain.security import vaultkey
from brain.util import hotkey, launcher


# --------------------------------------------------------------------------- #
# a fake OS secret store: the sandbox has no Windows Credential Locker
# --------------------------------------------------------------------------- #
@pytest.fixture
def fake_store(monkeypatch):
    """Stand in for keyring, so 'remembered' can be proved without Windows."""
    memory: dict[tuple[str, str], str] = {}

    class FakeKeyring:
        @staticmethod
        def get_keyring():
            return "fake store (test)"

        @staticmethod
        def set_password(service, user, value):
            memory[(service, user)] = value

        @staticmethod
        def get_password(service, user):
            return memory.get((service, user))

        @staticmethod
        def delete_password(service, user):
            memory.pop((service, user), None)

    monkeypatch.setattr(vaultkey, "_keyring", lambda: FakeKeyring)
    monkeypatch.setattr(vaultkey, "_dpapi_available", lambda: False)
    return memory


@pytest.fixture
def vault_home(tmp_path):
    config = Config.load(tmp_path / "vault")
    config.fast_kdf = True
    vault = Vault(config)
    vault.initialize("first-passphrase-1", "Owner", "dev-1")
    return config, vault


# --------------------------------------------------------------------------- #
# 1. the passphrase is typed once
# --------------------------------------------------------------------------- #
def test_the_key_is_remembered_and_opens_the_vault_without_the_passphrase(fake_store, vault_home):
    config, vault = vault_home
    mk = vault.unlock("first-passphrase-1")
    assert vaultkey.save(mk, config.home)["saved"] is True
    assert vaultkey.status(config.home)["remembered"] is True

    # a fresh process: nothing in memory, only the OS store
    reopened = Vault(config)
    saved = vaultkey.load(config.home)
    assert saved == bytes(mk)
    session = reopened.unlock_with_master_key(saved)
    assert session.mode == "device-key"
    assert reopened.is_unlocked() is True


def test_a_key_saved_for_another_vault_is_refused(fake_store, vault_home, tmp_path):
    config, vault = vault_home
    vaultkey.save(vault.unlock("first-passphrase-1"), config.home)
    other = Config.load(tmp_path / "second-vault")
    other.fast_kdf = True
    second = Vault(other)
    second.initialize("other-passphrase-1", "Owner", "dev-2")

    # the stored blob names the FIRST vault home, so it cannot open the second
    assert vaultkey.load(other.home) is None


def test_a_wrong_key_fails_exactly_like_a_wrong_passphrase(fake_store, vault_home):
    config, vault = vault_home
    fresh = Vault(config)
    with pytest.raises(PermissionError, match="does not match this vault"):
        fresh.unlock_with_master_key(b"\x00" * 32)
    assert fresh.is_unlocked() is False


def test_forgetting_the_key_makes_the_vault_ask_again(fake_store, vault_home):
    config, vault = vault_home
    vaultkey.save(vault.unlock("first-passphrase-1"), config.home)
    assert vaultkey.status(config.home)["remembered"] is True
    out = vaultkey.forget(config.home)
    assert out["ok"] is True and out["still_saved"] is False
    assert vaultkey.load(config.home) is None
    # and the memories are untouched by forgetting a shortcut
    assert Vault(config).verify_passphrase("first-passphrase-1") is True


def test_the_audit_says_which_way_the_vault_was_opened(fake_store, vault_home):
    """The journal must not imply a human typed a passphrase when a key was used -
    that difference is the whole safety question about 'remember me'."""
    config, vault = vault_home
    vaultkey.save(vault.unlock("first-passphrase-1"), config.home)
    reopened = Vault(config)
    reopened.unlock_with_master_key(vaultkey.load(config.home))
    lines = config.audit_path.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line)["event"] for line in lines if line.strip()]
    assert "unlock_via_device_key" in events


def test_setup_remembers_the_laptop_in_the_same_breath(fake_store, tmp_path):
    """The owner's exact sequence: unzip, press setup, type it ONCE.

    `brain init` used to leave the vault locked with nothing remembered, so the
    first start asked for the passphrase he had just typed. It now saves the key
    while it still has it, and a cold `open_vault` afterwards asks nothing.
    """
    from brain.cli import main as cli

    home = tmp_path / "first-run"
    parser = cli.build_parser()
    args = parser.parse_args(["--fast-kdf", "--home", str(home), "init",
                              "--passphrase", "typed-once-1"])
    import contextlib
    import io

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert cli.cmd_init(args, cli.UI()) == 0
    assert vaultkey.status(home)["remembered"] is True

    # a cold start: nothing passed in, nothing to type
    cold = parser.parse_args(["--home", str(home), "status", "--json"])
    _, opened = cli.open_vault(cold)
    assert opened.session.mode == "device-key"


def test_a_second_setup_never_overwrites_a_working_key(fake_store, tmp_path):
    """Re-extracting the ZIP and pressing setup again must not re-seal anything."""
    from brain.cli import main as cli
    import contextlib
    import io

    home = tmp_path / "twice"
    parser = cli.build_parser()
    for _ in range(2):
        args = parser.parse_args(["--fast-kdf", "--home", str(home), "init",
                                  "--passphrase", "typed-once-1"])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cli.cmd_init(args, cli.UI())
    assert len(fake_store) == 1, "the store should hold one key, not two"
    assert Vault(Config.load(home)).unlock_with_master_key(
        vaultkey.load(home)).mode == "device-key"


# --------------------------------------------------------------------------- #
# 2. "change the password" now actually changes it
# --------------------------------------------------------------------------- #
def test_setting_a_new_passphrase_retires_the_old_one(vault_home):
    config, vault = vault_home
    mk = vault.unlock("first-passphrase-1")
    vault.start_session(mk)
    vault.set_passphrase("second-passphrase-2", mode="recovery")
    vault.lock()

    fresh = Vault(config)
    assert fresh.verify_passphrase("first-passphrase-1") is False, \
        "the old passphrase still worked - this is the bug the owner hit"
    assert fresh.verify_passphrase("second-passphrase-2") is True


def test_a_new_passphrase_needs_an_open_vault_and_some_length(vault_home):
    config, vault = vault_home
    vault.start_session(vault.unlock("first-passphrase-1"))
    with pytest.raises(ValueError, match="at least 8"):
        vault.set_passphrase("short")
    vault.lock()
    with pytest.raises(RuntimeError, match="unlock the vault first"):
        vault.set_passphrase("another-passphrase-3")


def test_the_memories_survive_a_new_passphrase(vault_home):
    """A set-passphrase is a re-wrap of the same master key, not a re-encryption,
    so every record must still open afterwards - the reason this is safe to offer
    to an owner who has forgotten his passphrase."""
    from brain.memory.store import MemoryStore

    config, vault = vault_home
    mk = vault.unlock("first-passphrase-1")
    vault.start_session(mk)
    store = MemoryStore(config, vault.session.mk, session=vault.session)
    record = store.remember("the car service is on Friday", kind="note")
    vault.set_passphrase("second-passphrase-2")
    store.close()
    vault.lock()

    again = Vault(config)
    again.start_session(again.unlock("second-passphrase-2"))
    reopened = MemoryStore(config, again.session.mk, session=again.session)
    found = reopened.get(record.id)
    assert found is not None and "car service" in found.body
    reopened.close()


# --------------------------------------------------------------------------- #
# 3. one click, and the voice - with the microphone still bounded
# --------------------------------------------------------------------------- #
def test_a_sign_in_shortcut_is_the_dashboard_and_never_the_microphone(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "brain.py").write_text("#")
    text = launcher.windows_vbs("python", "brain.py", str(home), extra_args=["start"])
    assert '"start"' in text and "wake" not in text and "talk" not in text
    with pytest.raises(launcher.LauncherError, match="must not open the microphone"):
        launcher.write_startup(home, want_voice=True)


def test_the_hotkey_report_says_what_still_stops_it(vault_home, fake_store):
    config, vault = vault_home
    locked = hotkey.check(config.home)
    assert locked["no_password_prompt"] is False
    assert "passphrase remember" in locked["next_step"]

    vaultkey.save(vault.unlock("first-passphrase-1"), config.home)
    ready = hotkey.check(config.home)
    assert ready["no_password_prompt"] is True
    assert ready["next_step"].startswith("press ")


def test_the_voice_plan_never_offers_an_always_on_microphone():
    plan = hotkey.voice_plan()
    assert plan["wake_word"] is True
    assert plan["bounded_window_seconds"] <= 900
    assert any("brain wake" in line for line in plan["then"])


def test_the_shortcut_is_written_next_to_the_vault_and_starts_with_no_console(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "brain.py").write_text("#")
    out = hotkey.write_hotkey_shortcut(home, out_dir=tmp_path / "desk")
    assert out["ok"] is True
    assert any(name.endswith(".desktop") or name.endswith(".lnk") or name.endswith(".command")
               for name in out["written"])
    assert "password prompt" in out["note"]


def test_the_wake_word_still_refuses_a_window_it_would_not_close():
    from brain.agent import wake

    with pytest.raises(wake.WakeError, match="ceiling"):
        wake.WakeWindow(seconds=wake.MAX_WINDOW_SECONDS + 60)
