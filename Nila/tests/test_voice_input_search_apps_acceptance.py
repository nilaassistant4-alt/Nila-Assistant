"""Acceptance tests: stage 7 - hearing you, finding a file, opening an app.

The owner's rules, in their words:

* "I want to talk to her instead of typing - press a key, say it, she does it";
* "where is that bill? find it on my PC";
* "open notepad / chrome / calculator - by name, nothing else";
* "and her reply comes out of the speaker, in my language";
* and the standing one: "only my voice - anyone else's voice is refused".

The hard truth this stage has to keep: **the hearing is a wiring job.** No
speech-to-text engine and no microphone exist in this build, so every test below
that needs words stubs the engine and checks the honest answer that comes back
when nothing is installed.

Run:  python -m pytest tests/test_voice_input_search_apps_acceptance.py -q
"""
from __future__ import annotations

import math
import random
import struct
from pathlib import Path

import pytest

from brain.agent import ears
from brain.security import permission
from brain.security import speaker as spk
from brain.util import apps as appmod
from brain.util import finder
from brain.util import launcher
from brain.util.audio import SAMPLE_RATE, write_wav


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def voice(seed: int, seconds: float = 2.0, f0: float = 120.0, noise: float = 0.05):
    rnd = random.Random(seed)
    n = int(SAMPLE_RATE * seconds)
    return [0.4 * math.sin(2 * math.pi * f0 *
                            (1 + 0.02 * math.sin(2 * math.pi * 3 * i / SAMPLE_RATE))
                            * i / SAMPLE_RATE) + rnd.uniform(-noise, noise)
            for i in range(n)]


class Mem:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def put(self, k, v):
        self.d[k] = v

    def delete(self, k):
        self.d.pop(k, None)

    def close(self):            # the CLI closes its memory handle in a finally
        pass


@pytest.fixture()
def lock(tmp_path):
    return spk.SpeakerLock(tmp_path / "speaker.json", store=Mem())


@pytest.fixture()
def tree(tmp_path):
    base = tmp_path / "pc"
    (base / "sub").mkdir(parents=True)
    (base / ".git").mkdir()
    (base / "notes.txt").write_text("the electricity bill is due on the 12th\n")
    (base / "sub" / "invoice_2026.txt").write_text("paid\n")
    (base / "sub" / "movie.mp4").write_bytes(b"\x00" * 32)
    (base / ".git" / "config").write_text("electricity bill\n")
    return base


# --------------------------------------------------------------------------- #
# 1. hearing: honest when nothing is installed
# --------------------------------------------------------------------------- #
def test_the_status_says_what_is_missing_and_how_to_fix_it():
    status = ears.available()
    assert isinstance(status["engines"], list)
    assert set(status["engines"]).issubset(set(ears.ENGINES))
    # Every engine named in the report has a copy-paste command, not a click.
    for line in status["how"].split(";"):
        assert "install" in line or "no speech-to-text" in line
    assert "whisper" in status["how"]


def test_listening_without_a_microphone_is_an_error_not_an_empty_string(monkeypatch):
    monkeypatch.setattr(ears, "available", lambda: {
        "engines": [], "microphone": False, "best": None, "how": "install one"})
    with pytest.raises(ears.EarsError) as exc:
        ears.hear(seconds=1.0)
    assert "microphone" in str(exc.value) or "install" in str(exc.value)


def test_no_engine_is_an_error_that_names_the_install_line(monkeypatch):
    # A microphone that works and nothing to transcribe with: the audio exists
    # and the answer is still a refusal, with the copy-paste line for each engine.
    how = "; ".join(meta["install"] for meta in ears.ENGINES.values())
    monkeypatch.setattr(ears, "available", lambda: {
        "engines": [], "microphone": True, "best": None, "how": how})
    monkeypatch.setattr(ears, "record", lambda seconds=5.0: _audio(voice(1)))
    with pytest.raises(ears.EarsError) as exc:
        ears.hear(seconds=1.0)
    message = str(exc.value)
    assert "pip install" in message
    assert "whisper" in message and "vosk" in message


def _audio(samples, rate: int = SAMPLE_RATE):
    from brain.util.audio import AudioSample

    return AudioSample(samples=[float(s) for s in samples], sample_rate=rate)


