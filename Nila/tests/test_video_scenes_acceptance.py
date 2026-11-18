"""Acceptance tests: stage 12, gap 8 - the cuts in a video, and cutting it.

Two rows said "needs ffmpeg". One of them does not:

* **where the scenes change** can be read out of the video's own index. An MP4
  keeps a `stss` box listing the frames the encoder marked as independent, and
  those are the scene starts - exact cut points, no decode, fast on a 10 GB file.
  The tests build such a container byte by byte, so the reading is proved against
  a known answer (frames 1, 31, 61, 91 at 1 s intervals -> 1, 31, 61, 91 s).
* **comparing every frame to the next** (and finding silence) needs a decoder.
  That is refused in words, with the exact ffmpeg command and a way to get ffmpeg
  (`getffmpeg.bat`), which unpacks two files and installs nothing.

Cutting is the other half: `--trim 90,135` prints the command that copies the
stream rather than re-encoding it, and only runs it when asked, so a wrong cut
cannot happen because someone read the help text.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from brain.agent import video_edit
from tools import getffmpeg


def box(kind: bytes, *payloads: bytes) -> bytes:
    body = b"".join(payloads)
    return struct.pack(">I", 8 + len(body)) + kind + body


def mvhd(timescale: int = 1000, duration: int = 120_000) -> bytes:
    body = (b"\x00\x00\x00\x00" + struct.pack(">II", 0, 0)
            + struct.pack(">II", timescale, duration) + b"\x00" * 80)
    return box(b"mvhd", body)


def stss(frames) -> bytes:
    return box(b"stss", b"\x00" * 4 + struct.pack(">I", len(frames))
               + b"".join(struct.pack(">I", f) for f in frames))


def stts(count: int, delta: int) -> bytes:
    return box(b"stts", b"\x00" * 4 + struct.pack(">I", 1) + struct.pack(">II", count, delta))


def clip(tmp_path, frames=(1, 31, 61, 91), delta=1000, timescale=1000) -> Path:
    stbl = box(b"stbl", stts(120, delta), stss(list(frames)))
    trak = box(b"trak", box(b"mdia", box(b"minf", stbl)))
    data = (box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2")
            + box(b"moov", mvhd(timescale), trak)
            + box(b"mdat", b"\x00" * 32))
    path = tmp_path / "clip.mp4"
    path.write_bytes(data)
    return path


# --------------------------------------------------------------------------- #
# reading the cuts
# --------------------------------------------------------------------------- #
def test_the_scene_starts_are_read_from_the_videos_own_index(tmp_path):
    index = video_edit.keyframes(clip(tmp_path))
    assert index["ok"] is True
    assert index["frames"] == [1, 31, 61, 91]
    assert index["times"] == [1.0, 31.0, 61.0, 91.0]      # a second each, by the table
    assert index["duration_s"] == 120.0
    assert "not decoded" in index["source"]               # it says how it knows


def test_a_different_clock_is_still_converted_correctly(tmp_path):
    """A 90 kHz timescale with 90 000-tick samples is one second per frame."""
    index = video_edit.keyframes(clip(tmp_path, frames=(1, 3, 5), delta=90_000,
                                      timescale=90_000))
    assert index["times"] == [1.0, 3.0, 5.0]


def test_a_video_with_no_index_is_said_to_have_no_index(tmp_path):
    path = tmp_path / "plain.mp4"
    path.write_bytes(box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2")
                     + box(b"moov", mvhd()) + box(b"mdat", b"\x00" * 8))
    index = video_edit.keyframes(path)
    assert index["ok"] is False and index["count"] == 0


def test_the_plan_names_what_is_missing_and_how_to_get_it(tmp_path, monkeypatch):
    monkeypatch.setattr(video_edit, "_binary", lambda path: "")
    plan = video_edit.plan(clip(tmp_path))
    assert plan["ffmpeg"] is False
    assert plan["cuts"] and plan["cuts"][0] == {"at_s": 1.0, "frame": 1}
    refused = plan["scene_detect_refused"]
    assert "ffmpeg" in refused and "getffmpeg.bat" in refused
    assert "-f null -" in refused                          # the command, not a shrug


# --------------------------------------------------------------------------- #
# cutting
# --------------------------------------------------------------------------- #
def test_a_cut_copies_the_stream_and_never_re_encodes(tmp_path):
    command = video_edit.trim_command(tmp_path / "in.mp4", 90, 135, tmp_path / "out.mp4")
    assert "-c" in command and command[command.index("-c") + 1] == "copy"
    assert "00:01:30.000" in command and "00:02:15.000" in command
    assert "-c:v" not in command and "libx264" not in " ".join(command)


def test_a_cut_is_only_a_plan_until_it_is_asked_for(tmp_path):
    source = clip(tmp_path)
    out = video_edit.trim(source, 30, 60, tmp_path / "cut.mp4")
    assert out["ran"] is False and out["ok"] is True
    assert "nothing is re-encoded" in out["note"]
    assert not (tmp_path / "cut.mp4").exists()             # nothing was written


def test_an_impossible_cut_is_refused_before_anything_runs(tmp_path):
    source = clip(tmp_path)
    with pytest.raises(video_edit.VideoError, match="end must come after the start"):
        video_edit.trim(source, 60, 30, tmp_path / "x.mp4")
    with pytest.raises(video_edit.VideoError, match="no file at"):
        video_edit.trim(tmp_path / "missing.mp4", 0, 10, tmp_path / "x.mp4")


def test_running_a_cut_without_ffmpeg_says_how_to_get_it(tmp_path, monkeypatch):
    monkeypatch.setattr(video_edit, "_binary", lambda path: "")
    with pytest.raises(video_edit.VideoError, match="getffmpeg|winget"):
        video_edit.trim(clip(tmp_path), 0, 10, tmp_path / "out.mp4", run=True)


# --------------------------------------------------------------------------- #
# getting ffmpeg, without an installer or an admin prompt
# --------------------------------------------------------------------------- #
def test_the_downloader_finds_what_is_here_first_and_downloads_nothing(tmp_path):
    fake = tmp_path / "ffmpeg.exe"
    fake.write_bytes(b"MZ fake")
    called = {"download": False}

    def never(url):
        called["download"] = True
        raise AssertionError("it must not download when ffmpeg is already here")

    import brain.agent.video_edit as ve
    monkey = pytest.MonkeyPatch()
    monkey.setattr(getffmpeg, "_download", never)
    monkey.setattr(getffmpeg, "prove", lambda path: {"ok": bool(path), "path": path,
                                                     "version": "ffmpeg version test"})
    monkey.setattr(getffmpeg, "find_binary", lambda name="ffmpeg": str(fake))
    try:
        out = getffmpeg.install(dest=tmp_path)
    finally:
        monkey.undo()
    assert out["installed"] is False and called["download"] is False


def test_only_the_two_programs_are_unpacked_and_nothing_else(tmp_path):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("ffmpeg-build/bin/ffmpeg.exe", b"MZ ffmpeg")
        archive.writestr("ffmpeg-build/bin/ffprobe.exe", b"MZ ffprobe")
        archive.writestr("ffmpeg-build/README.txt", "not ours to install")
        archive.writestr("ffmpeg-build/bin/evil.dll", b"MZ")
    monkey = pytest.MonkeyPatch()
    monkey.setattr(getffmpeg, "prove", lambda path: {"ok": True, "path": path,
                                                     "version": "ffmpeg version test"})
    monkey.setattr(getffmpeg, "find_binary", lambda name="ffmpeg": "")
    try:
        out = getffmpeg.install(data=buffer.getvalue(), dest=tmp_path / "tools")
    finally:
        monkey.undo()
    written = sorted(p.name for p in (tmp_path / "tools").iterdir())
    assert written == ["ffmpeg.exe", "ffprobe.exe"]


def test_a_real_download_is_not_skipped_because_a_copy_is_already_here(tmp_path,
                                                                     monkeypatch):
    """The second half of `install` unpacks a payload the caller already has.

    A pre-check that returns "already here" would silently do nothing, which is
    exactly the bug this test was written for.
    """
    import io
    import zipfile

    exist = tmp_path / "ffmpeg.exe"
    exist.write_bytes(b"MZ")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("bin/ffmpeg.exe", b"MZ ffmpeg")
    monkey = pytest.MonkeyPatch()
    monkey.setattr(getffmpeg, "find_binary", lambda name="ffmpeg": str(exist))
    monkey.setattr(getffmpeg, "prove", lambda path: {"ok": True, "path": path,
                                                     "version": "ffmpeg version test"})
    try:
        out = getffmpeg.install(data=buffer.getvalue(), dest=tmp_path / "tools")
    finally:
        monkey.undo()
    assert out["installed"] is True
    assert (tmp_path / "tools" / "ffmpeg.exe").is_file()


def test_a_download_that_is_not_a_zip_is_named(tmp_path):
    with pytest.raises(RuntimeError, match="not a zip"):
        getffmpeg.install(data=b"<html>blocked by the proxy</html>", dest=tmp_path)


def test_a_zip_without_ffmpeg_says_the_link_moved(tmp_path):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "nothing useful")
    with pytest.raises(RuntimeError, match="link has probably changed"):
        getffmpeg.install(data=buffer.getvalue(), dest=tmp_path / "tools")


def test_check_says_what_is_missing_and_the_next_step(capsys):
    code = getffmpeg.main(["--check"])
    printed = capsys.readouterr().out
    assert "ffmpeg :" in printed and "ffprobe:" in printed
    if code != 0:
        assert "getffmpeg.bat" in printed


# --------------------------------------------------------------------------- #
# the command he types
# --------------------------------------------------------------------------- #
def test_brain_media_prints_the_cuts_and_the_cut_command(tmp_path, capsys):
    from brain.cli.main import UI, build_parser, cmd_media

    source = clip(tmp_path)
    args = build_parser().parse_args(["media", "scenes", str(source)])
    assert cmd_media(args, UI()) == 0
    printed = capsys.readouterr().out
    assert "4 scene start(s)" in printed and "31.00 s" in printed
    assert "--trim 90,135 --run" in printed            # the next command, pasteable


def test_brain_media_accepts_the_file_first_too(tmp_path, capsys):
    from brain.cli.main import UI, build_parser, cmd_media

    args = build_parser().parse_args(["media", str(clip(tmp_path))])
    assert cmd_media(args, UI()) == 0
    assert "scene start" in capsys.readouterr().out


def test_brain_media_trim_without_run_writes_nothing(tmp_path, capsys):
    from brain.cli.main import UI, build_parser, cmd_media

    source = clip(tmp_path)
    args = build_parser().parse_args(["media", str(source), "--trim", "30,60"])
    assert cmd_media(args, UI()) == 0
    printed = capsys.readouterr().out
    assert "-c copy" in printed
    assert not (tmp_path / "clip-cut.mp4").exists()


def test_a_trim_that_is_not_two_numbers_is_refused(tmp_path, capsys):
    from brain.cli.main import UI, build_parser, cmd_media

    args = build_parser().parse_args(["media", str(clip(tmp_path)), "--trim", "later"])
    assert cmd_media(args, UI()) == 2
    assert "two numbers" in capsys.readouterr().err
