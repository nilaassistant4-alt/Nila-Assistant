"""Acceptance tests: stage 6 - one voice only, any file, the D: archive.

The owner's rules, in their words:

* "only my voice may start her, approve anything, or be listened to - anyone
  else's voice must never be accepted, not even to say stop";
* "whatever the file is, read it and explain it short and sweet - and however big
  the code, tell me very briefly";
* "1 GB, 10 GB video, it has to take it in and analyse it";
* "put the storage on D:, in a folder called Nila Assistant Storage";
* "when I say delete, delete - but nothing goes on its own".

Run:  python -m pytest tests/test_voice_lock_files_storage_acceptance.py -q
"""
from __future__ import annotations

import math
import random
import struct
import zipfile
from pathlib import Path

import pytest

from brain.agent import media
from brain.config import Config, StorageUnavailable
from brain.security import speaker as spk
from brain.util import launcher
from brain.util.audio import SAMPLE_RATE


# --------------------------------------------------------------------------- #
# helpers: deterministic voices, so a stranger is always a stranger
# --------------------------------------------------------------------------- #
def voice(seed: int, seconds: float = 2.0, f0: float = 120.0,
          noise: float = 0.05):
    rnd = random.Random(seed)
    n = int(SAMPLE_RATE * seconds)
    return [0.4 * math.sin(2 * math.pi * f0 *
                            (1 + 0.02 * math.sin(2 * math.pi * 3 * i / SAMPLE_RATE))
                            * i / SAMPLE_RATE) + rnd.uniform(-noise, noise)
            for i in range(n)]


class Mem:
    """The sealed store, in memory."""

    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def put(self, k, v):
        self.d[k] = v

    def delete(self, k):
        self.d.pop(k, None)


@pytest.fixture()
def lock(tmp_path):
    return spk.SpeakerLock(tmp_path / "speaker.json", store=Mem())


# --------------------------------------------------------------------------- #
# the voice lock
# --------------------------------------------------------------------------- #
def test_nobody_is_recognised_before_enrolment(lock):
    decision = lock.judge(voice(1), SAMPLE_RATE)
    assert decision.ok is False and decision.verdict == "no_profile"
    assert "enrol" in decision.reason


def test_the_owner_is_recognised(lock):
    lock.enrol(voice(1, f0=120), SAMPLE_RATE)
    lock.enrol(voice(2, f0=121), SAMPLE_RATE)
    decision = lock.judge(voice(8, f0=122), SAMPLE_RATE)
    assert decision.ok is True and decision.verdict == "owner"


def test_a_different_voice_is_never_accepted(lock):
    for seed, f0 in ((1, 120), (2, 121), (3, 119)):
        lock.enrol(voice(seed, f0=f0), SAMPLE_RATE)
    for seed, f0 in ((4, 160), (5, 210), (7, 120)):
        decision = lock.judge(voice(seed, f0=f0, noise=0.4), SAMPLE_RATE)
        assert decision.ok is False, f"accepted a stranger at {f0} Hz"
        assert decision.acted is False


def test_a_short_sample_is_refused_rather_than_guessed(lock):
    lock.enrol(voice(1), SAMPLE_RATE)
    decision = lock.judge(voice(2, seconds=0.4), SAMPLE_RATE)
    assert decision.ok is False and decision.verdict == "uncertain"
    assert "at least" in decision.reason


def test_enrolment_needs_a_real_length_of_speech(lock):
    with pytest.raises(spk.SpeakerError) as exc:
        lock.enrol(voice(1, seconds=0.4), SAMPLE_RATE)
    assert "at least" in str(exc.value)


def test_the_threshold_fits_the_owner_once_there_are_two_samples(lock):
    first = lock.enrol(voice(1, f0=120), SAMPLE_RATE)
    assert lock.accept_threshold() == spk.ACCEPT       # one sample: the default line
    lock.enrol(voice(2, f0=121), SAMPLE_RATE)
    lock.enrol(voice(3, f0=119), SAMPLE_RATE)
    assert lock.accept_threshold() != spk.ACCEPT or lock.profile["self_scores"]
    assert lock.accept_threshold() >= spk.REJECT + 0.10   # it never loosens to nothing


