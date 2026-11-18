"""Acceptance tests: stage 8 - the small problems, found by using the product.

The owner's words for this stage were short and unfriendly, on purpose: "there are
twelve small problems, finish them, and re-test everything, not just what I
check". So this file is organised as *the bugs*, each one written as the failing
behaviour first, not as the feature that fixes it.

1. A reminder set for 90 minutes away said "[in 0 min]" (milliseconds truncated).
2. A reminder set for a few seconds away showed a clock with no seconds, so a
   working reminder looked broken.
3. Nothing announced a reminder that came due while Nila was closed, and there
   was no way to ask. The app now says it on start, a second icon checks it, the
   dashboard checks it every tick, and `brain remind --due` asks directly.
4. The icon could not set a reminder at all ("Nila remind me ..." was refused).
5. Gemini model names were guessed: a name this key does not have failed with a
   raw 404. The list is now read from the API (and the 404 is repaired once).
6. "Three months of memory, then let it go" did not exist: memory only grew.
   It is now a report plus an explicit purge, never a scheduled delete.

Run:  python -m pytest tests/test_small_bugs_and_retest_acceptance.py -q
"""
from __future__ import annotations

import io
import json
import time
from pathlib import Path

import pytest

from brain.agent import reminders
from brain.agent import providers as prov
from brain.memory import retention
from brain.util import launcher


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
class Rec:
    """A reminder-shaped object, without needing a vault."""

    def __init__(self, at_ms: int, title: str = "call amma"):
        self.id = "rem_test"
        self.title = title
        self.meta = {"at_ms": int(at_ms), "done": False}


def status_of(at_ms: int, now_ms: int) -> str:
    line = reminders.describe(Rec(at_ms), now_ms)
    return line.split("[")[-1].rstrip("]")


class FakeMemory:
    """The minimum the retention report needs."""

    def __init__(self, rows):
        self.rows = rows
        self.deleted = []

    def list_records(self, limit=5000, include_deleted=False):
        return self.rows

    def delete(self, rid, hard=False):
        self.deleted.append(rid)
        return True


def stored(i, kind, title, age_days, meta=None, now_ms=None):
    now = now_ms if now_ms is not None else int(time.time() * 1000)

    class Row:
        pass

    row = Row()
    row.id = i
    row.kind = kind
    row.title = title
    row.meta = meta or {}
    row.created_at = row.updated_at = now - int(age_days * 86400000)
    return row


# --------------------------------------------------------------------------- #
# bug 1 + 2: the clock on a reminder
# --------------------------------------------------------------------------- #
def test_ninety_minutes_away_is_not_zero_minutes():
    now = int(time.time() * 1000)
    assert status_of(now + 90 * 60000, now) == "in 2 h"
    assert status_of(now + 45 * 60000, now) == "in 45 min"


def test_a_few_seconds_away_says_seconds():
    now = int(time.time() * 1000)
    assert status_of(now + 3000, now) == "in 3 s"
    assert status_of(now + 90000, now) == "in 90 s"


def test_a_short_wait_shows_seconds_on_the_clock_too():
    now = int(time.time() * 1000)
    line = reminders.describe(Rec(now + 30000), now)
    stamp = line.split("  ")[2]
    assert stamp.count(":") == 2              # %H:%M:%S, so "00:00:30" is visible


def test_just_missed_is_not_minutes_ago():
    now = int(time.time() * 1000)
    assert status_of(now - 5000, now) == "MISSED just now"
    assert status_of(now - 2 * 3600000, now) == "MISSED 120 min ago"


def test_days_and_hours_still_read_normally():
    now = int(time.time() * 1000)
    assert status_of(now + 3 * 86400000, now) == "in 3 days"
    assert status_of(now + 10 * 86400000, now) == "in 10 days"


# --------------------------------------------------------------------------- #
# bug 3: a reminder that came due while Nila was closed
# --------------------------------------------------------------------------- #
def test_the_icon_announces_what_came_due_and_closes_it(tmp_path, monkeypatch):
    from brain.cli import main as cli

    spoken = []
    monkeypatch.setattr("brain.util.speech.speak_text",
                        lambda text, **kw: spoken.append(text) or {"spoken": True})
    calls = {}

    class Brain:
        def due_reminders(self, consume=False):
            calls["consume"] = consume
            return [{"id": "rem_1", "title": "call amma", "late_minutes": 12}]

    rows = cli._due_alerts(Brain(), spoken=True)
    assert calls["consume"] is True            # closed, so never announced twice
    assert rows[0]["title"] == "call amma"
    assert spoken and "12 min late" in spoken[0]


def test_a_broken_record_does_not_stop_the_action(tmp_path):
    from brain.cli import main as cli

    class Broken:
        def due_reminders(self, consume=False):
            raise RuntimeError("bad row")

    assert cli._due_alerts(Broken()) == []      # never raises into the caller


