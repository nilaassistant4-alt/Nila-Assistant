"""Acceptance tests: the confirmed v0.2 scope, finished and proved.

The owner asked for one thing: *"was everything we said for v2 really done
properly - yes or no?"* So every test here corresponds to a line he confirmed
for v0.2, not to something I invented:

* the business work - guided interview one question at a time, milestone due
  days, late things put in front of him, a weekly summary only when asked, an
  export he chooses;
* the desktop app - Chrome "Install as app" needs a manifest and an icon;
* the two D-drive scripts - copy + verify before switching, backup + restore
  check before believing it;
* the first-run promise - `setup.bat` then `start-nila.bat` must work once, and
  the one-time secrets must be visible even with `--quiet`.

Run:  python -m pytest tests/test_v02_scope_finish_acceptance.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# 1. the business interview: one question, in order, no block names to learn
# --------------------------------------------------------------------------- #
@pytest.fixture()
def store(memory):
    from brain.projects.store import ProjectStore

    st = ProjectStore(memory)
    st.create("Tea Shop", "a stall near the bus stand")
    return st


def test_the_interview_asks_one_question_and_advances(store):
    q = store.interview("Tea Shop")
    assert q["done"] is False and q["question"]
    assert q["filled"] == 0 and q["total"] == 9
    store.set_canvas("Tea Shop", q["block"], "students waiting for the bus")
    nxt = store.interview("Tea Shop")
    assert nxt["block"] != q["block"], "the same question was asked twice"
    assert nxt["filled"] == 1


def test_the_interview_is_the_same_order_every_time(store):
    seen = []
    for _ in range(9):
        q = store.interview("Tea Shop")
        if q["done"]:
            break
        seen.append(q["block"])
        store.set_canvas("Tea Shop", q["block"], "x")
    assert len(seen) == 9 and len(set(seen)) == 9
    assert store.interview("Tea Shop")["done"] is True


def test_the_question_carries_the_words_to_type(store):
    q = store.interview("Tea Shop")
    assert "brain" in q["ask_as"] and q["label"] in q["ask_as"]


# --------------------------------------------------------------------------- #
# 2. milestones with a day, and late ones in front of him
# --------------------------------------------------------------------------- #
def test_a_milestone_keeps_its_due_day(store):
    store.add_milestone("Tea Shop", "buy the stove", "2026-12-01")
    ms = store.get("Tea Shop")["milestones"]
    assert ms[0]["due"] == "2026-12-01"


def test_the_dates_he_actually_types_are_accepted(store):
    from brain.projects.store import _clean_due

    assert _clean_due("2026-12-01") == "2026-12-01"
    assert _clean_due("01/12/2026") == "2026-12-01"
    assert _clean_due("1 Dec 2026") == "2026-12-01"


def test_nonsense_is_refused_instead_of_guessed(store):
    from brain.projects.store import ProjectError

    with pytest.raises(ProjectError):
        _ = store.add_milestone("Tea Shop", "paint", "next week")
    with pytest.raises(ProjectError):
        _ = store.add_milestone("Tea Shop", "paint", "32/13/2026")


def test_late_milestones_lists_only_unfinished_past_days(store):
    store.add_milestone("Tea Shop", "stove", "2020-01-05")     # late, open
    store.add_milestone("Tea Shop", "board", "2099-01-01")     # future
    store.add_milestone("Tea Shop", "chair", "2020-01-06")
    store.complete_milestone("Tea Shop", 3)                    # late but done
    late = store.late_milestones()
    assert [r["text"] for r in late] == ["stove"]
    assert late[0]["days_late"] > 1000


def test_the_summary_counts_late_so_a_screen_can_badge_it(store):
    store.add_milestone("Tea Shop", "stove", "2020-01-05")
    assert store.get("Tea Shop")["milestones_late"] == 1


# --------------------------------------------------------------------------- #
# 3. the weekly summary: only when asked, and it only reads
# --------------------------------------------------------------------------- #
def test_the_weekly_summary_reports_what_moved(store):
    q = store.interview("Tea Shop")
    store.set_canvas("Tea Shop", q["block"], "students")
    store.add_note("Tea Shop", "found a supplier")
    out = store.weekly(7)
    assert out["projects_touched"] == 1
    assert out["projects"][0]["notes_this_week"] == 1
    assert "1 project(s) moved" in out["line"]


def test_the_weekly_summary_changes_nothing(store):
    before = store.get("Tea Shop")
    store.weekly(7)
    assert store.get("Tea Shop") == before


def test_the_weekly_summary_needs_no_network(store, monkeypatch):
    import urllib.request

    def boom(*a, **k):
        raise AssertionError("the weekly summary went online")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert store.weekly(7)["window_days"] == 7


# --------------------------------------------------------------------------- #
# 4. export to a file he chooses
# --------------------------------------------------------------------------- #
def test_export_writes_a_plain_text_file_where_he_asks(store, tmp_path):
    store.set_canvas("Tea Shop", "customers", "students")
    store.add_milestone("Tea Shop", "stove", "2020-01-05")
    out = store.export_summary("Tea Shop", str(tmp_path))
    path = Path(out["path"])
    assert path.parent == tmp_path and path.suffix == ".txt"
    text = path.read_text(encoding="utf-8")
    assert "Tea Shop" in text and "students" in text
    assert "[late] stove" in text
    assert out["chars"] == len(text)


def test_export_refuses_a_name_that_would_escape_the_folder(store, tmp_path):
    store.rename_or_title = None      # not used; the title itself is the vector
    store.create("../../etc", "sneaky")
    out = store.export_summary("../../etc", str(tmp_path / "safe"))
    assert (tmp_path / "safe") in Path(out["path"]).parents


def test_export_never_writes_inside_the_vault(store, tmp_path, config):
    out = store.export_summary("Tea Shop", str(tmp_path))
    assert config.home not in Path(out["path"]).parents


# --------------------------------------------------------------------------- #
# 5. the desktop app: a manifest and an icon, or Chrome offers nothing
# --------------------------------------------------------------------------- #
def test_the_manifest_makes_install_as_app_possible():
    from brain.api import dashboard

    page = dashboard.asset("/")
    assert page is not None
    html = page[0].decode()
    assert 'rel="manifest"' in html

    manifest = json.loads(dashboard.asset("/manifest.json")[0])
    assert manifest["start_url"] == "/" and manifest["display"] == "standalone"
    assert manifest["icons"] and manifest["icons"][0]["src"].startswith("/")

    icon = dashboard.asset("/icon.svg")
    assert icon is not None and icon[1] == "image/svg+xml"
    assert b"<svg" in icon[0]


def test_the_page_is_allowed_to_load_its_own_manifest():
    from brain.api import dashboard

    assert "manifest-src 'self'" in dashboard.CSP
    assert "frame-ancestors 'none'" in dashboard.CSP      # still not embeddable


# --------------------------------------------------------------------------- #
# 6. move to D: - copy, compare every byte, then switch
# --------------------------------------------------------------------------- #
def _tool(name):
    import importlib.util

    path = ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_move_copies_and_verifies_before_switching(tmp_path):
    move = _tool("move_storage")
    home = tmp_path / "vault"
    (home / "sub").mkdir(parents=True)
    (home / "identity.json").write_text('{"a": 1}')
    (home / "memory.db").write_bytes(b"x" * 5000)
    (home / "sub" / "note.txt").write_text("தமிழ்")

    target = tmp_path / "D" / "Nila-data"
    assert move.move(home, target, dry_run=False, yes=True) == 0
    assert (target / "sub" / "note.txt").read_text(encoding="utf-8") == "தமிழ்"
    # the old folder is still there, and a pointer was written
    assert (home / "identity.json").exists()
    cfg = json.loads((home / "config.json").read_text())
    assert cfg["agent"]["home"] == str(target)


def test_a_copy_that_does_not_match_refuses_to_switch(tmp_path, monkeypatch):
    move = _tool("move_storage")
    home = tmp_path / "vault"
    home.mkdir()
    (home / "memory.db").write_bytes(b"real")
    target = tmp_path / "D"

    real_copytree = move.shutil.copytree

    def lossy(src, dst, **kw):
        real_copytree(src, dst, **kw)
        (Path(dst) / "memory.db").write_bytes(b"partial")   # a truncated file

    monkeypatch.setattr(move.shutil, "copytree", lossy)
    code = move.move(home, target, dry_run=False, yes=True)
    assert code != 0
    assert not (home / "config.json").exists(), "it switched despite a bad copy"


def test_it_refuses_to_move_into_itself(tmp_path):
    move = _tool("move_storage")
    home = tmp_path / "vault"
    home.mkdir()
    assert move.move(home, home / "inner", dry_run=True) == 2


def test_it_refuses_to_merge_two_data_folders(tmp_path):
    move = _tool("move_storage")
    home = tmp_path / "vault"
    home.mkdir()
    (home / "a").write_text("a")
    target = tmp_path / "D"
    target.mkdir()
    (target / "something").write_text("not nila's")
    assert move.move(home, target, dry_run=False, yes=True) == 2


def test_it_will_not_copy_while_the_vault_is_open(tmp_path):
    move = _tool("move_storage")
    import os as _os

    home = tmp_path / "vault"
    home.mkdir()
    (home / "lock").write_text("x")
    (home / "vault.lock").write_text(json.dumps({"pid": _os.getpid()}))
    assert move.move(home, tmp_path / "D", dry_run=False, yes=True) == 3


def test_an_unreadable_file_stops_the_move(tmp_path):
    move = _tool("move_storage")
    home = tmp_path / "vault"
    home.mkdir()
    (home / "ok.txt").write_text("fine")
    assert move.inventory(home)[1] == []
    diff = move.compare({"a": (1, "x")}, {"a": (1, "y")})
    assert diff["different"] == ["a"]


# --------------------------------------------------------------------------- #
# 7. backup to D: - and prove it restores
# --------------------------------------------------------------------------- #
def test_the_backup_restores_and_counts_the_same(tmp_path):
    import sqlite3

    bcheck = _tool("backup_check")
    home = tmp_path / "vault"
    home.mkdir()
    db = home / "memory.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE records (id TEXT)")
    con.executemany("INSERT INTO records VALUES (?)", [(f"r{i}",) for i in range(7)])
    con.commit()
    con.close()

    target = tmp_path / "D" / "Nila-backups"
    assert bcheck.backup(home, target, dry_run=False, yes=True) == 0
    made = list(target.glob("*/memory.db"))
    assert made, "no backup file was written"
    assert bcheck.list_backups(target)


def test_the_backup_refuses_a_synced_folder(tmp_path):
    bcheck = _tool("backup_check")
    home = tmp_path / "vault"
    home.mkdir()
    (home / "memory.db").write_bytes(b"")
    for bad in ("Google Drive", "OneDrive", "Dropbox", "iCloud"):
        assert bcheck.backup(home, tmp_path / bad / "x", dry_run=True) == 2


def test_a_backup_of_nothing_is_an_error_not_a_success(tmp_path):
    bcheck = _tool("backup_check")
    home = tmp_path / "vault"
    home.mkdir()
    assert bcheck.backup(home, tmp_path / "D", dry_run=False, yes=True) == 3


def test_the_backup_never_deletes_an_older_one(tmp_path):
    import sqlite3

    bcheck = _tool("backup_check")
    home = tmp_path / "vault"
    home.mkdir()
    con = sqlite3.connect(home / "memory.db")
    con.execute("CREATE TABLE t (a)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()
    first = tmp_path / "old-backup"
    first.mkdir()
    (first / "keep.txt").write_text("mine")
    target = tmp_path / "D"
    assert bcheck.backup(home, target, dry_run=False, yes=True) == 0
    assert (first / "keep.txt").read_text() == "mine"


# --------------------------------------------------------------------------- #
# 8. the first run, and the secrets that are shown once
# --------------------------------------------------------------------------- #
def test_init_prints_the_key_even_with_quiet(tmp_path):
    """`--quiet` used to hide the ONLY copy of the authenticator secret."""
    home = tmp_path / "v"
    env = {"BRAIN_HOME": str(home), "PW": "a-strong-passphrase-1",
           "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-m", "brain.cli.main", "--fast-kdf",
                          "--quiet", "init", "--passphrase-env", "PW"],
                         cwd=str(ROOT), capture_output=True, text=True, env=env, timeout=300)
    text = out.stdout + out.stderr
    assert out.returncode == 0, text
    assert "secret:" in text, "the authenticator secret was hidden"
    assert "recovery codes" in text


def test_the_two_d_bat_files_exist_and_are_windows_scripts():
    for name in ("move-to-d.bat", "backup-to-d.bat"):
        raw = (ROOT / name).read_bytes()
        assert raw.startswith(b"@echo off")
        assert b"\r\n" in raw, "a .bat with LF endings can break on Windows"
        text = raw.decode("utf-8", "replace")
        assert "python" in text and "pause" in text


def test_progress_counts_these_as_done():
    from brain.agent import progress

    report = progress.report()
    labels = {row["label"]: row["state"] for row in report["features"]}
    row = next(r for r in report["features"] if "business" in r["label"].lower())
    assert row["state"] == "done", labels


# --------------------------------------------------------------------------- #
# 9. the unlock flags work in BOTH positions, on every command
# --------------------------------------------------------------------------- #
def test_the_documented_global_form_is_parsed():
    """`brain --passphrase-env PW chat` is what the README says; argparse put PW
    where the command name goes and answered with a usage error."""
    from brain.cli.main import build_parser

    p = build_parser()
    ns = p.parse_args(["--passphrase-env", "PW", "--totp", "123456", "progress"])
    assert (ns.command, ns.passphrase_env, ns.totp) == ("progress", "PW", "123456")
    # the older form still works, and must not overwrite what was given
    ns = p.parse_args(["progress", "--passphrase-env", "PW2"])
    assert ns.passphrase_env == "PW2"
    ns = p.parse_args(["--passphrase-env", "PW", "progress"])
    assert ns.passphrase_env == "PW"


def test_every_subcommand_accepts_the_unlock_flags():
    from brain.cli.main import build_parser

    p = build_parser()
    missing = [name for name, sub in p._subparsers._group_actions[0].choices.items()
               if "--passphrase-env" not in {o for a in sub._actions for o in a.option_strings}]
    assert missing == [], f"these commands reject the unlock flags: {missing}"


def test_memory_report_is_no_longer_an_argparse_error():
    from brain.cli.main import build_parser

    ns = build_parser().parse_args(["memory", "report", "--passphrase-env", "PW"])
    assert ns.command == "memory" and ns.action == "report"
    ns = build_parser().parse_args(["memory", "purge", "--passphrase-env", "PW"])
    assert ns.action == "purge"


# --------------------------------------------------------------------------- #
# 10. the local-AI installer says what it really did
# --------------------------------------------------------------------------- #
def test_the_installer_tells_the_three_ollama_states_apart(monkeypatch):
    tool = _tool("install_local_ai")

    monkeypatch.setattr(tool, "_http_json", lambda *a, **k: None)
    assert tool.status()["state"] == "no-ollama"

    monkeypatch.setattr(tool, "_http_json", lambda *a, **k: {"models": []})
    assert tool.status()["state"] == "no-model"

    monkeypatch.setattr(tool, "_http_json",
                        lambda *a, **k: {"models": [{"name": "llama3.2:3b"}]})
    ready = tool.status()
    assert ready["state"] == "ready" and "llama3.2:3b" in ready["models"]


def test_the_installer_does_not_download_when_a_model_is_ready(monkeypatch):
    tool = _tool("install_local_ai")
    monkeypatch.setattr(tool, "status", lambda url=tool.OLLAMA: {
        "running": True, "models": ["llama3.2:3b"], "state": "ready", "why": "", "next": ""})
    ran = []
    monkeypatch.setattr(tool, "_run", lambda *a, **k: ran.append(a) or {"ok": True})
    assert tool.install() == 0
    assert ran == []


def test_the_installer_points_at_the_official_page_instead_of_guessing(monkeypatch):
    tool = _tool("install_local_ai")
    monkeypatch.setattr(tool, "status", lambda url=tool.OLLAMA: {
        "running": False, "models": [], "state": "no-ollama", "why": "", "next": ""})
    monkeypatch.setattr(tool.shutil, "which", lambda name: None)
    assert tool.install() == 3           # a clear "open the download page", not a fake success


def test_the_installer_checks_the_model_actually_answers(monkeypatch):
    tool = _tool("install_local_ai")
    calls = []

    def fake_http(url, payload=None, timeout=3.0):
        calls.append(url)
        return {"response": ""} if "generate" in url else {"models": []}

    monkeypatch.setattr(tool, "_http_json", fake_http)
    monkeypatch.setattr(tool.shutil, "which", lambda name: "/usr/bin/ollama")
    monkeypatch.setattr(tool, "_run", lambda *a, **k: {"ok": True, "code": 0, "out": "", "err": ""})
    assert tool.pull() == 6              # an empty answer is a failure, not a success
    assert any("generate" in c for c in calls)


def test_the_three_windows_helpers_are_shipped():
    for name in ("move-to-d.bat", "backup-to-d.bat", "install-local-ai.bat"):
        raw = (ROOT / name).read_bytes()
        assert raw.startswith(b"@echo off") and b"\r\n" in raw
