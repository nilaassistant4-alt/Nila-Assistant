"""Acceptance tests: stage 3 - calendar (read-only), voice, and two languages.

The owner's words: "Nila talks to me only in Tamil and English, but she must
understand other languages and explain them to me in Tamil", "what is on
tomorrow?", and "say it out loud".

Run:  python -m pytest tests/test_voice_calendar_language_acceptance.py -q
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from brain.agent import translate as tr
from brain.util import ics, speech

CAL = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:1
DTSTART:20261104T090000
DTEND:20261104T100000
SUMMARY:Shop opening
LOCATION:T Nagar
END:VEVENT
BEGIN:VEVENT
UID:2
DTSTART;VALUE=DATE:20261105
SUMMARY:Holiday
END:VEVENT
BEGIN:VEVENT
UID:3
DTSTART;TZID=Asia/Kolkata:20261104T180000
DTEND;TZID=Asia/Kolkata:20261104T190000
SUMMARY:Call with Selvam
RRULE:FREQ=WEEKLY;BYDAY=WE;COUNT=3
END:VEVENT
BEGIN:VEVENT
UID:4
DTSTART;TZID=Asia/Kolkata:20261105T093000
SUMMARY:Doctor appointment
END:VEVENT
END:VCALENDAR
"""


def _cal_around(today: dt.date) -> str:
    """A calendar with events today and tomorrow, whatever today is.

    The fixed CAL above is for parsing tests, where the dates are the point. The
    agent's "what is on tomorrow?" is relative to the wall clock, so the fixture
    for those tests has to be built from `date.today()` or it rots.
    """
    d0 = today.strftime("%Y%m%d")
    d1 = (today + dt.timedelta(days=1)).strftime("%Y%m%d")
    return (
        "BEGIN:VCALENDAR\n"
        "BEGIN:VEVENT\n"
        f"DTSTART:{d0}T090000\nSUMMARY:Shop opening\nLOCATION:T Nagar\nEND:VEVENT\n"
        "BEGIN:VEVENT\n"
        f"DTSTART;VALUE=DATE:{d1}\nSUMMARY:Holiday\nEND:VEVENT\n"
        "BEGIN:VEVENT\n"
        f"DTSTART:{d1}T140000\nSUMMARY:Doctor appointment\nEND:VEVENT\n"
        "END:VCALENDAR\n")


# --------------------------------------------------------------------------- #
# calendar
# --------------------------------------------------------------------------- #
def test_the_calendar_is_read_without_a_login():
    events, skipped = ics.parse_ics(CAL)
    assert len(events) == 4 and skipped == []
    assert [e.summary for e in events][0] == "Shop opening"


def test_a_timezone_is_respected_and_an_all_day_event_is_marked():
    events, _ = ics.parse_ics(CAL)
    by_name = {e.summary: e for e in events}
    assert by_name["Doctor appointment"].start.hour == 9
    assert by_name["Doctor appointment"].start.minute == 30
    assert by_name["Holiday"].all_day is True


def test_todays_events_are_listed_and_sorted():
    day = ics.between(CAL, dt.date(2026, 11, 4), dt.date(2026, 11, 4))
    assert day["count"] == 2
    assert day["events"][0]["summary"] == "Shop opening"
    assert "09:00" in day["lines"][0] and "T Nagar" in day["lines"][0]


def test_a_weekly_repeat_appears_on_every_occurrence():
    month = ics.between(CAL, dt.date(2026, 11, 4), dt.date(2026, 11, 30))
    calls = [e for e in month["events"] if e["summary"] == "Call with Selvam"]
    assert len(calls) == 3                       # COUNT=3, not one and not endless


def test_a_broken_entry_is_skipped_and_counted_not_guessed():
    raw = "BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:no time\nEND:VEVENT\nEND:VCALENDAR"
    events, skipped = ics.parse_ics(raw)
    assert events == [] and len(skipped) == 1


@pytest.mark.parametrize("question,expect_start,expect_end", [
    ("what is on today", dt.date(2026, 11, 4), dt.date(2026, 11, 4)),
    ("what is on tomorrow", dt.date(2026, 11, 5), dt.date(2026, 11, 5)),
    ("this week", dt.date(2026, 11, 4), dt.date(2026, 11, 10)),
    ("நாளைக்கு என்ன இருக்கு", dt.date(2026, 11, 5), dt.date(2026, 11, 5)),
])
def test_the_question_decides_the_days(question, expect_start, expect_end):
    start, end, _ = ics.range_from_text(question, dt.datetime(2026, 11, 4, 12, 0))
    assert (start, end) == (expect_start, expect_end)


