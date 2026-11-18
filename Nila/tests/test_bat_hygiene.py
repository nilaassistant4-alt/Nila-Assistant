"""Acceptance tests: the Windows .bat files must be trustworthy.

The owner's complaint was not that a .bat was slow - it was that he could not
tell what a double-click would do. A .bat on Windows fails in four silent ways,
and all four are checked here because none of them can be seen on Linux CI:

1. **LF line endings.** `cmd.exe` mis-parses a batch file that uses bare newlines,
   especially around `if (...) else (...)`. CRLF only.
2. **A non-ASCII byte.** Without a code page the console prints mojibake, and an
   emoji or a Tamil letter breaks `goto`/labels. Every file is pure ASCII; the
   UTF-8 code page is set *by a command*, not by a character.
3. **`python` not found.** A window that flashes and closes leaves the owner
   guessing. Every script that needs Python says so in one sentence and waits.
4. **A path with a space.** Unquoted `%~dp0tools\\x.py` breaks in
   `C:\\Users\\Some Name\\`. Every script path is quoted.

The other half of trust is naming: a file called `start-nila.bat` must start
Nila, and the file that answers the phone must be discoverable from the one the
owner opens first.

Run:  python -m pytest tests/test_bat_hygiene.py -q
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BATS = sorted(ROOT.glob("*.bat"))
#: Scripts a first-run owner is told about, so each must exist and be mentioned.
DOCUMENTED = {"start-nila.bat", "setup.bat", "report.bat", "nila-phone.bat",
              "sync-host.bat", "sync-join.bat", "sync-now.bat"}


def test_there_are_bat_files_and_the_expected_ones_are_present():
    names = {p.name for p in BATS}
    assert len(BATS) >= 8
    assert DOCUMENTED <= names, sorted(DOCUMENTED - names)


@pytest.mark.parametrize("path", BATS, ids=lambda p: p.name)
def test_crlf_line_endings_only(path):
    raw = path.read_bytes()
    assert b"\r\n" in raw
    # every LF is part of a CRLF pair
    stripped = raw.replace(b"\r\n", b"")
    assert b"\n" not in stripped, f"{path.name}: bare LF line ending"
    assert b"\r" not in stripped, f"{path.name}: stray CR"


@pytest.mark.parametrize("path", BATS, ids=lambda p: p.name)
def test_ascii_only_so_no_code_page_can_mangle_it(path):
    raw = path.read_bytes()
    assert raw.startswith(b"@echo off"), f"{path.name}: must begin with @echo off"
    assert not raw.startswith(b"\xef\xbb\xbf"), f"{path.name}: a BOM breaks cmd.exe"
    bad = [(i, b) for i, b in enumerate(raw) if b > 126 or (b < 32 and b not in (9, 10, 13))]
    assert not bad, f"{path.name}: non-ASCII/control bytes at {bad[:3]}"


@pytest.mark.parametrize("path", BATS, ids=lambda p: p.name)
def test_a_double_click_never_leaves_the_owner_without_an_explanation(path):
    text = path.read_text()
    # nila-env.bat and nila-common-check.bat are called, not double-clicked:
    # they must RETURN, never pause or exit the caller.
    if path.name in ("nila-env.bat", "nila-common-check.bat"):
        assert "pause" not in text.lower()
        assert "exit /b" in text.lower() or path.name == "nila-env.bat"
        return
    if path.name == "nila-cmd.bat":
        # a live shell is the point of this one: it stays open by itself
        assert "cmd /k" in text
        return
    assert "pause" in text.lower(), f"{path.name}: the window would close silently"


@pytest.mark.parametrize("path", BATS, ids=lambda p: p.name)
def test_every_script_that_runs_python_first_checks_python(path):
    text = path.read_text().lower()
    if path.name == "nila-common-check.bat":
        return
    runs_python = "brain.py" in text or "-m pip" in text or "tools\\" in text
    if not runs_python:
        return
    assert "nila-env.bat" in text, f"{path.name}: settings must come from one place"
    assert "nila-common-check.bat" in text, f"{path.name}: must not fail with 'not recognized'"


@pytest.mark.parametrize("path", BATS, ids=lambda p: p.name)
def test_a_path_with_a_space_in_it_still_works(path):
    for line in path.read_text().splitlines():
        low = line.strip().lower()
        if low.startswith("rem ") or low.startswith("@rem ") or low.startswith("echo"):
            continue
        if low.startswith("call "):       # a call to another .bat is quoted too
            pass
        # %~dp0 followed by a file name must be inside quotes
        for m in re.finditer(r'(^|[^"])(%~dp0[\\\w.\-]+\.(py|bat))', line, re.I):
            raise AssertionError(f"{path.name}: unquoted {m.group(2)} in: {line.strip()}")


def test_the_settings_file_names_the_vault_in_exactly_one_place():
    env = (ROOT / "nila-env.bat").read_text()
    assert env.count("set \"BRAIN_HOME=") == 1
    assert "LOCALAPPDATA" in env
    assert "if not defined BRAIN_HOME" in env      # an owner override wins
    # UTF-8 for the console AND for files Python writes
    assert "chcp 65001" in env
    assert "PYTHONUTF8=1" in env and "PYTHONIOENCODING=utf-8" in env


def test_the_scripts_call_the_right_python():
    """`py -3` when the launcher exists, `python` otherwise - and one variable,
    so a machine with python2 first on PATH cannot pick the wrong one."""
    env = (ROOT / "nila-env.bat").read_text()
    assert 'set "NILA_PY=python"' in env
    assert "where py" in env
    for path in BATS:
        text = path.read_text()
        if path.name in ("nila-env.bat", "nila-common-check.bat"):
            continue
        # a bare `python brain.py` in a .bat ignores the resolved interpreter
        assert not re.search(r"^\s*python\s+brain\.py", text, re.M), \
            f"{path.name}: calls bare `python` instead of %NILA_PY%"


def test_start_nila_starts_nila_and_nothing_else():
    text = (ROOT / "start-nila.bat").read_text()
    # stage 11 made this one command: `brain dashboard` unlocks once and opens
    # the app, instead of `brain start` (a terminal chat) plus `brain serve`.
    assert "brain.py dashboard" in text
    assert "identity.json" in text           # says "no vault, run setup" instead of crashing
    assert "recovery" in text.lower()        # the break-glass path is reachable
    assert "brain.py serve" not in text      # never both, or two servers start


def test_the_phone_window_exists_and_is_reachable_from_the_docs():
    phone = (ROOT / "nila-phone.bat").read_text()
    assert "brain.py telegram listen" in phone
    assert "identity.json" in phone
    start_here = (ROOT / "START_HERE.md").read_text()
    assert "nila-phone.bat" in start_here
    setup = (ROOT / "setup.bat").read_text()
    assert "nila-phone.bat" in setup


def test_the_two_pc_scripts_are_described_where_the_owner_looks():
    start_here = (ROOT / "START_HERE.md").read_text()
    for name in ("sync-host.bat", "sync-join.bat", "sync-now.bat"):
        assert name in start_here, f"{name} is not mentioned in START_HERE.md"
    # and the explanation has to say the two things he must type, not just name
    # the files: an address and a join code
    assert "join code" in start_here and "192.168" in start_here
    setup = (ROOT / "setup.bat").read_text()
    assert "sync-host.bat" in setup or "START_HERE.md" in setup


def test_report_does_not_pretend_to_have_written_a_report_when_it_failed():
    text = (ROOT / "report.bat").read_text()
    assert "brain.py report" in text
    assert "if errorlevel 1" in text
    assert "no passphrase" in text.lower()


def test_the_backup_script_never_deletes_and_says_its_folder():
    text = (ROOT / "backup-to-d.bat").read_text()
    assert "--yes" in text and "--list" in text
    assert "D:\\Nila-backups" in text
    assert "del " not in text.lower().replace("delete", "")


def test_getffmpeg_is_double_clickable_and_says_what_it_touches():
    text = (ROOT / "getffmpeg.bat").read_text()
    assert "tools\\getffmpeg.py" in text
    assert "pause" in text                        # never closes on the owner
    # it installs two files, not a system package, and says so before doing it
    assert "installs nothing else" in text
    assert "150 MB" in text                       # the size, before the wait
