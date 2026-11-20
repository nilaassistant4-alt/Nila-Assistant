"""Acceptance tests: the start he asked for - one file, one key, memories on D:.

Everything here was measured first, by running the thing the owner runs, and each
test names the defect it stops from coming back.

1. **`"py -3"` was put into quotes.** `nila-env.bat` chose ``NILA_PY="py -3"`` and
   42 places run ``"%NILA_PY%" brain.py ...``. A quoted two-word command is not a
   program name, so the window printed ``The system cannot find the path specified``
   and closed - the "it does not start" report, with no Nila message in it at all.

2. **The shortcut and the sign-in start ran a command that does not parse.**
   ``launcher_args`` appended ``--home X`` AFTER the subcommand; ``--home`` is a
   GLOBAL option, so every generated `.vbs` died with
   ``error: unrecognized arguments: --home``.

3. **They also ran `start`, which was an alias.** ``brain start`` opens the app, but
   the command the docs, the icons and the daily habit use is ``dashboard``; the
   launcher now names that one, and the tests below run it.

4. **The icon was never written.** ``brain hotkey --write`` created a `.lnk` and
   told the owner to set a Shortcut key - and Windows has no API for that box, so
   an owner who did not open Properties had no way in at all. The Desktop icon is
   written by the same command now.

5. **"Move the memory to D:" did not move anything anybody reads.** After a
   verified copy, the old code wrote ``agent.home`` into the old ``config.json``;
   nothing reads that key, and the launchers get their folder from ``BRAIN_HOME``.
   Every start after the "successful" move opened the OLD folder and said the
   memories were not there. ``Config.load`` now follows ``moved-to.json``.

6. **`brain storage --set D:/x` produced a database that could not be opened**
   (``sqlite3.OperationalError: unable to open database file``) because the folder
   was never created - and it never said the old memories stayed behind.

7. **`echo %VAR%|` inside an ``if (...)`` block.** cmd.exe expands the variable
   when it parses the whole block, i.e. before the recovery code is typed, so
   ``start-nila.bat recovery`` sent an empty line forever.
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import sys
from pathlib import Path

import pytest

from brain.config import Config
from brain.memory.store import MemoryStore
from brain.memory.vault import Vault
from brain.security import vaultkey

ROOT = Path(__file__).resolve().parents[1]


class _FullStore(dict):
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
    good = _FullStore()
    monkeypatch.setattr(vaultkey, "_keyring", lambda: good)
    monkeypatch.setattr(vaultkey, "_dpapi_available", lambda: False)
    return good


def _vault(home: Path, passphrase: str = "the-passphrase-i-typed") -> Vault:
    config = Config.load(home)
    config.fast_kdf = True
    vault = Vault(config)
    vault.initialize(passphrase, "Owner", f"dev-{home.name}")
    return vault


# --------------------------------------------------------------------------- #
# 1. every .bat runs a python that cmd.exe can actually find
# --------------------------------------------------------------------------- #
def test_the_python_the_scripts_run_is_one_quotable_token():
    env = (ROOT / "nila-env.bat").read_text()
    assert 'set "NILA_PY=py -3"' not in env, \
        'a quoted two-word command is not a program ("The system cannot find the path specified")'
    # it asks the launcher for the real executable path, so the value has no space
    assert "sys.executable" in env
    assert 'for /f' in env.lower()
    # and there is always a last-resort value, so the scripts never see an empty var
    assert 'if not defined NILA_PY set "NILA_PY=python"' in env


def _no_parse_time_expansion(path: Path) -> None:
    """The one rule: a `set /p` and an `echo %X%|` in the SAME (...) block is a bug."""
    text = path.read_text(errors="replace")
    for block in re.finditer(r"\((\r?\n.*?)\r?\n\)", text, re.S):
        typed = {m.group(1) for m in
                 re.finditer(r"set /p\s+(\w+)=", block.group(1))}
        for name in typed:
            if re.search(r"(?m)^\s*echo\s+%" + name + r"%\s*\|", block.group(1)):
                raise AssertionError(
                    f"{path.name}: %{name}% is expanded inside a (...) block that also "
                    f"contains `set /p {name}=` - cmd.exe expands it before the owner "
                    f"types, so an empty line is sent")


def test_no_script_uses_a_variable_the_block_would_expand_too_early(monkeypatch):
    for path in sorted(ROOT.glob("*.bat")):
        _no_parse_time_expansion(path)


def test_the_detector_fails_on_the_shape_it_exists_to_catch():
    bad = ROOT / "tests" / "_tmp_parse_time_trap.bat"
    bad.write_bytes(b"@echo off\r\nif exist x (\r\n  set /p CODE=code: \r\n"
                    b"  echo %CODE%| python brain.py dashboard\r\n)\r\npause\r\n")
    try:
        with pytest.raises(AssertionError, match="expanded inside"):
            _no_parse_time_expansion(bad)
    finally:
        bad.unlink()


def test_the_recovery_code_is_typed_outside_the_if_and_never_on_the_command_line():
    text = (ROOT / "start-nila.bat").read_text()
    assert "goto :nila_recovery" in text
    assert re.search(r"\r?\n:nila_recovery\r?\n", text)
    assert 'echo %NILA_CODE%| "%NILA_PY%" brain.py dashboard --recovery-stdin' in text
    # the code is never an argument (Task Manager, ps, shell history)
    assert "--recovery " not in text and '--recovery %' not in text


# --------------------------------------------------------------------------- #
# 2 + 3. the generated launchers parse, and they run the documented command
# --------------------------------------------------------------------------- #
def test_the_launcher_puts_global_options_before_the_command():
    from brain.util import launcher

    assert launcher.launcher_args(r"C:\vault", command="dashboard") == \
        ["--home", r"C:\vault", "dashboard"]
    assert launcher.launcher_args("", extra_args=["talk"]) == ["dashboard", "talk"]


def test_the_generated_vbs_parses_in_the_real_parser():
    from brain.cli import main as cli
    from brain.util import launcher

    vbs = launcher.windows_vbs("C:/py/python.exe", "C:/nila/brain.py",
                              "C:/Users/me/AppData/Local/Nila/vault")
    # take the arguments back out of the generated line and hand them to argparse
    line = [ln for ln in vbs.splitlines() if ln.startswith("sh.Run")][0]
    argv = re.findall(r'""([^"]*)""', line)
    assert argv[0] == "C:/py/python.exe"
    argv = argv[2:]                     # drop python and brain.py
    parsed = cli.build_parser().parse_args(argv)
    assert Path(parsed.home) == Path("C:/Users/me/AppData/Local/Nila/vault")
    assert parsed.command == "dashboard", \
        "the shortcut must run the documented command, not an alias"


def test_the_startup_shortcut_and_the_icons_agree(tmp_path, monkeypatch):
    from brain.cli import main as cli
    from brain.util import launcher

    home = tmp_path / "vault"
    home.mkdir()
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    out = launcher.write_startup(home)
    text = Path(out["written"]).read_text()
    exec_line = [ln for ln in text.splitlines() if ln.startswith("Exec=")][0]
    assert exec_line.split()[-1] == "dashboard", exec_line
    parsed = cli.build_parser().parse_args(["--home", str(home), "dashboard"])
    assert parsed.command == "dashboard"


def test_an_icon_points_at_the_program_and_not_at_the_data_folder(tmp_path):
    """`<vault>/brain.py` does not exist. A shortcut to it opens and closes."""
    from brain.util import launcher

    home = tmp_path / "vault"
    home.mkdir()
    out = launcher.write_launchers(home, out_dir=tmp_path / "Desktop")
    assert out["ok"]
    text = "\n".join(Path(p).read_text() for p in out["written"]
                     if Path(p).suffix in (".desktop", ".vbs", ".bat", ".command"))
    assert str(home / "brain.py") not in text, "the icon points at a file that is not there"
    assert "dashboard" in text


def test_the_hotkey_command_writes_the_desktop_icon_too(tmp_path, monkeypatch, store):
    """"One key" was a .lnk plus a note about a Windows dialog. The icon is the part
    that always works, so it is written by the same command."""
    from brain.cli import main as cli
    from brain.util import hotkey

    home = tmp_path / "vault"
    vault = _vault(home)
    vaultkey.save(vault.unlock("the-passphrase-i-typed"), home)
    vault.lock()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(hotkey, "where", lambda start_menu=False: tmp_path / "Desktop")
    wrote: list = []
    monkeypatch.setattr(cli, "print", lambda *a, **k: wrote.append(a), raising=False)
    args = cli.build_parser().parse_args(["--home", str(home), "hotkey", "--write"])
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        assert cli.cmd_hotkey(args, cli.UI()) == 0
    written = list((tmp_path / "Desktop").iterdir()) if (tmp_path / "Desktop").exists() else []
    assert written, "no icon was written: " + buf.getvalue()


# --------------------------------------------------------------------------- #
# 4. the memories on D: - the pointer every start reads
# --------------------------------------------------------------------------- #
def _move_tool():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "move_storage_s17", ROOT / "tools" / "move_storage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_after_a_verified_move_the_same_brains_home_opens_the_new_folder(tmp_path):
    home = tmp_path / "vault"
    vault = _vault(home)
    vault.lock()
    target = tmp_path / "D" / "Nila-data"

    assert _move_tool().move(home, target, dry_run=False, yes=True) == 0
    # the launcher still says BRAIN_HOME=<old place>; that must now be a pointer
    config = Config.load(home)
    assert config.home == target
    assert (target / "identity.json").exists()
    # and it is the same vault: the passphrase that was set still opens it
    reopened = Vault(config)
    assert reopened.verify_passphrase("the-passphrase-i-typed") is True


def test_the_remembered_key_survives_the_move_instead_of_asking_again(store, tmp_path):
    """The device key is sealed with the folder it was saved for. Moving the data
    (a byte-verified copy) must not turn "opens without typing" into a prompt."""
    home = tmp_path / "vault"
    vault = _vault(home)
    from brain.security import vaultkey as vk

    vk.save(vault.unlock("the-passphrase-i-typed"), home)
    vault.lock()
    target = tmp_path / "D" / "Nila-data"
    assert _move_tool().move(home, target, dry_run=False, yes=True) == 0

    moved = Config.load(home)
    assert vk.status(moved.home)["remembered"] is True, \
        "the key was dropped for a folder whose contents are byte-identical"
    assert vk.load(moved.home) == vk.load(home)


def test_an_unplugged_drive_is_named_and_nothing_is_written_to_c(tmp_path):
    home = tmp_path / "vault"
    vault = _vault(home)
    vault.lock()
    target = tmp_path / "D" / "Nila-data"
    assert _move_tool().move(home, target, dry_run=False, yes=True) == 0
    target.rename(tmp_path / "unplugged")

    config = Config.load(home)
    assert config.home == home
    assert config.moved_to_missing == str(target)
    assert (home / "identity.json").exists()


# --------------------------------------------------------------------------- #
# 5. "only the memories on D:" must actually work
# --------------------------------------------------------------------------- #
def test_setting_a_storage_folder_makes_the_folder_and_the_store_opens(tmp_path):
    from brain.config import StorageUnavailable

    home = tmp_path / "vault"
    vault = _vault(home)
    mk = vault.unlock("the-passphrase-i-typed")
    vault.start_session(mk, mode="setup")
    config = Config.load(home)
    config.agent = {"storage_dir": str(tmp_path / "D" / "Nila-memories"),
                    "storage_create": True}
    config.save()

    fresh = Config.load(home)
    store = None
    try:
        store = MemoryStore(fresh, mk, session=vault.session)
        store.remember(kind="note", title="bike", body="bike number is 1234")
        assert (fresh.memory_path).exists()
        assert store.search("bike")
    finally:
        if store is not None:
            store.close()
        vault.lock()


def test_a_drive_that_is_not_there_is_still_a_refusal_not_a_smaller_database(tmp_path):
    from brain.config import StorageUnavailable

    home = tmp_path / "vault"
    home.mkdir()
    config = Config.load(home)
    config.agent = {"storage_dir": "Q:/no-such-drive/Nila"}
    config.save()
    with pytest.raises(StorageUnavailable):
        _ = Config.load(home).memory_path


def test_switching_the_folder_says_where_the_old_memories_stayed(tmp_path):
    """A new, empty store opening silently is how "my memories are gone" happens."""
    from brain.cli import main as cli

    home = tmp_path / "vault"
    vault = _vault(home)
    mk = vault.unlock("the-passphrase-i-typed")
    vault.start_session(mk, mode="setup")
    first = MemoryStore(Config.load(home), mk, session=vault.session)
    first.remember(kind="note", title="amma", body="call amma on sunday")
    first.close()
    assert (home / "memory.db").stat().st_size > 0
    vault.lock()

    args = cli.build_parser().parse_args(
        ["--home", str(home), "storage", "--set", str(tmp_path / "D" / "memories")])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert cli.cmd_storage(args, cli.UI()) == 0
    assert "left_behind" in buf.getvalue()
    assert str(home / "memory.db") in buf.getvalue()
    assert "move-to-d.bat" in buf.getvalue()
    # and the setting is where the loader reads it
    assert "storage_dir" in (home / "config.json").read_text()


# --------------------------------------------------------------------------- #
# 6. the two "move to D:" scripts both exist and ask before either one happens
# --------------------------------------------------------------------------- #
def test_both_move_scripts_exist_and_neither_one_deletes_anything():
    names = ["move-to-d.bat", "move-memories-to-d.bat"]
    for name in names:
        raw = (ROOT / name).read_bytes()
        assert raw.startswith(b"@echo off")
        assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
        text = raw.decode("ascii")
        assert "move_storage.py" in text
        assert "--dry-run" in text, f"{name} copies without showing the check first"
        assert "YES" in text, f"{name} does not ask before copying"
        assert "del " not in text.lower()
    # the memories-only choice is reachable from the file the owner already knows
    assert "move-memories-to-d.bat" in (ROOT / "move-to-d.bat").read_text()
    assert "move-memories-to-d.bat" in (ROOT / "setup.bat").read_text()
    # and it explains the pointer, so "the vault is still on C:" has an answer
    assert "pointer" in (ROOT / "move-memories-to-d.bat").read_text()


# --------------------------------------------------------------------------- #
# 7. one double-click path: the launcher, its checks, its messages
# --------------------------------------------------------------------------- #
def test_the_launcher_says_where_the_memories_are_and_offers_one_key(tmp_path):
    from brain.cli import main as cli

    home = tmp_path / "vault"
    vault = _vault(home)
    vault.lock()
    args = cli.build_parser().parse_args(["--home", str(home), "come-from"])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert cli.cmd_come_from(args, cli.UI()) == 0
    text = buf.getvalue()
    assert "memories stored at" in text
    assert str(home / "memory.db") in text


def test_the_daily_file_offers_the_icon_once_and_never_blocks_the_start():
    text = (ROOT / "nila-start.bat").read_text()
    assert "nila-start.vbs" in text              # the marker the icon writes
    assert "hotkey --write" in text
    assert "dashboard" in text                   # still starts Nila either way
    assert "never stop" in text or "works anyway" in text
