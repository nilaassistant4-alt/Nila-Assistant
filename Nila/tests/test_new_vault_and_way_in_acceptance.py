"""Acceptance tests: "I do not know the password and I do not know what a recovery code is".

That sentence is the whole request, and nothing in the build could answer it:

* `brain init` refuses when a vault exists, so there was no way to *choose a new
  password* - only to type the old one;
* a recovery code could open the vault (stage 15) but nothing said where codes come
  from, or that they are only shown once, at setup;
* the passphrase prompt hid every character and printed nothing else, so it looked
  frozen: "I type it and nothing appears";
* and the only advice left in the `:stuck` path was "send me the window", i.e. the
  owner was told to wait instead of being given a way out.

Each test below runs the thing the owner runs. The shapes that must NOT be able to
happen are pinned too - the old vault must survive, and a new vault must not silently
adopt an old memory file.

Run:  python -m pytest tests/test_new_vault_and_way_in_acceptance.py -q
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys
from pathlib import Path

import pytest

from brain.config import Config
from brain.memory.store import MemoryStore
from brain.memory.vault import Vault

ROOT = Path(__file__).resolve().parents[1]


class _Store(dict):
    """A keyring that behaves (write, then read the same value back)."""

    def get_keyring(self):
        return "test store"

    def set_password(self, service, user, value):
        self[(service, user)] = value

    def get_password(self, service, user):
        return self.get((service, user))

    def delete_password(self, service, user):
        self.pop((service, user), None)


@pytest.fixture()
def store(monkeypatch):
    from brain.security import vaultkey

    good = _Store()
    monkeypatch.setattr(vaultkey, "_keyring", lambda: good)
    monkeypatch.setattr(vaultkey, "_dpapi_available", lambda: False)
    return good


def _old_vault(home: Path, passphrase: str = "old-password-123") -> Vault:
    config = Config.load(home)
    config.fast_kdf = True
    vault = Vault(config)
    vault.initialize(passphrase, "Owner", "dev-old")
    return vault


def _run_new_vault(home: Path, new_passphrase: str, monkeypatch, stdin: str = ""):
    """Run `brain new-vault` the way the .bat does, and return what it printed."""
    from brain.cli import main as cli

    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin or f"{new_passphrase}\n"))
    args = cli.build_parser().parse_args(["--home", str(home), "new-vault",
                                          "--passphrase-stdin"])
    buf = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        code = cli.cmd_new_vault(args, cli.UI())
    return code, buf.getvalue() + err.getvalue()


# --------------------------------------------------------------------------- #
# 1. a NEW password, chosen now - the thing he actually asked for
# --------------------------------------------------------------------------- #
def test_a_new_vault_can_be_made_when_the_old_password_is_unknown(tmp_path, monkeypatch, store):
    home = tmp_path / "vault"
    _old_vault(home).lock()

    code, text = _run_new_vault(home, "santhosh123", monkeypatch)
    assert code == 0, text
    fresh = Vault(Config.load(home))
    assert fresh.verify_passphrase("santhosh123") is True, "the NEW passphrase does not open it"
    assert fresh.verify_passphrase("old-password-123") is False


def test_the_old_vault_is_copied_and_kept_beside_the_new_one(tmp_path, monkeypatch, store):
    home = tmp_path / "vault"
    _old_vault(home).lock()

    code, text = _run_new_vault(home, "santhosh123", monkeypatch)
    assert code == 0, text
    copies = [p for p in tmp_path.iterdir() if p.name.startswith("vault-old-")]
    assert len(copies) == 1, f"the old vault was not kept: {list(tmp_path.iterdir())}"
    kept = Vault(Config(home=copies[0]))
    assert kept.verify_passphrase("old-password-123") is True, \
        "the kept copy no longer opens with its own passphrase - that is data loss"
    assert "vault-old-" in text and "Nothing was deleted" in text


def test_the_old_memories_are_counted_from_the_copy_not_guessed(tmp_path, monkeypatch, store):
    home = tmp_path / "vault"
    vault = _old_vault(home, "old-password-123")
    mk = vault.unlock("old-password-123")
    vault.start_session(mk, mode="setup")
    mem = MemoryStore(Config.load(home), mk, session=vault.session)
    for i in range(3):
        mem.remember(kind="note", title=f"t{i}", body=f"amma call {i}")
    mem.close()
    vault.lock()

    # the old passphrase IS known to the caller here, so it must be reported
    from brain.cli import main as cli

    monkeypatch.setattr(sys, "stdin", io.StringIO("santhosh123\n"))
    monkeypatch.setenv("NILA_OLD", "old-password-123")
    args = cli.build_parser().parse_args(
        ["--home", str(home), "new-vault", "--passphrase-stdin",
         "--old-passphrase-env", "NILA_OLD"])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        assert cli.cmd_new_vault(args, cli.UI()) == 0
    assert "memories in it: 3" in buf.getvalue(), buf.getvalue()[-600:]


def test_a_failed_copy_stops_everything(tmp_path, monkeypatch, store):
    """If the copy cannot be made, NOTHING is replaced - the vault stays as it was."""
    import shutil as real_shutil

    home = tmp_path / "vault"
    _old_vault(home).lock()
    before = (home / "identity.json").read_bytes()

    def refuse(src, dst, **kw):
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(real_shutil, "copytree", refuse)
    code, text = _run_new_vault(home, "santhosh123", monkeypatch)
    assert code != 0
    assert (home / "identity.json").read_bytes() == before, "the vault was touched anyway"
    assert Vault(Config.load(home)).verify_passphrase("old-password-123") is True


def test_a_new_vault_does_not_adopt_the_old_memory_file(tmp_path, monkeypatch, store):
    """The new vault has a new master key: an old memory.db next to it can never be
    readable, so it is set aside rather than left to fail at the first query."""
    home = tmp_path / "vault"
    vault = _old_vault(home, "old-password-123")
    mk = vault.unlock("old-password-123")
    vault.start_session(mk, mode="setup")
    mem = MemoryStore(Config.load(home), mk, session=vault.session)
    mem.remember(kind="note", title="bike", body="bike number is 1234")
    mem.close()
    vault.lock()
    assert (home / "memory.db").exists()

    code, text = _run_new_vault(home, "santhosh123", monkeypatch)
    assert code == 0, text
    assert not (home / "memory.db").exists(), \
        "the old encrypted store was left where the new vault would read it"
    # and the new vault can actually open a store
    fresh = Vault(Config.load(home))
    mk2 = fresh.unlock("santhosh123")
    fresh.start_session(mk2, mode="setup")
    store = MemoryStore(Config.load(home), mk2, session=fresh.session)
    assert store.stats().get("records", 0) == 0
    store.close()
    fresh.lock()


# --------------------------------------------------------------------------- #
# 2. the recovery codes are shown, explained, and can open the vault
# --------------------------------------------------------------------------- #
def test_the_new_setup_prints_recovery_codes_and_says_how_to_use_one(tmp_path, monkeypatch, store):
    home = tmp_path / "vault"
    code, text = _run_new_vault(home, "santhosh123", monkeypatch)
    assert code == 0, text
    assert "RECOVERY CODES" in text
    assert "shown once" in text
    assert "start-nila.bat recovery" in text
    # eight codes, each like abcde-fghij
    import re

    codes = re.findall(r"\b[a-z0-9]{5}-[a-z0-9]{5}\b", text)
    assert len(set(codes)) == 8, codes
    # and one of them really opens the vault on its own
    vault = Vault(Config.load(home))
    session = vault.unlock_with_recovery_code(codes[0])   # opens its own session
    assert vault.is_unlocked() and session.mode == "recovery"
    vault.lock()


# --------------------------------------------------------------------------- #
# 3. the prompt never looks frozen
# --------------------------------------------------------------------------- #
def test_the_passphrase_prompt_says_that_nothing_shows_and_shows_progress(monkeypatch):
    """"I type it and nothing appears on the screen, so I do not know if it worked."

    The characters must stay hidden (someone may be watching), so the window prints
    one fixed marker before and one after - the passphrase itself never.
    """
    from brain.cli import main as cli

    class FakeStdin:
        def __init__(self, text):
            self.buf = list(text)

        def isatty(self):
            return True

        def read(self, n=1):
            return self.buf.pop(0) if self.buf else ""

        def readline(self):
            return "".join(iter(lambda: self.read(1), ""))

        def fileno(self):
            return 0

    import getpass

    monkeypatch.setattr(sys, "stdin", FakeStdin("santhosh123\r"))
    # the hidden read is not what this test is about; its OUTPUT is
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "santhosh123")
    seen = io.StringIO()
    monkeypatch.setattr(sys, "stderr", seen)
    args = argparse.Namespace(passphrase_env=None, passphrase_stdin=False, passphrase=None)
    got = cli.read_passphrase(args, "new passphrase: ")
    assert got == "santhosh123"
    assert "nothing appears while you type" in seen.getvalue()
    assert seen.getvalue().count("XX") == 2
    assert "santhosh123" not in seen.getvalue(), "the passphrase itself was echoed"


def test_a_six_character_passphrase_is_allowed_and_five_is_not(tmp_path):
    """The owner's own choice was refused by an 8-character floor nobody asked for."""
    config = Config.load(tmp_path / "a")
    config.fast_kdf = True
    Vault(config).initialize("abc123", "Owner", "d1")          # 6: allowed

    config2 = Config.load(tmp_path / "b")
    config2.fast_kdf = True
    with pytest.raises(ValueError, match="at least 6"):
        Vault(config2).initialize("abcde", "Owner", "d2")       # 5: refused