def test_a_wav_is_transcribed_and_the_audio_comes_back_with_it(tmp_path, monkeypatch):
    path = tmp_path / "say.wav"
    write_wav(path, voice(1, seconds=2.0))
    monkeypatch.setattr(ears, "available", lambda: {
        "engines": ["vosk"], "microphone": False, "best": "vosk", "how": ""})
    monkeypatch.setattr(ears, "transcribe", lambda audio, **kw: {
        "text": "nila open notepad", "engine": "vosk", "language": "en"})
    heard = ears.hear(source=path)
    assert heard["text"] == "nila open notepad"
    assert heard["seconds"] == pytest.approx(2.0, abs=0.1)
    # The samples are handed back on purpose: the voice lock judges the same audio.
    assert len(heard["samples"]) > 1000 and heard["rate"] == SAMPLE_RATE


def test_an_unreadable_file_is_reported_plainly(tmp_path):
    with pytest.raises(Exception) as exc:
        ears.hear(source=tmp_path / "missing.wav")
    assert "not found" in str(exc.value).lower()


def test_the_microphone_is_its_own_capability():
    assert permission.CAP_MIC == "mic.listen"
    entry = permission.CAPABILITIES[permission.CAP_MIC]
    assert entry["risk"] == "high" and entry["durable_ok"] is False


# --------------------------------------------------------------------------- #
# 2. strangers: their words are thrown away
# --------------------------------------------------------------------------- #
def build_ctx(tmp_path, memory=None):
    from brain.agent.tools import ToolContext

    class Cfg:
        home = tmp_path
        agent = {}

    return ToolContext(memory=memory if memory is not None else Mem(), vault=None,
                       config=Cfg())


def test_a_stranger_is_heard_but_their_words_are_discarded(tmp_path, monkeypatch):
    """The whole point of listening through the lock: text from another voice dies."""
    from brain.agent import tools as tools_mod

    home = tmp_path / "home"
    home.mkdir()
    (home / "speaker.json").parent.mkdir(exist_ok=True)
    store = Mem()                       # the voiceprint lives in the sealed store
    lock = spk.SpeakerLock(home / "speaker.json", store=store)
    lock.enrol(voice(1, f0=120), SAMPLE_RATE)
    lock.enrol(voice(2, f0=121), SAMPLE_RATE)
    registry = tools_mod.build_default_registry()
    tool = registry._tools["listen"]

    stranger = {"text": "nila open notepad", "engine": "stub", "seconds": 2.0,
                "rate": SAMPLE_RATE, "samples": voice(9, f0=210, noise=0.35)}
    monkeypatch.setattr(ears, "hear", lambda **kw: dict(stranger))
    out = tool.handler(build_ctx(home, store), seconds=2.0, file="")
    assert out["ok"] is False and out["words_discarded"] is True
    assert "text" not in out                    # the words never leave the tool

    owner = {"text": "nila open notepad", "engine": "stub", "seconds": 2.0,
             "rate": SAMPLE_RATE, "samples": voice(3, f0=122)}
    monkeypatch.setattr(ears, "hear", lambda **kw: dict(owner))
    out = tool.handler(build_ctx(home, store), seconds=2.0, file="")
    assert out["ok"] is True and out["text"] == "nila open notepad"
    assert out["verdict"] == "owner"


def test_a_broken_ear_says_why_and_does_not_crash(tmp_path, monkeypatch):
    from brain.agent import tools as tools_mod

    def broken(**kw):
        raise ears.EarsError("microphone capture needs `pip install sounddevice numpy`")

    monkeypatch.setattr(ears, "hear", broken)
    registry = tools_mod.build_default_registry()
    out = registry._tools["listen"].handler(build_ctx(tmp_path), seconds=2.0, file="")
    assert out["ok"] is False and "sounddevice" in out["error"]


def test_the_voiceprint_survives_a_restart(tmp_path):
    """Found in this stage: the lock looked for speaker.json and so forgot the
    enrolled voice on the next run. The voiceprint lives in the sealed store."""
    store = Mem()
    path = tmp_path / "speaker.json"
    first = spk.SpeakerLock(path, store=store)
    first.enrol(voice(1, f0=120), SAMPLE_RATE)
    second = spk.SpeakerLock(path, store=store)       # as if Nila was restarted
    assert second.enrolled() is True
    assert second.judge(voice(3, f0=122), SAMPLE_RATE).ok is True
    assert not path.exists()                          # nothing leaked to disk