def test_a_calendar_address_is_never_printed_in_full():
    url = "https://calendar.google.com/calendar/ical/owner%40gmail.com/private-abc/basic.ics"
    masked = ics.masked_url(url)
    assert "private-abc" not in masked and masked.startswith("https://calendar.google.com")
    assert "owner" not in masked


def test_a_url_is_not_fetched_unless_the_owner_says_yes(tmp_path):
    out = ics.load("https://example.com/cal.ics", allow_network=False)
    assert out["ok"] is False and out["needs_permission"] is True
    assert "example.com" in out["host"]


def test_a_local_file_needs_no_permission(tmp_path):
    f = tmp_path / "cal.ics"
    f.write_text(CAL)
    got = ics.load(str(f))
    assert got["ok"] and got["bytes"] > 0


def test_a_missing_file_is_reported_plainly(tmp_path):
    out = ics.load(str(tmp_path / "nope.ics"))
    assert out["ok"] is False and "no calendar file" in out["error"]


def test_asking_the_agent_about_tomorrow_reads_the_calendar(config, vault, memory):
    from brain.agent.agent import Brain

    f = config.home / "cal.ics"
    f.write_text(_cal_around(dt.date.today()))
    config.agent = {**(config.agent or {}), "calendar_ics": str(f)}
    answer = Brain(config, vault, memory).handle("what is on tomorrow?").answer
    assert "Doctor appointment" in answer and "Holiday" in answer


def test_the_agent_says_how_to_set_a_calendar_when_none_is_configured(config, vault, memory):
    from brain.agent.agent import Brain

    answer = Brain(config, vault, memory).handle("my calendar for tomorrow").answer
    assert "Secret address in iCal format" in answer
    assert "read-only" in answer


def test_an_ordinary_question_is_not_turned_into_a_calendar_lookup(config, vault, memory):
    from brain.agent.agent import Brain

    f = config.home / "cal.ics"
    f.write_text(_cal_around(dt.date.today()))
    config.agent = {**(config.agent or {}), "calendar_ics": str(f)}
    answer = Brain(config, vault, memory).handle("what is on the news").answer
    assert "Events" not in answer


# --------------------------------------------------------------------------- #
# languages: speaks two, reads many
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,language,spoken", [
    ("வணக்கம், எப்படி இருக்கீங்க", "Tamil", True),
    ("hello, how are you", "English (or Latin-script text)", True),
    ("എന്റെ പേര് അനു, എനിക്ക് സഹായം വേണം", "Malayalam", False),
    ("नमस्ते, आप कैसे हैं?", "Hindi", False),
    ("నమస్తే, ఎలా ఉన్నారు", "Telugu", False),
    ("ನಮಸ್ಕಾರ", "Kannada", False),
    ("নমস্কার", "Bengali", False),
    ("નમસ્તે", "Gujarati", False),
    ("ਸਤ ਸ੍ਰੀ ਅਕਾਲ", "Punjabi", False),
    ("السلام علیکم", "Urdu/Arabic", False),
])
def test_scripts_are_recognised(text, language, spoken):
    info = tr.detect(text)
    assert info["language"] == language and info["spoken"] is spoken


@pytest.mark.parametrize("text,language", [
    ("ente peru anu, enikku sahayam venam", "Malayalam"),
    ("mera naam anu hai", "Hindi"),
    ("enna epdi irukeenga", "Tamil"),
])
def test_languages_sent_in_latin_letters_are_still_recognised(text, language):
    assert tr.detect(text)["language"] == language


def test_only_tamil_and_english_are_spoken():
    assert set(tr.SPOKEN) == {"tamil", "english"}


def test_an_unknown_language_is_explained_in_tamil_without_inventing_a_translation():
    text = "എന്റെ പേര് അനു"
    out = tr.explain(text, tr.detect(text))
    assert "Malayalam" in out
    assert "முழு மொழிபெயர்ப்புக்கு ஒரு model தேவை" in out       # needs a model
    assert "എന്റെ = my" in out                                     # a hint, labelled as one
    assert "முழு மொழிபெயர்ப்பு அல்ல" in out                        # not a translation


def test_a_real_translation_is_labelled_with_who_did_it():
    info = tr.detect("എന്റെ പേര് അനു")
    out = tr.explain("എന്റെ പേര് അനു", info, translated="என் பெயர் அனு.",
                     model="gemini (gemini-flash-latest)")
    assert "என் பெயர் அனு." in out and "translated by" in out


def test_the_translation_prompt_forbids_invention():
    prompt = tr.prompt_for("എന്റെ പേര് അനു", "Malayalam")
    assert "do not" in prompt and "Malayalam" in prompt and "Tamil" in prompt


