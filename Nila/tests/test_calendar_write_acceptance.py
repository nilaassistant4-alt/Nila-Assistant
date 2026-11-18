"""Acceptance tests: stage 12, gap 3 - the calendar can be written, honestly.

The row said "Gmail / Drive / Calendar write (OAuth)" and it was marked *not
built*. Half of it can be built without a consent screen, and that half is worth
having: a local `.ics` that the owner imports once into his phone or Google
Calendar, and that stays correct when he edits an event.

What makes an imported file *replace* an event instead of adding a second copy is
invisible in a screenshot and is the whole point, so it is what these tests check:

* the `UID` survives an edit and `SEQUENCE` goes up;
* moving an event keeps its length (an event that ends before it starts was the
  first bug written here);
* the file is valid RFC 5545 (CRLF, folded at 75, escaped commas) and reads back
  through the *same* parser the read path uses;
* a recurring event keeps its rule;
* writing straight into Google is still refused, in words, and no request is
  ever made.

Run:  python -m pytest tests/test_calendar_write_acceptance.py -q
"""
from __future__ import annotations

import datetime as dt

import pytest

from brain.util import ics


def test_an_event_is_written_and_read_back(tmp_path):
    path = tmp_path / "nila.ics"
    out = ics.add_event(path, "Dentist", dt.datetime(2026, 11, 6, 10, 30),
                        end=dt.datetime(2026, 11, 6, 11, 30), location="Anna Nagar")
    assert out["ok"] and out["uid"].endswith("@nila.local")
    events = ics.read_events(path)
    assert len(events) == 1
    assert events[0].summary == "Dentist"
    assert events[0].start == dt.datetime(2026, 11, 6, 10, 30)
    assert events[0].end == dt.datetime(2026, 11, 6, 11, 30)
    assert events[0].location == "Anna Nagar"


def test_the_file_is_a_real_calendar_and_not_a_text_file_with_ics_in_the_name(tmp_path):
    path = tmp_path / "nila.ics"
    ics.add_event(path, "Dentist", dt.datetime(2026, 11, 6, 10, 30))
    raw = path.read_bytes()
    assert raw.startswith(b"BEGIN:VCALENDAR\r\n")      # CRLF, as the RFC says
    assert raw.rstrip().endswith(b"END:VCALENDAR")
    assert b"\n" not in raw.replace(b"\r\n", b"")      # no bare LF anywhere
    assert b"VERSION:2.0" in raw and b"BEGIN:VEVENT" in raw
    # and every line is folded at 75 characters, which is what stops a long
    # summary from breaking another app's reader
    assert all(len(line) <= 75 for line in raw.decode().split("\r\n"))


def test_a_comma_in_the_title_does_not_break_the_file(tmp_path):
    path = tmp_path / "nila.ics"
    ics.add_event(path, "Buy milk, eggs; and bread", dt.datetime(2026, 11, 6, 18, 0))
    events = ics.read_events(path)
    assert events[0].summary == "Buy milk, eggs; and bread"


def test_an_edit_keeps_the_uid_and_raises_the_sequence(tmp_path):
    """This is the difference between replacing an event and duplicating it."""
    path = tmp_path / "nila.ics"
    first = ics.add_event(path, "Dentist", dt.datetime(2026, 11, 6, 10, 30))
    second = ics.update_event(path, first["uid"], summary="Dentist (moved)",
                              start=dt.datetime(2026, 11, 6, 12, 0))
    assert second["sequence"] == 1 and second["uid"] == first["uid"]
    raw = path.read_text()
    assert raw.count("BEGIN:VEVENT") == 1           # still one event, not two
    assert "UID:" + first["uid"] in raw and "SEQUENCE:1" in raw
    assert "Dentist (moved)" in raw


def test_moving_an_event_keeps_its_length(tmp_path):
    """Moving 10:30-11:30 to 12:00 must end at 13:00, not at 11:30."""
    path = tmp_path / "nila.ics"
    first = ics.add_event(path, "Dentist", dt.datetime(2026, 11, 6, 10, 30),
                          end=dt.datetime(2026, 11, 6, 11, 30))
    ics.update_event(path, first["uid"], start=dt.datetime(2026, 11, 6, 12, 0))
    event = ics.read_events(path)[0]
    assert event.start == dt.datetime(2026, 11, 6, 12, 0)
    assert event.end == dt.datetime(2026, 11, 6, 13, 0)


def test_the_other_events_survive_an_edit_and_a_removal(tmp_path):
    path = tmp_path / "nila.ics"
    a = ics.add_event(path, "Dentist", dt.datetime(2026, 11, 6, 10, 30))
    b = ics.add_event(path, "Team call", dt.datetime(2026, 11, 7, 15, 0))
    ics.update_event(path, a["uid"], summary="Dentist (moved)")
    assert {e.summary for e in ics.read_events(path)} == {"Dentist (moved)", "Team call"}
    ics.remove_event(path, b["uid"])
    assert [e.summary for e in ics.read_events(path)] == ["Dentist (moved)"]
    # a removal leaves a cancelled entry, so an import of this file also removes
    # it on the phone - silence would leave the old copy there for ever
    assert "STATUS:CANCELLED" in path.read_text()