# --------------------------------------------------------------------------- #
# 4. the launcher offers a way OUT, not a dead end
# --------------------------------------------------------------------------- #
def test_the_stuck_screen_names_all_three_ways_in_and_offers_new():
    text = (ROOT / "nila-start.bat").read_text()
    stuck = text[text.index(":stuck"):]
    assert "THE PASSPHRASE" in stuck
    assert "A RECOVERY CODE" in stuck
    assert "START A NEW VAULT" in stuck
    assert "abcde-fghij" in stuck            # what a code looks like
    assert "brain.py new-vault" in stuck     # the way out is a real command
    assert "Nothing was deleted" in stuck or "nothing has been deleted" in stuck.lower()
    # and it is reachable by typing ONE word, never by a parenthesised block that
    # expands the answer before it is typed
    assert 'goto :nila_new_vault' in stuck
    assert "new-vault" in (ROOT / "nila-start.bat").read_text()


def test_the_new_vault_path_starts_the_app_exactly_once():
    text = (ROOT / "nila-start.bat").read_text()
    new = text[text.index(":nila_new_vault"):]
    new = new[:new.index("\n:start")] if "\n:start" in new else new
    assert new.count("brain.py dashboard") == 1, \
        "two servers, one port: 'Address already in use'"
    assert "goto :start" not in new, "the :start block would open a second server"