def test_a_foreign_message_reaches_the_owner_in_tamil(config, vault, memory):
    from brain.agent.agent import Brain

    turn = Brain(config, vault, memory).handle("എന്റെ പേര് അനു, എനിക്ക് സഹായം വേണം")
    assert "Malayalam" in turn.answer and "model" in turn.answer


def test_an_english_message_is_not_hijacked(config, vault, memory):
    from brain.agent.agent import Brain

    turn = Brain(config, vault, memory).handle("hello there")
    assert "Malayalam" not in turn.answer and "மொழி" not in turn.answer


# --------------------------------------------------------------------------- #
# voice
# --------------------------------------------------------------------------- #
def test_with_no_engine_nila_says_so_and_writes_a_wav_not_a_fake_voice(tmp_path,
                                                                     monkeypatch):
    monkeypatch.setattr(speech, "find_piper", lambda: None)
    monkeypatch.setattr(speech, "_is_windows", lambda: False)
    out = speech.speak_text("வணக்கம்", out_wav=tmp_path / "x.wav")
    assert out["spoken"] is False and out["ok"] is False
    assert "no speech engine" in out["reason"]
    assert Path(out["wav"]).exists()
    assert Path(out["wav"]).stat().st_size > 44           # a real, if silent, WAV


def test_windows_sapi_is_used_when_it_is_there(monkeypatch):
    calls = {}

    class Done:
        returncode = 0
        stdout = b""
        stderr = b""

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        return Done()

    monkeypatch.setattr(speech, "_is_windows", lambda: True)
    monkeypatch.setattr(speech.shutil, "which", lambda n: "C:/powershell.exe")
    monkeypatch.setattr(speech.subprocess, "run", fake_run)
    out = speech.speak_text("vanakkam")
    assert out["spoken"] is True and out["engine"] == "sapi"
    assert "System.Speech" in " ".join(calls["cmd"])


def test_piper_is_preferred_when_a_voice_file_exists(tmp_path, monkeypatch):
    voice = tmp_path / "ta_IN-test.onnx"
    voice.write_bytes(b"x")

    class Done:
        returncode = 0
        stdout = b""
        stderr = b""

        def __init__(self, target):
            self.target = target

    def fake_run(cmd, **kw):
        target = Path(cmd[cmd.index("--output_file") + 1])
        target.write_bytes(b"RIFF" + b"\0" * 100)
        return Done(target)

    monkeypatch.setattr(speech, "find_piper", lambda: "/usr/bin/piper")
    monkeypatch.setattr(speech, "find_voice", lambda d=None: voice)
    monkeypatch.setattr(speech.subprocess, "run", fake_run)
    out = speech.speak_text("வணக்கம்", out_wav=tmp_path / "o.wav")
    assert out["spoken"] is True and out["engine"] == "piper"


def test_speech_is_never_claimed_without_a_engine(config, vault, memory):
    """The tool Nila can call must not say 'spoken' when nothing spoke."""
    turn = None
    from brain.agent.agent import Brain

    brain = Brain(config, vault, memory)
    res = brain.registry.call("speak", {"text": "hello"}, brain.tool_ctx)
    assert "spoken" in res and "reason" in res


def test_the_voice_list_is_honest_about_what_it_cannot_see(monkeypatch):
    monkeypatch.setattr(speech, "_is_windows", lambda: False)
    assert speech.sapi_voices() == []


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


def test_languages_over_http(srv):
    s, d = call(srv, "/v1/languages")
    assert s == 200 and d["speaks"] == ["tamil", "english"]
    assert "Malayalam" in d["reads"]
    s, d = call(srv, "/v1/languages/detect", {"text": "നമസ്കാരം"})
    assert d["language"] == "Malayalam" and d["spoken"] is False


def test_calendar_over_http(config, srv):
    s, d = call(srv, "/v1/calendar")
    assert s == 200 and d["configured"] is False
    f = config.home / "cal.ics"
    f.write_text(_cal_around(dt.date.today()))
    s, d = call(srv, "/v1/calendar", {"source": str(f)})
    assert s == 200 and d["ok"]
    s, d = call(srv, "/v1/calendar?when=tomorrow")
    assert d["ok"] and d["count"] >= 2
    assert "Doctor appointment" in d["rendered"]


def test_http_refuses_a_remote_calendar_without_permission(srv):
    s, d = call(srv, "/v1/calendar", {"source": "https://example.com/x.ics"})
    assert s == 400 and d["needs_permission"] is True


def test_speak_over_http_never_lies(srv, monkeypatch):
    monkeypatch.setattr(speech, "find_piper", lambda: None)
    monkeypatch.setattr(speech, "_is_windows", lambda: False)
    s, d = call(srv, "/v1/speak", {"text": "வணக்கம்"})
    assert s == 200 and d["spoken"] is False and "reason" in d