def test_the_owner_only_rule_is_the_same_rule_as_stage_6(lock):
    # The lock the listening tool uses is the stage-6 lock, not a second opinion.
    lock.enrol(voice(1, f0=120), SAMPLE_RATE)
    lock.enrol(voice(2, f0=121), SAMPLE_RATE)
    assert lock.judge(voice(3, f0=122), SAMPLE_RATE).ok is True
    assert lock.judge(voice(4, f0=200, noise=0.3), SAMPLE_RATE).ok is False


# --------------------------------------------------------------------------- #
# 3. spoken sentences become actions, and nothing else
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("said,action,argument", [
    ("nila start", "start", ""),
    ("Nila, open notepad", "open", "notepad"),
    ("nila find invoice", "find", "invoice"),
    ("nila read D:/bill.pdf.", "read", "d:/bill.pdf"),
    ("நிலா திற chrome", "open", "chrome"),
    ("nila stop", "stop", ""),
])
def test_a_spoken_sentence_is_split_into_action_and_argument(said, action, argument):
    out = launcher.spoken_action(said)
    assert out["action"] == action and out["argument"] == argument


@pytest.mark.parametrize("said", ["hello there", "nila", "nila dance now"])
def test_an_unclear_sentence_says_so_instead_of_guessing(said):
    out = launcher.spoken_action(said)
    assert out["action"] == "" and out["reason"]
    assert "dance" not in out["argument"] or "do not know" in out["reason"]


# --------------------------------------------------------------------------- #
# 4. finding a file
# --------------------------------------------------------------------------- #
def test_find_by_name_skips_junk_folders(tree):
    out = finder.search(tree, "invoice")
    assert out["count"] == 1 and out["hits"][0]["name"] == "invoice_2026.txt"
    assert out["scanned"] >= 2


def test_find_by_content_is_opt_in_and_returns_the_line(tree):
    names_only = finder.search(tree, "electricity")
    assert names_only["count"] == 0                      # not a name, so no match
    inside = finder.search(tree, "electricity", content=True)
    assert inside["count"] == 1
    assert inside["hits"][0]["name"] == "notes.txt"
    assert "electricity bill" in inside["hits"][0]["snippet"]
    # .git is skipped either way: a search is not a reason to read its internals.
    assert all(".git" not in h["path"] for h in inside["hits"])


def test_find_says_how_much_it_looked_at(tree):
    out = finder.search(tree, "nothing_matches_this")
    assert out["count"] == 0 and out["scanned"] >= 2
    assert "checked" in out["rendered"]


def test_find_refuses_a_folder_that_is_not_there(tmp_path):
    out = finder.search(tmp_path / "nope", "bill")
    assert out["ok"] is False and "not a folder" in out["error"]


def test_find_with_no_query_asks_for_one(tmp_path):
    out = finder.search(tmp_path, "   ")
    assert out["ok"] is False and "look for" in out["error"]


def test_the_find_tool_uses_the_configured_folder(tmp_path, tree):
    from brain.agent import tools as tools_mod

    registry = tools_mod.build_default_registry()
    ctx = build_ctx(tree)
    out = registry._tools["find_file"].handler(ctx, query="invoice", root=str(tree))
    assert out["count"] == 1


# --------------------------------------------------------------------------- #
# 5. opening an app: a fixed list, never a free-text command
# --------------------------------------------------------------------------- #
def test_every_listed_name_resolves_to_one_program_and_its_own_arguments():
    checked = 0
    for row in appmod.list_apps():
        try:
            plan = appmod.resolve(row["name"])
        except appmod.AppError as exc:
            # "word/excel on Linux" is a name from another platform, and saying so
            # is a correct answer; an unknown name would never get here.
            assert "not available on" in str(exc)
            continue
        assert plan["ok"] is True
        assert isinstance(plan["command"], str) and plan["command"]
        assert all(isinstance(a, str) for a in plan["args"])
        checked += 1
    assert checked >= 8                       # most names must really resolve


