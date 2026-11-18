"""Acceptance tests: stage 11 polish - the habit streak and one-command start.

The owner's ask for this part was "make it a bit better developed", not "add a
game". Two small things were chosen because they are what makes a personal tool
survive contact with a real week:

* **A streak that cannot be faked.** Days are counted from the audit journal's
  real unlocks. A dashboard left open in a tab all week is not seven days of use,
  and the code must refuse to count it.
* **`brain start` that starts.** Unlocking used to end with a wall of JSON and the
  owner then had to run a second command to see the app. One command, one login,
  browser open.

Run:  python -m pytest tests/test_habit_and_start_acceptance.py -q
"""
from __future__ import annotations

import datetime as dt
import inspect

import pytest

from brain.agent import journal
from brain.cli import main as cli


def ms(day: dt.date, hour: int = 9) -> int:
    return int(dt.datetime.combine(day, dt.time(hour)).timestamp() * 1000)


class Journal:
    def __init__(self, rows):
        self.rows = rows

    def read_audit(self, limit=50):
        return self.rows[:limit]


TODAY = dt.date(2026, 11, 4)


def days_back(*offsets, event="session_unlock"):
    return [{"event": event, "ts": ms(TODAY - dt.timedelta(days=n))} for n in offsets]


# --------------------------------------------------------------------------- #
# 1. the streak
# --------------------------------------------------------------------------- #
def test_a_fresh_vault_says_day_one_not_a_cheerful_streak():
    out = journal.activity(None, today=TODAY)
    assert out["days_in_app"] == 0 and out["current_streak"] == 0
    assert "Day one" in journal.line(out)


def test_a_real_run_of_days_counts_backwards_from_today():
    out = journal.activity(Journal(days_back(0, 1, 2)), today=TODAY)
    assert (out["current_streak"], out["best_streak"], out["today"]) == (3, 3, True)


def test_a_gap_ends_the_streak_but_today_is_still_in_progress():
    # yesterday, the day before, the day before: still a live 3-day streak
    live = journal.activity(Journal(days_back(1, 2, 3)), today=TODAY)
    assert live["current_streak"] == 3 and live["today"] is False
    assert "today not counted yet" in journal.line(live)
    # a hole two days ago breaks it: only yesterday is current
    broken = journal.activity(Journal(days_back(1, 3, 4)), today=TODAY)
    assert broken["current_streak"] == 1
    assert broken["best_streak"] >= 1


def test_polling_the_api_is_not_a_day_of_use():
    rows = days_back(0) + [{"event": "api_request", "ts": ms(TODAY, 12)},
                           {"event": "session_touch", "ts": ms(TODAY, 13)}]
    out = journal.activity(Journal(rows), today=TODAY)
    assert out["days_in_app"] == 1


def test_the_best_streak_survives_a_bad_week():
    rows = days_back(*range(0, 5)) + days_back(*range(10, 17))
    out = journal.activity(Journal(rows), today=TODAY)
    assert out["current_streak"] == 5 and out["best_streak"] == 7


def test_the_week_strip_is_seven_days_and_ends_today():
    out = journal.activity(Journal(days_back(0, 2)), today=TODAY)
    assert len(out["week"]) == 7
    assert out["week"][-1]["date"] == TODAY.isoformat() and out["week"][-1]["used"]
    assert out["week"][-2]["used"] is False
    assert out["week"][-3]["used"] is True


def test_a_broken_journal_never_raises():
    class Broken:
        def read_audit(self, limit=50):
            raise RuntimeError("vault locked")

    out = journal.activity(Broken(), today=TODAY)
    assert out["days_in_app"] == 0 and out["ok"] is True


@pytest.mark.parametrize("stamp", [None, "not a date", {}, 10 ** 20])
def test_a_nonsense_timestamp_is_skipped_not_counted(stamp):
    out = journal.activity(Journal([{"event": "session_unlock", "ts": stamp}]), today=TODAY)
    assert out["days_in_app"] == 0


def test_iso_timestamps_are_understood_too():
    rows = [{"event": "session_unlock", "ts": TODAY.isoformat() + "T10:00:00"}]
    out = journal.activity(Journal(rows), today=TODAY)
    assert out["days_in_app"] == 1 and out["today"] is True


# --------------------------------------------------------------------------- #
# 2. the command and the route
# --------------------------------------------------------------------------- #
def test_brain_habit_exists_and_reads_history_only():
    src = inspect.getsource(cli.cmd_habit)
    assert "journal.activity" in src
    # nothing in the command writes
    assert ".add(" not in src and "meta_set" not in src and "audit(" not in src


def test_the_api_route_serves_it_without_the_journal(monkeypatch):
    from brain.api import gateway

    src = inspect.getsource(gateway.Gateway.route)
    assert '"/v1/habit"' in src
    assert "journal.activity" in src


# --------------------------------------------------------------------------- #
# 3. one command to start
# --------------------------------------------------------------------------- #
def test_brain_dashboard_opens_the_app_after_one_login():
    src = inspect.getsource(cli.cmd_dashboard)
    assert "open_vault(args)" in src          # the same lock ceremony
    assert "serve(" in src
    assert "open_browser=not args.no_open" in src
    assert "quiet_banner=True" in src


def test_the_start_banner_reaches_a_pipe_at_once():
    """Found by starting the app from a .bat: every print in `serve` was buffered
    when stdout was a pipe, so the window stayed blank and the token the owner
    asked for with --show-token was not there. Anything a human reads on start
    is flushed."""
    import inspect

    from brain.api import gateway as gw

    lines = inspect.getsource(gw.serve).splitlines()
    for i, line in enumerate(lines):
        if "print(" not in line:
            continue
        if not any(word in line for word in ("listening", "open at", "dashboard:",
                                             "bearer token")):
            continue
        # a message may wrap onto the next line before its flush
        joined = line + (lines[i + 1] if i + 1 < len(lines) else "")
        assert "flush=True" in joined, line.strip()


def test_the_start_script_uses_it():
    from pathlib import Path

    bat = (Path(__file__).resolve().parents[1] / "start-nila.bat").read_text()
    assert "brain.py dashboard" in bat
    assert "brain.py serve" not in bat
