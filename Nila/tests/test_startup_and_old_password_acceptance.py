"""Acceptance tests: "I extracted it, it asks for a password, and my old one still works".

Three separate failures hid inside that one sentence, and each has a test here.

1. **"It said it remembered, and then asked again."** `vaultkey.save` returned
   ``saved: True`` without ever reading the value back, so a secret store that
   accepts a write and hands back nothing (a locked Credential Locker, a headless
   Secret Service) reported success. Every start then asked for the passphrase - the
   exact complaint - while `brain passphrase status` said YES.

2. **"I changed my password and the old one still works."** `change-passphrase` asked
   for the OLD passphrase first. An owner who does not know it answers wrongly or
   gives up, nothing changes, and the command looks like it worked. When the vault was
   opened by the saved device key or a recovery code, the old passphrase is now not
   asked for at all - and a test proves the old one stops working afterwards.

3. **"It asks for a password" is not a bug report anyone can act on.** `brain
   come-from` reads the machine and says which of the four situations it is:
   no vault, remembered, a usable recovery code, or a passphrase only.

Also pinned: a vault made by `Nila-v0.1.0.zip` (kdf n=4096) still opens here, so a
fresh extract does NOT mean a new vault.

Run:  python -m pytest tests/test_startup_and_old_password_acceptance.py -q
"""
from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from brain.config import Config
from brain.memory.vault import Vault
from brain.security import vaultkey


class _FullStore(dict):
    """A keyring that behaves (write, then read the same value back)."""

    def get_keyring(self):
        return "test store"

    def set_password(self, service, user, value):
        self[(service, user)] = value

    def get_password(self, service, user):
        return self.get((service, user))

    def delete_password(self, service, user):
        self.pop((service, user), None)


class _AcceptThenForget(_FullStore):
    """Accepts a write and returns nothing - the real Windows failure mode."""

    def get_password(self, service, user):
        return None


@pytest.fixture()
def store(monkeypatch):
    good = _FullStore()
    monkeypatch.setattr(vaultkey, "_keyring", lambda: good)
    monkeypatch.setattr(vaultkey, "_dpapi_available", lambda: False)
    return good


# --------------------------------------------------------------------------- #
# 1. "it says it remembered" must be true
# --------------------------------------------------------------------------- #
def test_a_store_that_swallows_the_key_is_reported_as_a_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(vaultkey, "_keyring", lambda: _AcceptThenForget())
    monkeypatch.setattr(vaultkey, "_dpapi_available", lambda: False)
    with pytest.raises(vaultkey.VaultKeyError, match="did not return it when read back"):
        vaultkey.save(b"k" * 32, tmp_path)
    # and the status that the owner reads agrees: nothing is remembered
    assert vaultkey.status(tmp_path)["remembered"] is False


def test_a_working_store_is_saved_and_verified(store, tmp_path):
    out = vaultkey.save(b"m" * 32, tmp_path)
    assert out["saved"] is True and out["verified"] is True
    assert vaultkey.load(tmp_path) == b"m" * 32


def test_a_key_for_another_vault_is_dropped_instead_of_blocking_forever(store, tmp_path):
    """A saved key for a vault that has been moved is useless, and `save` refuses to
    overwrite a stored value: that combination is a permanent "it will not remember me".
    Reading it now clears it, so the next save can succeed."""
    vaultkey.save(b"1" * 32, tmp_path / "old-home")
    # a new vault in a new folder, same keyring entry
    new_home = tmp_path / "new-home"
    new_home.mkdir()
    assert vaultkey.load(new_home) is None
    assert vaultkey.save(b"2" * 32, new_home)["saved"] is True
    assert vaultkey.load(new_home) == b"2" * 32


# --------------------------------------------------------------------------- #
# 2. changing the passphrase without knowing the old one
# --------------------------------------------------------------------------- #
def _make_vault(home: Path, passphrase: str = "the-passphrase-i-forgot") -> Config:
    config = Config.load(home)
    config.fast_kdf = True
    vault = Vault(config)
    vault.initialize(passphrase, "Owner", "dev-1")
    vault.lock()
    return config


