"""Acceptance tests: stage 2 - reminders and the strengthened calculator.

The owner's words: "remind me when I ask, by time and date", "a calculator that
does percentages, GST and units", and "all of it works with no network".

Run:  python -m pytest tests/test_reminders_acceptance.py -q
"""
from __future__ import annotations

import datetime as _dt
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from brain.agent.reminders import (
    ReminderBook, ReminderError, describe, due, next_after, parse_when, split_text,
)
from brain.util import money as money

NOW = _dt.datetime(2026, 11, 4, 15, 10)


# --------------------------------------------------------------------------- #
# reading a time
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,expect", [
    ("remind me in 10 minutes to drink water", _dt.datetime(2026, 11, 4, 15, 20)),
    ("remind me in 2 hours to check the oven", _dt.datetime(2026, 11, 4, 17, 10)),
    ("remind me at 4:30 to call the shop", _dt.datetime(2026, 11, 4, 16, 30)),
    ("remind me tomorrow at 9 to take the tablet", _dt.datetime(2026, 11, 5, 9, 0)),
    ("remind me on 12/12 at 18:00 to pay the bill", _dt.datetime(2026, 12, 12, 18, 0)),
    ("remind me at 9 am to call", _dt.datetime(2026, 11, 5, 9, 0)),
])
def test_times_are_read_exactly(text, expect):
    when = parse_when(text, NOW)
    assert _dt.datetime.fromtimestamp(when.at_ms / 1000) == expect


def test_a_bare_hour_in_the_evening_is_pm_not_am():
    """'at 4' said at 15:10 means 16:30-ish today, never 04:00 tomorrow."""
    when = parse_when("remind me at 4 to call the shop", NOW)
    assert _dt.datetime.fromtimestamp(when.at_ms / 1000).hour == 16


def test_daily_and_weekly_repeats_are_recognised():
    daily = parse_when("remind me every day at 22:00 to take the tablet", NOW)
    assert daily.repeat == "daily"
    weekly = parse_when("remind me every monday at 8 to open the shop", NOW)
    assert weekly.repeat == "weekly" and weekly.weekday == 0
    assert _dt.datetime.fromtimestamp(weekly.at_ms / 1000).weekday() == 0


def test_tamil_times_are_read_too():
    when = parse_when("10 நிமிடத்தில் தண்ணி குடிக்க நினைவூட்டு", NOW)
    assert _dt.datetime.fromtimestamp(when.at_ms / 1000) == _dt.datetime(2026, 11, 4, 15, 20)
    when = parse_when("நாளைக்கு காலை 9 மணிக்கு மருந்து சாப்பிட நினைவூட்டு", NOW)
    assert _dt.datetime.fromtimestamp(when.at_ms / 1000) == _dt.datetime(2026, 11, 5, 9, 0)
    daily = parse_when("தினமும் இரவு 22 மணிக்கு மருந்து நினைவூட்டு", NOW)
    assert daily.repeat == "daily"


@pytest.mark.parametrize("bad", [
    "remind me about the meeting",        # no time at all
    "remind me tomorrow",                 # a day but no hour
    "remind me at 25:99 to sleep",        # not a clock time
])
def test_a_reminder_without_a_clear_time_is_refused_not_guessed(bad):
    with pytest.raises(ReminderError):
        parse_when(bad, NOW)


def test_the_owner_s_own_words_survive():
    """The sentence is copied, not rebuilt: no lost letters, no paraphrase."""
    for text, expect in [
        ("remind me in 10 minutes to drink water", "drink water"),
        ("remind me in 45 minutes to stretch", "stretch"),
        ("remind me tomorrow at 9 to take the tablet", "take the tablet"),
        ("remind me every day at 22:00 to take the tablet", "take the tablet"),
    ]:
        assert split_text(text, parse_when(text, NOW)) == expect


def test_next_firing_rolls_forward_from_the_clock():
    at = int(_dt.datetime(2026, 11, 4, 22, 0).timestamp() * 1000)
    after = int(_dt.datetime(2026, 11, 6, 1, 0).timestamp() * 1000)
    nxt = next_after(at, "daily", after_ms=after)
    assert _dt.datetime.fromtimestamp(nxt / 1000) == _dt.datetime(2026, 11, 6, 22, 0)