def test_a_replay_is_caught_by_the_spoken_number(lock):
    lock.enrol(voice(1), SAMPLE_RATE)
    first = lock.challenge()
    wrong = lock.verify_challenge(first["token"], voice(1, f0=121), SAMPLE_RATE,
                                  heard="0000")
    assert wrong.ok is False and "recording" in wrong.reason
    again = lock.verify_challenge(first["token"], voice(1, f0=121), SAMPLE_RATE,
                                  heard=first["number"])
    assert again.ok is False and "already used" in again.reason


def test_a_challenge_needs_the_right_voice_and_the_right_number(lock):
    lock.enrol(voice(1), SAMPLE_RATE)
    chal = lock.challenge()
    stranger = lock.verify_challenge(chal["token"], voice(9, f0=200), SAMPLE_RATE,
                                     heard=chal["number"])
    assert stranger.ok is False
    chal2 = lock.challenge()
    good = lock.verify_challenge(chal2["token"], voice(1, f0=121), SAMPLE_RATE,
                                 heard=chal2["number"])
    assert good.ok is True and good.verdict == "owner"


def test_a_challenge_without_a_voiceprint_is_refused(lock):
    with pytest.raises(spk.SpeakerError):
        lock.challenge()


def test_the_voice_lock_states_its_own_limits():
    assert "not a trained speaker model" in spk.LIMITS
    assert "ECAPA" in spk.LIMITS


# --------------------------------------------------------------------------- #
# "Nila start" - and nobody else's "stop"
# --------------------------------------------------------------------------- #
def test_only_the_owner_s_voice_may_start_or_stop(lock):
    lock.enrol(voice(1), SAMPLE_RATE)
    chal = lock.challenge()
    owner_check = lock.judge(voice(1, f0=121), SAMPLE_RATE)
    stranger_check = lock.judge(voice(9, f0=200), SAMPLE_RATE)
    own = launcher.spoken_trigger("Nila start", owner_check.ok, True)
    other = launcher.spoken_trigger("Nila stop", stranger_check.ok, True)
    assert own["ok"] is True and own["action"] == "start"
    assert other["ok"] is False and other["action"] is None
    assert "only the owner's voice" in other["reason"]


def test_the_phrase_is_matched_but_never_alone(lock):
    assert launcher.match_command("nila start") == "start"
    assert launcher.match_command("NILA, STOP") == "stop"
    assert launcher.match_command("நிலா தொடங்கு") == "start"
    assert launcher.match_command("start") is None          # the bare word is not a command
    assert launcher.match_command("nila dance") is None


def test_without_a_voiceprint_the_trigger_says_so_instead_of_pretending(lock):
    out = launcher.spoken_trigger("nila start", True, has_voiceprint=False)
    assert out["ok"] is True and out["warning"].startswith("no voiceprint")


def test_always_listening_is_offered_as_a_refusal_not_a_feature():
    plan = launcher.plan(None)
    modes = {m["id"]: m for m in plan["modes"]}
    assert modes["always_on"]["works"] is False
    assert "privacy" in modes["always_on"]["how"]
    assert modes["icon"]["works"] is True
    assert "cannot start herself" in plan["limits"]


def test_the_windows_launcher_has_no_console_window(tmp_path):
    text = launcher.windows_vbs("C:/py/python.exe", "C:/nila/brain.py", "C:/nila")
    assert "WScript.Shell" in text and ", 0," in text and "brain.py" in text
    assert "start" in text


def test_launcher_files_are_written_where_the_owner_can_read_them(tmp_path):
    out = launcher.write_launchers(tmp_path, out_dir=tmp_path / "Desktop")
    assert out["ok"] and out["written"]
    for path in out["written"]:
        assert Path(path).exists()
        assert Path(path).stat().st_size > 0


# --------------------------------------------------------------------------- #
# files: documents, code, images, video
# --------------------------------------------------------------------------- #
def test_a_docx_is_read_with_no_dependency(tmp_path):
    path = tmp_path / "memo.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml",
                   "<w:document><w:p><w:r><w:t>Hello Nila</w:t></w:r></w:p>"
                   "<w:p><w:r><w:t>Second line</w:t></w:r></w:p></w:document>")
    out = media.read_document(path)
    assert "Hello Nila" in out["text"] and "Second line" in out["text"]