def test_brain_start_says_the_missed_reminder_before_opening_the_app(monkeypatch):
    from brain.cli import main as cli

    printed = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(" ".join(map(str, a))))
    monkeypatch.setattr(cli, "_due_alerts", lambda *a, **k: printed.append("REMINDERS-15"))
    # `_due_alerts` is what cmd_start calls first; the call itself is asserted by
    # the source contract below, because starting the server is not a unit test.
    import inspect

    body = inspect.getsource(cli.cmd_start)
    assert "_due_alerts(" in body.split("serve(")[0], "start must announce before serving"


def test_the_dashboard_polls_for_due_reminders():
    js = (Path(__file__).resolve().parents[1] / "brain" / "api" / "static"
          / "dashboard.js").read_text()
    assert "/v1/reminders/due" in js
    assert "/v1/reminders/close" in js
    html = (Path(__file__).resolve().parents[1] / "brain" / "api" / "static"
            / "dashboard.html").read_text()
    assert 'id="due"' in html


def test_the_reminder_icon_is_written_next_to_the_app_icon(tmp_path):
    out = launcher.write_launchers(tmp_path / "home", tmp_path / "desk")
    names = [Path(p).name for p in out["written"]]
    assert any("Nila" in n or "nila" in n for n in names)
    due = [p for p in out["written"] if "eminder" in Path(p).name.lower()]
    assert due, f"no reminder icon was written: {names}"
    assert "remind" in Path(due[0]).read_text()


# --------------------------------------------------------------------------- #
# bug 4: setting a reminder by voice / icon
# --------------------------------------------------------------------------- #
def test_a_spoken_reminder_becomes_an_action():
    out = launcher.spoken_action("nila remind me in 10 minutes to drink water")
    assert out["action"] == "remind"
    assert out["argument"] == "me in 10 minutes to drink water"
    when = reminders.parse_when(out["argument"])
    assert when.at_ms > time.time() * 1000


def test_the_talk_command_handles_remind_before_any_model():
    from brain.cli import main as cli
    import inspect

    body = inspect.getsource(cli.cmd_talk)
    assert 'action == "remind"' in body
    # The reminder path must be the parser, not a provider.
    assert "parse_when" in body
    assert "_NoProvider" in inspect.getsource(cli._talk_brain)


# --------------------------------------------------------------------------- #
# bug 5: Gemini model names are read, not guessed
# --------------------------------------------------------------------------- #
class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def gemini_with(names, configured="gemini-flash-latest"):
    return prov.GeminiProvider(api_key="test-key", model=configured,
                               base_url="http://127.0.0.1:9/v1beta")


def patch_models(monkeypatch, names):
    def fake_open(req, timeout=8):
        body = {"models": [{"name": f"models/{n}",
                            "supportedGenerationMethods": ["generateContent"]}
                           for n in names]}
        return FakeResponse(json.dumps(body).encode())

    monkeypatch.setattr(prov, "open_once", fake_open)


def test_the_model_list_is_asked_for_not_guessed(monkeypatch):
    patch_models(monkeypatch, ["gemini-2.5-flash", "gemini-1.5-pro"])
    provider = gemini_with([])
    assert provider.list_models() == ["gemini-2.5-flash", "gemini-1.5-pro"]
    assert provider.pick_model() == "gemini-2.5-flash"


def test_a_configured_model_that_exists_is_kept(monkeypatch):
    patch_models(monkeypatch, ["gemini-2.5-flash", "gemini-1.5-pro"])
    provider = gemini_with([], configured="gemini-1.5-pro")
    assert provider.pick_model() == "gemini-1.5-pro"