def test_change_passphrase_does_not_ask_for_the_old_one_when_the_key_opened_it(store, tmp_path):
    from brain.cli import main as cli

    home = tmp_path / "vault"
    config = _make_vault(home)
    vault = Vault(config)
    vaultkey.save(vault.unlock("the-passphrase-i-forgot"), home)
    vault.lock()

    parser = cli.build_parser()
    monkeypatch_stdin = io.StringIO("a-new-paper-passphrase\n")
    real_stdin, sys.stdin = sys.stdin, monkeypatch_stdin
    try:
        args = parser.parse_args(["--home", str(home), "change-passphrase",
                                  "--new-passphrase-stdin"])
        with contextlib.redirect_stdout(io.StringIO()):
            assert cli.cmd_change_passphrase(args, cli.UI()) == 0
    finally:
        sys.stdin = real_stdin

    fresh = Vault(Config.load(home))
    assert fresh.verify_passphrase("the-passphrase-i-forgot") is False, \
        "the OLD passphrase still works - this is exactly what the owner reported"
    assert fresh.verify_passphrase("a-new-paper-passphrase") is True
    # the memories are keyed by the master key, which did not change
    assert Vault(Config.load(home)).unlock_with_master_key(
        vaultkey.load(home)).mode == "device-key"


def test_the_audit_says_the_old_passphrase_was_not_needed(store, tmp_path):
    from brain.cli import main as cli

    home = tmp_path / "vault"
    config = _make_vault(home)
    vault = Vault(config)
    vaultkey.save(vault.unlock("the-passphrase-i-forgot"), home)
    vault.lock()
    parser = cli.build_parser()
    real_stdin, sys.stdin = sys.stdin, io.StringIO("another-paper-pass-2\n")
    try:
        args = parser.parse_args(["--home", str(home), "change-passphrase",
                                  "--new-passphrase-stdin"])
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_change_passphrase(args, cli.UI())
    finally:
        sys.stdin = real_stdin
    import json

    rows = [json.loads(line) for line in
            config.audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(r["event"] == "passphrase_changed_by_owner"
               and r["data"].get("old_passphrase_required") is False for r in rows)


# --------------------------------------------------------------------------- #
# 3. "it asks for a password" - said in a way he can act on
# --------------------------------------------------------------------------- #
def _come_from(home: Path) -> dict:
    from brain.cli import main as cli

    args = cli.build_parser().parse_args(["--home", str(home), "come-from", "--json"])
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert cli.cmd_come_from(args, cli.UI(json_out=True)) == 0
    import json

    return json.loads(out.getvalue())


def test_come_from_names_the_four_situations(store, tmp_path):
    empty = tmp_path / "nothing-here"
    assert _come_from(empty)["vault_exists"] is False
    assert "nila-first-run.bat" in _come_from(empty)["what_to_do"]

    home = tmp_path / "vault"
    config = _make_vault(home)
    remembered = _come_from(home)
    assert remembered["vault_exists"] is True and remembered["remembered"] is False
    assert "passphrase" in remembered["what_to_do"]

    vault = Vault(config)
    vaultkey.save(vault.unlock("the-passphrase-i-forgot"), home)
    vault.lock()
    after = _come_from(home)
    assert after["remembered"] is True
    assert "start-nila.bat" in after["what_to_do"]


def test_come_from_says_when_a_vault_was_moved(store, tmp_path):
    home = tmp_path / "vault"
    config = _make_vault(home)
    vault = Vault(config)
    vault.start_session(vault.unlock("the-passphrase-i-forgot"))
    vault.note_home()
    vault.lock()
    moved = tmp_path / "somewhere-else"
    shutil.copytree(home, moved)
    assert _come_from(moved)["moved_from"] == str(home.resolve())


def test_come_from_counts_only_codes_that_can_actually_open_it(store, tmp_path):
    from brain.identity.ceremony import IdentityCeremony

    home = tmp_path / "vault"
    config = _make_vault(home)
    vault = Vault(config)
    vault.start_session(vault.unlock("the-passphrase-i-forgot"))
    IdentityCeremony(config, vault).enroll_recovery_codes(3)
    # pretend they were issued by an older build: hashes, no wraps
    vault.data["recovery_wraps"]["wraps"] = {}
    vault.save()
    vault.lock()
    out = _come_from(home)
    assert out["recovery_codes_left"] == 3
    assert out["recovery_codes_that_can_open_it"] == 0
    assert "older build" in out["what_to_do"]


# --------------------------------------------------------------------------- #
# 4. a fresh extract is not a new vault - the old format still opens
# --------------------------------------------------------------------------- #
def _build_v1_style_vault(home: Path, passphrase: str) -> None:
    """Exactly what `Nila-v0.1.0.zip` wrote: kdf n=4096, one device, no new fields."""
    from brain.crypto.envelope import encrypt
    from brain.crypto.hash import derive_key, mac, random_bytes
    from brain.util.misc import b64e, now_ms

    config = Config.load(home)
    mk = random_bytes(32)
    salt = random_bytes(16)
    kdf = {"alg": "scrypt", "n": 4096, "r": 8, "p": 1, "salt": b64e(salt)}
    kek = derive_key(passphrase.encode(), salt, b"pbx/kek/v1", 32, n=4096, r=8, p=1)
    dev_id = "dev-v1test"
    identity = {"voice": None, "totp": None, "recovery": None, "hardware_wrap": None,
                "policy": {"max_attempts": 5, "lockout_base_seconds": 30,
                           "require_voice": True, "require_liveness": True,
                           "allow_degraded": False},
                "meta": {}, "sessions": {}}
    import json

    from brain.crypto.envelope import encrypt as _enc
    from brain.memory.vault import IDENT_AAD, SESSION_TAG

    blob = _enc(json.dumps(identity).encode(), mk, aad=IDENT_AAD)
    data = {
        "version": 1, "created_at": now_ms(), "user": "Owner", "device_id": dev_id,
        "kdf": kdf,
        "devices": {dev_id: {"kek_wrap": b64e(encrypt(mk, kek, aad=b"pbx/wrap/kek")),
                             "created_at": now_ms(), "label": "v1", "primary": True}},
        "identity_blob": b64e(blob),
        "verifier": b64e(mac(mk, SESSION_TAG)),
        "policy": {}, "attempts": {"count": 0, "locked_until": 0, "last_failure": 0},
    }
    (home).mkdir(parents=True, exist_ok=True)
    (home / "identity.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_a_vault_from_the_first_release_still_opens_here(tmp_path):
    """`Nila-v0.1.0.zip` wrote kdf n=4096; today's default differs. The KDF params are
    read FROM THE VAULT, so the old vault opens instead of looking like a wrong
    password. If this ever fails, re-extracting silently costs the owner his memories -
    the worst possible answer to "it asks for a password"."""
    home = tmp_path / "v1-vault"
    _build_v1_style_vault(home, "old-build-pass-1")
    fresh = Vault(Config.load(home))
    assert fresh.initialized is True
    assert fresh.verify_passphrase("old-build-pass-1") is True
    assert fresh.verify_passphrase("a-different-passphrase") is False


# --------------------------------------------------------------------------- #
# 5. the one file he double-clicks
# --------------------------------------------------------------------------- #
def test_the_one_double_click_file_exists_and_says_where_the_vault_is():
    root = Path(__file__).resolve().parents[1]
    text = (root / "nila-start.bat").read_bytes().decode("ascii")
    assert "\r\n" in text and "\n" not in text.replace("\r\n", "")
    assert "%BRAIN_HOME%" in text, "it must name the folder the memories live in"
    assert "brain.py come-from" in text, "it must diagnose, not guess"
    assert "brain.py passphrase remember" in text
    assert "--recovery-stdin" in text, "the code must never be an argument"
    # nothing here may delete or re-create a vault
    for dangerous in ("rmdir", "del ", "rd /s", "format "):
        assert dangerous not in text.lower()