def test_a_spreadsheet_is_read(tmp_path):
    path = tmp_path / "sheet.xlsx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/sharedStrings.xml", "<sst><si><t>Name</t></si><si><t>Anu</t></si></sst>")
        z.writestr("xl/worksheets/sheet1.xml",
                   '<worksheet><row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row></worksheet>')
    assert media.read_document(path)["text"].strip() == "Name | Anu"


def test_a_slide_deck_is_read(tmp_path):
    path = tmp_path / "deck.pptx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/slides/slide1.xml", "<p:sld><a:t>Opening</a:t><a:t>Welcome</a:t></p:sld>")
    text = media.read_document(path)["text"]
    assert "slide 1" in text and "Opening" in text


def test_a_pdf_with_text_is_read(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF-1.4\n1 0 obj<<>>stream\nBT (Invoice number 42) Tj ET\n"
                     b"endstream\nendobj\n%%EOF")
    out = media.read_document(path)
    assert "Invoice number 42" in out["text"] and out["scanned"] is False


def test_a_scanned_pdf_is_called_scanned_not_empty(tmp_path):
    path = tmp_path / "scan.pdf"
    path.write_bytes(b"%PDF-1.4\n1 0 obj<<>>stream\nendstream\nendobj\n%%EOF")
    out = media.read_document(path)
    assert out["scanned"] is True and "OCR" in out["note"]


def test_code_is_summarised_in_one_line(tmp_path):
    path = tmp_path / "m.py"
    path.write_text("import os\nimport sys\n\ndef alpha():\n    pass\n\n"
                    "class Beta:\n    pass\n# TODO tidy\n")
    out = media.summarize_code(path)
    assert out["defines"] == ["alpha", "Beta"]
    assert out["imports"] == ["os", "sys"]
    assert "9 lines" in out["rendered"] and "1 TODO" in out["rendered"]


def test_a_folder_is_summarised_without_walking_forever(tmp_path):
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text("x" * (i + 1))
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.py").write_text("print(1)")
    out = media.summarize_tree(tmp_path)
    assert out["files"] == 6
    assert out["by_kind"][".txt"] == 5


def test_an_image_reports_its_shape_not_a_made_up_caption(tmp_path):
    path = tmp_path / "p.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
                     + struct.pack(">II", 640, 480) + b"\x00" * 20)
    out = media.analyze_image(path)
    assert (out["width"], out["height"], out["format"]) == (640, 480, "png")
    assert out["caption"] == "" and "vision model" in out["note"]


def _mp4(path: Path, seconds_worth: int = 60000) -> Path:
    def box(tag: bytes, payload: bytes = b"") -> bytes:
        return struct.pack(">I", 8 + len(payload)) + tag + payload

    mvhd = (bytes([0, 0, 0, 0]) + b"\x00" * 8 + struct.pack(">I", 1000)
            + struct.pack(">I", seconds_worth) + b"\x00" * 80)
    data = box(b"ftyp", b"isom") + box(b"moov", box(b"mvhd", mvhd)) + box(b"mdat", b"x" * 200)
    path.write_bytes(data)
    return path


def test_a_video_is_measured_without_ffmpeg(tmp_path):
    out = media.analyze_video(_mp4(tmp_path / "clip.mp4"))
    assert out["duration_s"] == pytest.approx(60.0, abs=0.5)
    assert out["source"] == "mp4 boxes"
    assert "frame rate and codec need ffprobe" in out["note"]


def test_the_edit_plan_is_the_exact_ffmpeg_commands(tmp_path):
    plan = media.analyze_video(_mp4(tmp_path / "clip.mp4"))["edit_plan"]
    assert "scene,0.4" in plan["scene_detect"]
    assert "silencedetect" in plan["silence_detect"]
    assert "-c copy" in plan["trim_without_reencode"]