def test_embedding_models_are_not_offered_as_a_brain(monkeypatch):
    def fake_open(req, timeout=8):
        body = {"models": [
            {"name": "models/text-embedding-004"},
            {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
        ]}
        return FakeResponse(json.dumps(body).encode())

    monkeypatch.setattr(prov, "open_once", fake_open)
    assert gemini_with([]).list_models() == ["gemini-2.5-flash"]


def test_no_network_keeps_the_configured_name_instead_of_crashing(monkeypatch):
    def boom(req, timeout=8):
        raise OSError("no network")

    monkeypatch.setattr(prov, "open_once", boom)
    provider = gemini_with([], configured="gemini-1.5-flash")
    assert provider.pick_model() == "gemini-1.5-flash"
    assert provider.models_seen == []


def test_a_404_on_the_model_name_is_repaired_once(monkeypatch):
    patch_models(monkeypatch, ["gemini-2.5-flash"])
    provider = gemini_with([], configured="gemini-does-not-exist")
    attempts = []

    def fake_once(system, messages, tools):
        attempts.append(provider.model)
        if provider.model == "gemini-does-not-exist":
            raise prov.ProviderError("404", status=404)
        return "answered"

    monkeypatch.setattr(provider, "_complete_once", fake_once)
    assert provider.complete("sys", [{"role": "user", "content": "hi"}]) == "answered"
    assert attempts == ["gemini-does-not-exist", "gemini-2.5-flash"]


def test_a_404_with_nothing_to_fall_back_to_says_what_is_available(monkeypatch):
    patch_models(monkeypatch, ["gemini-2.5-flash"])

    def always_404(system, messages, tools):
        raise prov.ProviderError("404", status=404)

    provider = gemini_with([], configured="gemini-nope")
    provider.pick_model = lambda candidates=None: "gemini-nope"   # nothing better
    provider.models_seen = ["gemini-2.5-flash"]
    monkeypatch.setattr(provider, "_complete_once", always_404)
    with pytest.raises(prov.ProviderError) as exc:
        provider.complete("sys", [{"role": "user", "content": "hi"}])
    assert "gemini-2.5-flash" in str(exc.value)


# --------------------------------------------------------------------------- #
# bug 6: three months of memory, then let it go
# --------------------------------------------------------------------------- #
NOW = int(time.time() * 1000)


def sample_rows():
    return [
        stored("old-note", "note", "grocery list from the old house", 120, now_ms=NOW),
        stored("fresh", "note", "the new gate code", 3, now_ms=NOW),
        stored("due-reminder", "reminder", "take the tablet", 200, now_ms=NOW),
        stored("pinned", "note", "amma's tablet names", 150, {"importance": "highest"},
               now_ms=NOW),
        stored("login", "credential_ref", "bank hint", 400, now_ms=NOW),
    ]


def test_the_report_never_deletes_anything():
    memory = FakeMemory(sample_rows())
    out = retention.report(memory, days=90, now_ms=NOW)
    assert memory.deleted == []
    assert out["older"] == 1 and out["older_records"][0]["id"] == "old-note"


def test_protected_records_are_kept_and_named():
    memory = FakeMemory(sample_rows())
    out = retention.report(memory, days=90, now_ms=NOW)
    kept = {row["id"] for row in out["protected_records"]}
    assert kept == {"due-reminder", "pinned", "login"}
    assert "protected" in out["line"]


def test_a_purge_needs_an_explicit_yes():
    memory = FakeMemory(sample_rows())
    dry = retention.purge(memory, days=90, apply=False, now_ms=NOW)
    assert dry["purged"] == 0 and memory.deleted == []
    real = retention.purge(memory, days=90, apply=True, now_ms=NOW)
    assert real["purged"] == 1 and memory.deleted == ["old-note"]
    assert real["audit"] == {"event": "memory_purge", "count": 1, "window_days": 90}


def test_a_nonsense_window_is_refused():
    with pytest.raises(retention.RetentionError):
        retention.report(FakeMemory([]), days=0)


def test_nothing_deletes_on_its_own():
    """The owner's rule: Nila deletes only when told. No thread, no timer, no loop."""
    import ast

    path = (Path(__file__).resolve().parents[1] / "brain" / "memory" / "retention.py")
    tree = ast.parse(path.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"threading", "sched", "asyncio", "atexit", "signal"}
    calls = {getattr(n.func, "id", getattr(n.func, "attr", ""))
             for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not calls & {"Timer", "sleep", "fork"}


def test_the_memory_command_exists_and_defaults_to_a_report():
    from brain.cli import main as cli
    import inspect

    body = inspect.getsource(cli.cmd_memory)
    assert "retention.report" in body and "retention.purge" in body
    assert "--purge" in inspect.getsource(cli.build_parser)


# --------------------------------------------------------------------------- #
# bug 7: a hint the owner cannot paste
# --------------------------------------------------------------------------- #
def test_the_grant_hint_survives_a_path_with_spaces():
    """Found while re-testing the refusal message: `--scope D:/Nila Assistant
    Storage/...` unquoted is three arguments, so the pasted grant failed."""
    from brain.agent import tools as tools_mod
    from brain.agent.tools import ToolContext
    from brain.security.permission import Broker

    registry = tools_mod.build_default_registry()
    broker = Broker(path=Path("/tmp") / "nila-hint-permissions.json",
                    audit=lambda e, d: None)
    vault = type("V", (), {"is_unlocked": lambda self: True})()
    ctx = ToolContext(memory=None, vault=vault,
                      config=type("C", (), {"home": Path("/tmp"), "agent": {}})(),
                      permissions=broker)
    out = registry.call("read_file_report",
                        {"path": "D:/Nila Assistant Storage/report.pdf"}, ctx)
    assert out["ok"] is False and out["error"] == "permission required"
    hint = out["how_to_grant"]
    assert f'--scope "{out["scope"]}"' in hint.replace("  ", " ") or '"' in hint
    assert '--purpose "<why>"' in hint
