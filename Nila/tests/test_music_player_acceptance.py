"""Acceptance tests: stage 12, gap 6 - a music player that needs nothing installed.

"Music player" was on the v3 list because it sounded like it needed a streaming
service. It does not: the owner has songs in folders, and Windows already has a
player. What was missing was a way to *find* a song by its name and hand it over.

The honest limits are part of the tests:

* tags are read from the file itself (ID3v2.3/2.4 and FLAC comments), and when a
  file has no tags the *file name* is mined - "01 - Ilaiyaraaja - Nila.mp3" is
  how a folder of Tamil songs is actually named;
* nothing is downloaded, and a file that has gone missing is refused by name;
* a dry run says what would happen and touches nothing, so "it works" can be
  proved on a PC with no speakers.

Run:  python -m pytest tests/test_music_player_acceptance.py -q
"""
from __future__ import annotations

from pathlib import Path

import pytest

from brain.agent import music


def id3(title: str, artist: str, album: str = "Songs") -> bytes:
    """A real ID3v2.3 header, the shape an MP3 ripper writes."""
    def frame(fid: bytes, text: str) -> bytes:
        data = b"\x03" + text.encode("utf-8")
        return fid + len(data).to_bytes(4, "big") + b"\x00\x00" + data

    body = frame(b"TIT2", title) + frame(b"TPE1", artist) + frame(b"TALB", album)
    size = len(body)
    syncsafe = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F,
                      (size >> 7) & 0x7F, size & 0x7F])
    return b"ID3\x03\x00\x00" + syncsafe + body + b"\x00" * 64


@pytest.fixture()
def songs(tmp_path) -> Path:
    folder = tmp_path / "Songs"
    folder.mkdir()
    (folder / "01 - Ilaiyaraaja - Nila Nila.mp3").write_bytes(id3("Nila Nila", "Ilaiyaraaja"))
    (folder / "02 - A R Rahman - Vaanam.m4a").write_bytes(id3("Vaanam", "A R Rahman"))
    (folder / "untagged.ogg").write_bytes(b"OggS" + b"\x00" * 40)
    (folder / "notes.txt").write_text("not music")
    (folder / "nested").mkdir()
    (folder / "nested" / "03 - Harris Jayaraj - Onnu Rendu.wav").write_bytes(b"RIFF" + b"\x00" * 40)
    return folder


def test_the_library_finds_audio_and_ignores_everything_else(songs):
    found = music.library([songs])
    names = {Path(song["path"]).name for song in found}
    assert len(found) == 4
    assert "notes.txt" not in names
    assert "03 - Harris Jayaraj - Onnu Rendu.wav" in names       # nested folders too


def test_tags_come_from_the_file_and_the_name_is_the_fallback(songs):
    found = {Path(s["path"]).name: s for s in music.library([songs])}
    nila = found["01 - Ilaiyaraaja - Nila Nila.mp3"]
    assert nila["title"] == "Nila Nila" and nila["artist"] == "Ilaiyaraaja"
    rahman = found["02 - A R Rahman - Vaanam.m4a"]
    assert rahman["artist"] == "A R Rahman"
    # no ID3 frame at all: the name is split, because that is how it was named
    wav = found["03 - Harris Jayaraj - Onnu Rendu.wav"]
    assert wav["title"] == "Onnu Rendu" and wav["artist"] == "Harris Jayaraj"
    assert found["untagged.ogg"]["title"] == "untagged"


def test_finding_is_by_title_artist_or_file_name_in_any_order(songs):
    found = music.library([songs])
    assert [s["title"] for s in music.find(found, "nila")] == ["Nila Nila"]
    assert [s["title"] for s in music.find(found, "rahman vaanam")] == ["Vaanam"]
    assert [s["title"] for s in music.find(found, "vanam")] == []       # no fuzzy guessing
    assert len(music.find(found, "")) == 4                              # empty = everything


def test_a_missing_file_is_refused_by_name_and_never_silently_skipped(songs):
    song = {"path": str(songs / "gone.mp3"), "title": "gone"}
    with pytest.raises(music.MusicError, match="that file is gone"):
        music.play(song)


def test_a_dry_run_says_what_it_would_do_and_opens_nothing(songs):
    found = music.library([songs])
    out = music.play(found[0], dry_run=True)
    assert out["played"] is False and out["path"].endswith(found[0]["path"].split("/")[-1])
    assert "dry run" in out["note"]


def test_an_empty_folder_says_where_to_point_it(tmp_path, capsys):
    code = music.main(["--folder", str(tmp_path / "nothing-here"), "--list"])
    err = capsys.readouterr().err
    assert code == 2
    assert "No music found" in err and "--folder" in err      # the hint is pasteable


def test_the_command_lists_and_plays_without_a_player_running(tmp_path, songs, capsys):
    from brain.cli.main import UI, build_parser, cmd_music

    home = tmp_path / "home"
    home.mkdir()
    parser = build_parser()

    # remember the folder once, then never pass it again
    args = parser.parse_args(["music", "--add-folder", str(songs)])
    args.home = str(home)
    assert cmd_music(args, UI()) == 0
    assert str(songs) in capsys.readouterr().out

    args = parser.parse_args(["music", "rahman"])
    args.home = str(home)
    assert cmd_music(args, UI()) == 0
    printed = capsys.readouterr().out
    assert "Vaanam" in printed and "Add --play" in printed

    args = parser.parse_args(["music", "rahman", "--play", "--dry-run"])
    args.home = str(home)
    assert cmd_music(args, UI()) == 0
    printed = capsys.readouterr().out
    assert "would play:" in printed and "Vaanam" not in printed.split("would play:")[0]

    args = parser.parse_args(["music", "nothing-like-this"])
    args.home = str(home)
    assert cmd_music(args, UI()) == 1
    assert "nothing matched" in capsys.readouterr().err