def test_a_10gb_file_is_never_read_into_memory(tmp_path, monkeypatch):
    """A sparse 10 GB file: the reader must seek, not read."""
    path = tmp_path / "huge.mp4"
    with open(path, "wb") as fh:
        fh.seek(10 * 1024 ** 3 - 1)
        fh.write(b"\0")
    calls = {"bytes": 0}
    real_open = open

    def counting_open(name, *a, **k):
        fh = real_open(name, *a, **k)
        if str(name) == str(path):
            read = fh.read

            def wrapped(n=-1):
                data = read(n)
                calls["bytes"] += len(data)
                return data
            fh.read = wrapped
        return fh

    monkeypatch.setattr("builtins.open", counting_open)
    out = media.analyze_video(path)
    assert out["gb"] == 10.0
    assert calls["bytes"] < 80 * 1024 * 1024      # seeks, never a full read


def test_an_unknown_file_is_said_to_be_unknown(tmp_path):
    path = tmp_path / "thing.xyz"
    path.write_bytes(b"abc")
    out = media.read_anything(path)
    assert out["kind"] == "unknown" and "do not have a reader" in out["rendered"]


def test_a_missing_file_is_refused_plainly(tmp_path):
    with pytest.raises(media.MediaError):
        media.read_anything(tmp_path / "nope.pdf")


def test_reading_a_file_is_gated_by_the_file_permission(config, vault, memory, tmp_path):
    from brain.agent.agent import Brain
    from brain.security.permission import CAP_FILE_READ

    brain = Brain(config, vault, memory)
    spec = brain.registry.get("read_file_report")
    assert spec is not None and spec.capability == CAP_FILE_READ
    target = tmp_path / "x.txt"
    target.write_text("hello")
    res = brain.registry.call("read_file_report", {"path": str(target)}, brain.tool_ctx)
    assert res["ok"] is False and res["error"] == "permission required"


# --------------------------------------------------------------------------- #
# storage on D:
# --------------------------------------------------------------------------- #
def test_the_archive_can_live_on_another_drive(tmp_path):
    cfg = Config(tmp_path)
    folder = tmp_path / "D_drive" / "Nila Assistant Storage"
    cfg.agent = {"storage_dir": str(folder)}
    assert cfg.memory_path == folder / "memory.db"


def test_asking_for_a_default_folder_is_not_a_side_effect(tmp_path):
    folder = tmp_path / "not_yet"
    cfg = Config(tmp_path)
    cfg.agent = {"storage_dir": str(folder)}
    _ = cfg.memory_path
    assert not folder.exists()


def test_a_missing_drive_refuses_instead_of_writing_to_c(tmp_path):
    cfg = Config(tmp_path)
    cfg.agent = {"storage_dir": "Q:/Nila Assistant Storage"}
    with pytest.raises(StorageUnavailable) as exc:
        _ = cfg.memory_path
    assert "not reachable" in str(exc.value)


def test_a_fallback_to_c_is_available_but_off_by_default(tmp_path):
    cfg = Config(tmp_path)
    cfg.agent = {"storage_dir": "Q:/Nila Assistant Storage"}
    with pytest.raises(StorageUnavailable):
        _ = cfg.memory_path
    cfg.agent = {**cfg.agent, "storage_fallback_to_c": True}
    assert cfg.memory_path == tmp_path / "memory.db"


def test_the_default_is_unchanged_when_nothing_is_configured(tmp_path):
    assert Config(tmp_path).memory_path == tmp_path / "memory.db"


def test_nothing_deletes_itself(tmp_path, config, vault, memory):
    """The owner's rule: deletion happens when they say so, never on a schedule."""
    from brain.agent.agent import Brain

    memory.remember("a note that must stay", title="keep me")
    brain = Brain(config, vault, memory)
    brain.handle("hello")
    brain.handle("remind me in 5 minutes to stretch")
    ids = [r.id for r in memory.list_records(limit=50)]
    brain.handle("hello again")
    assert sorted(r.id for r in memory.list_records(limit=50)) == sorted(ids)


def test_storage_and_launcher_and_voice_are_registered_commands():
    from brain.cli.main import build_parser

    parser = build_parser()
    for command in ("storage", "launcher", "voice", "file"):
        args = parser.parse_args([command] if command != "voice" else [command, "status"])
        assert callable(args.func)