# --------------------------------------------------------------------------- #
# the reminder book
# --------------------------------------------------------------------------- #
def test_a_reminder_is_stored_encrypted_and_named_in_its_own_words(memory):
    book = ReminderBook(memory)
    rec = book.add("remind me in 10 minutes to drink water", parse_when(
        "remind me in 10 minutes to drink water"))
    assert rec.kind == "reminder" and rec.title == "drink water"
    again = ReminderBook(memory).all()[0]           # survives a fresh read
    assert again.id == rec.id and again.title == "drink water"
    assert "water" in again.body


def test_done_cancel_and_snooze(memory):
    book = ReminderBook(memory)
    a = book.add("in 10 minutes to call the shop", parse_when("in 10 minutes"))
    b = book.add("in 20 minutes to close the gate", parse_when("in 20 minutes"))
    book.close(a, done=True)
    book.cancel(b)
    assert book.open() == []
    c = book.add("in 5 minutes to stand up", parse_when("in 5 minutes"))
    before = c.meta["at_ms"]
    book.snooze(c, 10)
    assert book.get(c.id).meta["at_ms"] > before


def test_a_number_refers_to_the_position_in_the_list(memory):
    book = ReminderBook(memory)
    first = book.add("in 5 minutes to stretch", parse_when("in 5 minutes"))
    book.add("in 30 minutes to eat", parse_when("in 30 minutes"))
    assert book.by_number("1").id == first.id
    assert book.by_number("9") is None


def test_a_repeating_reminder_rolls_forward_instead_of_dying(memory):
    book = ReminderBook(memory)
    when = parse_when("remind me every day at 22:00 to take the tablet", NOW)
    rec = book.add("remind me every day at 22:00 to take the tablet", when)
    meta = dict(rec.meta)
    meta["at_ms"] = 1_000                     # long past
    memory.update(rec.id, meta=meta)
    assert due(book.all(), now_ms=10_000)     # it comes due
    book.close(book.get(rec.id), done=True)
    after = book.get(rec.id)
    assert after.meta["done"] is False and after.meta["at_ms"] > 10_000


def test_a_missed_reminder_says_how_late_it_is(memory):
    book = ReminderBook(memory)
    rec = book.add("in 5 minutes to check the stove", parse_when("in 5 minutes"))
    meta = dict(rec.meta)
    now = meta["at_ms"] + 12 * 60000
    line = describe(book.get(rec.id), now)
    assert "MISSED" in line and "drink" not in line and "check the stove" in line


def test_the_list_never_shows_a_cancelled_reminder(memory):
    book = ReminderBook(memory)
    rec = book.add("in 5 minutes to go", parse_when("in 5 minutes"))
    book.cancel(rec)
    assert rec.id not in book.render()
    assert "No reminders yet" in ReminderBook(memory).render()


# --------------------------------------------------------------------------- #
# through the agent: offline, no model, no network
# --------------------------------------------------------------------------- #
def test_saying_remind_me_sets_one_with_no_model_at_all(brain):
    turn = brain.handle("remind me in 10 minutes to drink water")
    assert "drink water" in turn.answer
    assert turn.route.get("where") in ("offline", "local", None)
    assert len(brain.reminder_book().all()) == 1


def test_asking_for_the_list_shows_the_list(brain):
    brain.handle("remind me in 10 minutes to drink water")
    turn = brain.handle("reminders")
    assert "drink water" in turn.answer and "Reminders:" in turn.answer


def test_asking_remind_without_a_time_asks_back_and_stores_nothing(brain):
    turn = brain.handle("remind me about the meeting")
    assert "could not find a time" in turn.answer
    assert brain.reminder_book().all() == []


def test_a_due_reminder_is_reported_on_the_next_turn_and_only_once(brain):
    brain.handle("remind me in 10 minutes to check the stove")
    rec = brain.reminder_book().all()[0]
    meta = dict(rec.meta)
    meta["at_ms"] = rec.meta["at_ms"] - 11 * 60000        # due one minute ago
    brain.memory.update(rec.id, meta=meta)

    turn = brain.handle("hello")
    assert len(turn.alerts) == 1 and turn.alerts[0]["title"] == "check the stove"
    assert turn.alerts[0]["late_minutes"] == 1
    assert brain.handle("hello").alerts == []             # not repeated forever


def test_the_alert_is_printed_above_the_answer(brain):
    from brain.agent.repl import _fmt_turn

    brain.handle("remind me in 10 minutes to check the stove")
    rec = brain.reminder_book().all()[0]
    meta = dict(rec.meta)
    meta["at_ms"] = rec.meta["at_ms"] - 11 * 60000
    brain.memory.update(rec.id, meta=meta)
    text = _fmt_turn(brain.handle("hello"))
    assert text.index("REMINDER") < text.index("Hello")