def test_a_recurring_event_keeps_its_rule(tmp_path):
    path = tmp_path / "nila.ics"
    ics.add_event(path, "Gym", dt.datetime(2026, 11, 9, 6, 0), rrule="FREQ=WEEKLY;BYDAY=MO")
    raw = path.read_text()
    assert "RRULE:FREQ=WEEKLY;BYDAY=MO" in raw
    assert ics.read_events(path)[0].rrule == "FREQ=WEEKLY;BYDAY=MO"


def test_an_all_day_event_has_no_invented_midnight_time(tmp_path):
    path = tmp_path / "nila.ics"
    ics.add_event(path, "Holiday", dt.datetime(2026, 11, 14), all_day=True)
    raw = path.read_text()
    assert "DTSTART;VALUE=DATE:20261114" in raw
    assert "DTEND;VALUE=DATE:20261115" in raw          # a day long, not zero
    event = ics.read_events(path)[0]
    assert event.all_day is True


def test_a_bad_title_or_unknown_uid_is_refused_in_words(tmp_path):
    with pytest.raises(ics.IcsWriteError, match="needs a title"):
        ics.add_event(tmp_path / "x.ics", "  ", dt.datetime(2026, 11, 6, 10, 0))
    with pytest.raises(ics.IcsWriteError, match="no event with UID"):
        ics.update_event(tmp_path / "x.ics", "nope", summary="x")
    ics.add_event(tmp_path / "x.ics", "Dentist", dt.datetime(2026, 11, 6, 10, 0))
    with pytest.raises(ics.IcsWriteError, match="no event with UID"):
        ics.remove_event(tmp_path / "x.ics", "nope")


def test_writing_into_google_is_still_refused_and_says_why(tmp_path):
    out = ics.add_event(tmp_path / "nila.ics", "Dentist", dt.datetime(2026, 11, 6, 10, 0))
    assert "needs OAuth" in out["note"]
    # nothing in the write path can reach the network: no fetcher exists here
    assert not hasattr(ics, "push")


def test_the_read_path_can_still_read_what_the_write_path_wrote(tmp_path):
    """A written file must work in `brain calendar` - the same parser, not a second one."""
    path = tmp_path / "nila.ics"
    ics.add_event(path, "Dentist", dt.datetime(2026, 11, 6, 10, 30),
                  end=dt.datetime(2026, 11, 6, 11, 30))
    data = ics.between(path.read_text(), dt.date(2026, 11, 1), dt.date(2026, 11, 10))
    assert data["count"] == 1 and "Dentist" in data["lines"][0]
    assert "Fri 06 Nov" in data["lines"][0]


# --------------------------------------------------------------------------- #
# the command the owner actually types
# --------------------------------------------------------------------------- #
def test_brain_calendar_adds_lists_and_removes(tmp_path, capsys):
    from brain.cli.main import UI, build_parser, cmd_calendar

    target = tmp_path / "mine.ics"
    parser = build_parser()

    def run(*argv):
        args = parser.parse_args(["calendar", *argv, "--file", str(target)])
        return cmd_calendar(args, UI())

    assert run("--add", "Dentist", "--start", "2026-11-06 10:30",
               "--end", "2026-11-06 11:30", "--where", "Anna Nagar") == 0
    printed = capsys.readouterr().out
    assert "added: 2026-11-06T10:30  Dentist" in printed
    assert "Anna Nagar" in printed

    assert run("--add", "Holiday", "--start", "2026-11-14") == 0
    capsys.readouterr()

    assert run("--list") == 0
    printed = capsys.readouterr().out
    assert "2 event(s)" in printed and "Holiday" in printed and "uid " in printed

    uid = ics.read_events(target)[0].uid
    assert run("--update", uid, "--start", "2026-11-06 12:00") == 0
    assert "SEQUENCE 1" in capsys.readouterr().out

    assert run("--remove", uid) == 0
    assert "removed" in capsys.readouterr().out
    assert [e.summary for e in ics.read_events(target)] == ["Holiday"]


def test_a_time_it_cannot_read_is_refused_before_anything_is_written(tmp_path, capsys):
    from brain.cli.main import UI, build_parser, cmd_calendar

    target = tmp_path / "mine.ics"
    args = build_parser().parse_args(["calendar", "--add", "X", "--start",
                                      "whenever you like", "--file", str(target)])
    assert cmd_calendar(args, UI()) == 2
    assert not target.exists()
    # the refusal is the reminder parser's own sentence, reused on purpose
    assert "could not find a time" in capsys.readouterr().err


def test_the_everyday_words_work_because_the_reminder_parser_is_reused(tmp_path, capsys):
    from brain.cli.main import UI, build_parser, cmd_calendar

    target = tmp_path / "mine.ics"
    args = build_parser().parse_args(["calendar", "--add", "Team call",
                                      "--start", "tomorrow at 9",
                                      "--file", str(target)])
    assert cmd_calendar(args, UI()) == 0
    event = ics.read_events(target)[0]
    assert (event.start.hour, event.start.minute) == (9, 0)
    assert event.start.date() == (dt.datetime.now() + dt.timedelta(days=1)).date()