def test_a_folder_opener_keeps_its_dot_as_an_argument():
    plan = appmod.resolve("files")
    assert "." not in plan["command"]           # "." is never the program
    assert plan["args"] == ["."] or plan["command"] == "explorer.exe"


def test_a_name_outside_the_list_is_refused_and_a_hint_is_offered():
    with pytest.raises(appmod.AppError) as exc:
        appmod.resolve("rm")
    message = str(exc.value)
    assert "do not have 'rm'" in message
    assert "terminal" in message                # the nearest thing it does know


def test_an_unknown_name_never_reaches_a_shell():
    with pytest.raises(appmod.AppError):
        appmod.open_app("rm -rf /")


def test_every_app_is_started_without_a_shell(monkeypatch):
    seen = {}

    class Proc:
        pid = 4242

    def fake_popen(args, **kw):
        seen["args"] = args
        seen["kw"] = kw
        return Proc()

    monkeypatch.setattr(appmod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(appmod, "resolve", lambda name, config=None: {
        "ok": True, "name": name, "command": "/usr/bin/gedit", "args": ["a.txt"],
        "found": True, "uri": False})
    out = appmod.open_app("notepad")
    assert out["pid"] == 4242
    assert seen["args"] == ["/usr/bin/gedit", "a.txt"]
    assert seen["kw"]["shell"] is False


def test_a_dry_run_starts_nothing_at_all(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a preview must not start a process")

    monkeypatch.setattr(appmod.subprocess, "Popen", boom)
    out = appmod.open_app("files", dry_run=True)
    assert out["dry_run"] is True and out["would_run"]


def test_a_missing_program_is_said_not_swallowed(monkeypatch):
    monkeypatch.setattr(appmod, "resolve", lambda name, config=None: {
        "ok": True, "name": name, "command": "/opt/nothing/here", "args": [],
        "found": False, "uri": False})
    with pytest.raises(appmod.AppError) as exc:
        appmod.open_app("notepad")
    assert "not installed" in str(exc.value)


def test_the_owner_can_add_their_own_names(tmp_path):
    config = type("C", (), {"agent": {"apps": {"studio": ["code", "--new-window"]}},
                            "home": tmp_path})()
    plan = appmod.resolve("studio", config=config)
    assert plan["command"] == "code" and plan["args"] == ["--new-window"]
    assert "studio" in [row["name"] for row in appmod.list_apps(config)]


def test_the_open_tool_refuses_a_name_it_does_not_know(tmp_path):
    from brain.agent import tools as tools_mod

    registry = tools_mod.build_default_registry()
    out = registry._tools["open_app"].handler(build_ctx(tmp_path), name="rm")
    assert out["ok"] is False and "known" in out


# --------------------------------------------------------------------------- #
# 6. one press of an icon: the whole loop, with the honest refusals
# --------------------------------------------------------------------------- #
def make_home(tmp_path):
    """A real vault-lite home: only what cmd_talk touches."""
    home = tmp_path / "home"
    home.mkdir()
    return home


def test_hearing_nothing_stops_the_loop_before_any_action(tmp_path):
    from brain.cli import main as cli

    home = make_home(tmp_path)

    class Args:
        text = ""
        seconds = 1.0
        lang = "ta"
        model = "small"
        file = ""
        json = True
        no_speak = True
        speak_voice = ""
        voice = ""
        show = False
        skip_voice_check = False
        search_root = ""
        content = False
        limit = 5

    def fake_hear(**kw):
        return {"text": "", "samples": [], "rate": SAMPLE_RATE, "engine": "stub"}

    calls = {"opened": 0}

    class Never:
        @staticmethod
        def open_app(*a, **k):
            calls["opened"] += 1
            raise AssertionError("nothing may open when nothing was heard")

    import brain.agent.ears as ears_mod
    import brain.util.apps as apps_mod

    original_hear, original_open = ears_mod.hear, apps_mod.open_app
    ears_mod.hear, apps_mod.open_app = fake_hear, Never.open_app
    try:
        import brain.config as cfgmod

        class Ui:
            def emit(self, *a, **k):
                pass

            def err(self, *a, **k):
                pass

        config = type("C", (), {"home": home, "agent": {}})()
        vault = type("V", (), {"lock": lambda self: None})()
        memory = Mem()
        old_open_vault, old_open_memory = cli.open_vault, cli.open_memory
        cli.open_vault = lambda a, **k: (config, vault)
        cli.open_memory = lambda c, v: memory
        try:
            code = cli.cmd_talk(Args(), Ui())
        finally:
            cli.open_vault, cli.open_memory = old_open_vault, old_open_memory
    finally:
        ears_mod.hear, apps_mod.open_app = original_hear, original_open
    assert code == 1 and calls["opened"] == 0


def test_a_typed_sentence_opens_the_right_app_in_a_preview(tmp_path):
    from brain.cli import main as cli

    home = make_home(tmp_path)

    class Args:
        text = "nila open notepad"
        seconds = 1.0
        lang = "ta"
        model = "small"
        file = ""
        json = True
        no_speak = True
        speak_voice = ""
        show = True                 # preview: nothing is really launched
        skip_voice_check = True
        search_root = ""
        content = False
        limit = 5

    class Ui:
        def __init__(self):
            self.out = []

        def emit(self, payload, *a, **k):
            self.out.append(payload)

        def err(self, *a, **k):
            pass

    config = type("C", (), {"home": home, "agent": {}})()
    vault = type("V", (), {"lock": lambda self: None})()
    old_open_vault, old_open_memory = cli.open_vault, cli.open_memory
    cli.open_vault = lambda a, **k: (config, vault)
    cli.open_memory = lambda c, v: Mem()
    ui = Ui()
    try:
        code = cli.cmd_talk(Args(), ui)
    finally:
        cli.open_vault, cli.open_memory = old_open_vault, old_open_memory
    assert code in (0, 1)
    assert ui.out and ui.out[-1]["action"] == "open"
    assert ui.out[-1]["argument"] == "notepad"
    # A preview reports what it would run, and starts nothing.
    assert ui.out[-1]["app"].get("dry_run") is True
    assert ui.out[-1]["app"]["would_run"]


def test_a_sentence_nila_does_not_understand_is_answered_not_guessed(tmp_path):
    from brain.cli import main as cli

    home = make_home(tmp_path)

    class Args:
        text = "nila dance now"
        seconds = 1.0
        lang = "ta"
        model = "small"
        file = ""
        json = True
        no_speak = True
        speak_voice = ""
        show = True
        skip_voice_check = True
        search_root = ""
        content = False
        limit = 5

    class Ui:
        def __init__(self):
            self.out = []

        def emit(self, payload, *a, **k):
            self.out.append(payload)

        def err(self, *a, **k):
            pass

    config = type("C", (), {"home": home, "agent": {}})()
    vault = type("V", (), {"lock": lambda self: None})()
    old_open_vault, old_open_memory = cli.open_vault, cli.open_memory
    cli.open_vault = lambda a, **k: (config, vault)
    cli.open_memory = lambda c, v: Mem()
    ui = Ui()
    try:
        code = cli.cmd_talk(Args(), ui)
    finally:
        cli.open_vault, cli.open_memory = old_open_vault, old_open_memory
    assert code == 1
    assert ui.out[-1]["ok"] is False and "do not know" in ui.out[-1]["reason"]


def test_the_push_to_talk_icon_is_bounded_and_written(tmp_path):
    out = launcher.push_to_talk_launchers(tmp_path, tmp_path / "desk", max_seconds=6)
    assert out["bounded"] is True and out["seconds"] == 6
    written = Path(out["written"]["talk"])
    assert written.exists()
    text = written.read_text()
    assert "talk" in text and "--seconds" in text and "6" in text
    # It is a listen, not a permanent listener: no bare `brain start` in the file.
    assert " start" not in text


# --------------------------------------------------------------------------- #
# 6. the tools are registered with the right powers
# --------------------------------------------------------------------------- #
def test_the_new_tools_carry_the_powers_they_need():
    from brain.agent import tools as tools_mod

    registry = tools_mod.build_default_registry()
    specs = {name: registry._tools[name] for name in
             ("find_file", "open_app", "make_folder", "listen")}
    assert specs["find_file"].capability == permission.CAP_FILE_LIST
    assert specs["open_app"].capability == permission.CAP_SHELL
    assert specs["make_folder"].capability == permission.CAP_FILE_WRITE
    assert specs["listen"].capability == permission.CAP_MIC