def test_a_reminder_turn_never_touches_the_network(brain, monkeypatch):
    """With urllib disabled, both setting and listing still work."""
    import urllib.request as _u

    def boom(*a, **k):
        raise AssertionError("a reminder must never open a connection")

    monkeypatch.setattr(_u, "urlopen", boom)
    assert "drink water" in brain.handle("remind me in 10 minutes to drink water").answer
    assert "drink water" in brain.handle("reminders").answer


def test_the_reminder_is_written_to_the_audit_journal(brain, vault):
    brain.handle("remind me in 10 minutes to drink water")
    events = [e.get("event") for e in vault.read_audit(40)]
    assert "reminder_added" in events


def test_reminders_survive_a_new_brain_on_the_same_store(config, vault, memory):
    from brain.agent.agent import Brain

    Brain(config, vault, memory).handle("remind me in 10 minutes to drink water")
    assert "drink water" in Brain(config, vault, memory).reminder_book().render()


# --------------------------------------------------------------------------- #
# the calculator
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("question,expect", [
    ("18% of 1250", 225.0),
    ("1250-ல 18 சதவீதம்", 225.0),
    ("இருநூறு ரூபாய்க்கு 15 சதவீதம்", 30.0),
    ("GST on 2499", 2948.82),
    ("GST inside 2950", 2500.0),
    ("10% discount on 899", 809.10),
    ("simple interest on 50000 at 8% for 3 years", 62000.0),
    ("compound interest on 100000 at 7% for 5 years", 140255.17),
    ("5 feet in cm", 152.4),
    ("2 kg in lb", 4.409),
    ("5 அடி எத்தனை சென்டிமீட்டர்", 152.4),
    ("100 celsius in fahrenheit", 212.0),
])
def test_the_calculator_answers_with_the_right_number(question, expect):
    out = money.solve(question)
    assert out and out["ok"], question
    assert float(out["result"]) == pytest.approx(expect, abs=0.02)


def test_every_money_answer_shows_its_steps():
    out = money.solve("GST on 2499")
    assert out["steps"] and out["tax"] == pytest.approx(449.82, abs=0.01)


def test_a_question_that_is_not_maths_is_left_alone():
    for text in ("what is my name", "remind me at 4", "hello"):
        assert money.solve(text) is None


def test_money_is_rounded_the_way_a_shop_rounds():
    assert money.solve("18% of 1250.55")["result"] == pytest.approx(225.10, abs=0.01)


def test_the_calculator_tool_uses_the_money_engine(brain):
    res = brain.registry.call("calculate", {"expression": "GST on 2499"}, brain.tool_ctx)
    assert res["ok"] and res["result"] == pytest.approx(2948.82, abs=0.01)
    plain = brain.registry.call("calculate", {"expression": "2+3*4"}, brain.tool_ctx)
    assert plain["result"] == 14


def test_the_calculator_refuses_code_loudly(brain):
    from brain.agent.tools import ToolError

    with pytest.raises(ToolError):
        brain.registry.call("calculate", {"expression": "__import__('os')"}, brain.tool_ctx)


def test_a_money_question_is_answered_offline(brain):
    turn = brain.handle("18% of 1250")
    assert "225" in turn.answer


# --------------------------------------------------------------------------- #
# over HTTP
# --------------------------------------------------------------------------- #
@pytest.fixture()
def srv(config, vault, memory):
    from brain.api.gateway import Gateway, _Handler

    gw = Gateway(config, vault, memory, token="t" * 24)
    _Handler.gateway = gw
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def call(httpd, path, body=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{httpd.server_port}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + "t" * 24,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_reminders_over_http(srv):
    s, d = call(srv, "/v1/reminders")
    assert s == 200 and d["count"] == 0
    s, d = call(srv, "/v1/reminders", {"text": "in 10 minutes to drink water"})
    assert s == 200 and d["title"] == "drink water"
    s, d = call(srv, "/v1/reminders")
    assert d["count"] == 1 and "drink water" in d["line"]
    s, d = call(srv, "/v1/reminders/close", {"id": d["reminders"][0]["id"],
                                             "action": "done"})
    assert s == 200 and d["ok"]


def test_http_refuses_a_reminder_without_a_time(srv):
    s, d = call(srv, "/v1/reminders", {"text": "remind me about nothing"})
    assert s == 400 and "time" in d["error"]


def test_http_says_so_when_the_id_is_unknown(srv):
    s, d = call(srv, "/v1/reminders/close", {"id": "rem_nope", "action": "done"})
    assert s == 404
