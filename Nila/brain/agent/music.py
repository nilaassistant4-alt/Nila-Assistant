"""A music player with no player: your own folders, one command, no account.

The owner's list had "Music player" as v3. It does not need a streaming service or
an extra install - it needs three things, and this module does exactly those:

* **find** the songs he already has (mp3, m4a, wav, ogg, flac, opus, aac) in the
  folders he names, by title, artist or file name, with the tags read out of the
  file rather than downloaded;
* **play** one in whatever player Windows already has (`os.startfile`), or print
  the next N so he can pick - never a hidden background process with a playlist
  he cannot see;
* **say what is playing** by reading the file, not by guessing.

What it deliberately does not do: download music, stream from the internet, or
keep playing after he has closed the window. There is no daemon here.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".oga", ".opus", ".flac", ".wma"}
MAX_FILES = 20_000


class MusicError(RuntimeError):
    """Something the owner can act on, not a stack trace."""


# --------------------------------------------------------------------------- #
# tags, from the file itself
# --------------------------------------------------------------------------- #
def _id3_tags(raw: bytes) -> Dict[str, str]:
    """Title/artist/album from an ID3v2 header - the three fields a person reads."""
    out: Dict[str, str] = {}
    if len(raw) < 10 or raw[:3] != b"ID3":
        return out
    major = raw[3]
    body = raw[10:]
    # every frame is a 4-character id, a size and flags. ID3v2.2 used 3-char ids
    # and a plain 24-bit size; both are handled, because old Tamil MP3s are old.
    names = {b"TIT2": "title", b"TPE1": "artist", b"TALB": "album",
             b"TT2": "title", b"TP1": "artist", b"TAL": "album"}
    pos = 0
    while pos + 10 <= len(body) and len(out) < 4:
        frame = body[pos:pos + 4]
        if frame[:3] in names and major >= 3:
            size = int.from_bytes(body[pos + 4:pos + 8], "big")
            encoding = body[pos + 8]
            data = body[pos + 10:pos + 10 + size]
            key = names[frame[:3]]
            pos += 10 + size
        elif major == 2 and frame[:3] in names:
            size = int.from_bytes(body[pos + 3:pos + 6], "big")
            data = body[pos + 6:pos + 6 + size]
            key = names[frame[:3]]
            pos += 6 + size
        elif major == 2:
            size = int.from_bytes(body[pos + 3:pos + 6], "big")
            pos += 6 + size
            continue
        else:
            size = int.from_bytes(body[pos + 4:pos + 8], "big")
            pos += 10 + size
            continue
        if not data:
            continue
        encoding = data[0] if data else 0
        text = data[1:]
        try:
            if encoding == 1:
                value = text.decode("utf-16", "ignore")
            elif encoding == 2:
                value = text.decode("utf-16-be", "ignore")
            elif encoding == 3:
                value = text.decode("utf-8", "ignore")
            else:
                value = text.decode("latin-1", "ignore")
        except LookupError:                       # pragma: no cover
            value = ""
        value = value.replace("\x00", "").strip()
        if value:
            out[key] = value
    return out


def _flac_tags(raw: bytes) -> Dict[str, str]:
    """FLAC/Vorbis comments: TITLE=, ARTIST=."""
    out: Dict[str, str] = {}
    head = raw[:200_000]
    for key, field in (("TITLE", "title"), ("ARTIST", "artist"), ("ALBUM", "album")):
        match = re.search(rb"(?i)" + key.encode() + rb"=([^\x00]{1,200})", head)
        if match:
            out[field] = match.group(1).decode("utf-8", "ignore").strip()
    return out


def tags_of(path: Path) -> Dict[str, str]:
    """Whatever the file itself says about itself; empty when it says nothing."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(512 * 1024)
    except OSError:
        return {}
    found: Dict[str, str] = {}
    if path.suffix.lower() == ".flac":
        found = _flac_tags(raw)
    if not found:
        found = _id3_tags(raw)
    title = found.get("title") or ""
    if not title:
        # "01 - Artist - Song.mp3" is the commonest naming in a folder of Tamil
        # songs, so the name is mined before giving up.
        stem = path.stem
        stem = re.sub(r"^\d{1,2}[\s._-]+", "", stem)
        parts = [p.strip() for p in re.split(r"\s+-\s+", stem) if p.strip()]
        if len(parts) >= 2:
            found.setdefault("artist", parts[0])
            found["title"] = parts[-1]
        else:
            found["title"] = stem.replace("_", " ").strip()
    return found


# --------------------------------------------------------------------------- #
# the library
# --------------------------------------------------------------------------- #
def library(folders: Iterable[str | Path], limit: int = MAX_FILES) -> List[Dict[str, Any]]:
    """Every audio file under these folders, with what its tags say."""
    songs: List[Dict[str, Any]] = []
    for folder in folders:
        root = Path(folder).expanduser()
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if len(songs) >= limit:
                return songs
            if path.suffix.lower() not in AUDIO_EXT or not path.is_file():
                continue
            info = tags_of(path)
            songs.append({"path": str(path), "title": info.get("title") or path.stem,
                          "artist": info.get("artist", ""), "album": info.get("album", ""),
                          "bytes": path.stat().st_size,
                          "folder": path.parent.name})
    return songs


def find(songs: List[Dict[str, Any]], words: str) -> List[Dict[str, Any]]:
    """Match on title, artist, album or file name - all the words, any order."""
    wanted = [w for w in re.split(r"\s+", (words or "").lower().strip()) if w]
    if not wanted:
        return songs
    hits = []
    for song in songs:
        hay = " ".join([song["title"], song["artist"], song["album"],
                        Path(song["path"]).stem]).lower()
        if all(word in hay for word in wanted):
            hits.append(song)
    return hits


def play(song: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
    """Hand the file to the player this PC already has. No download, no account."""
    path = Path(song["path"])
    if not path.is_file():
        raise MusicError(f"that file is gone: {path}")
    if dry_run:
        return {"ok": True, "played": False, "path": str(path),
                "note": "dry run: nothing was opened"}
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))                    # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            opener = "xdg-open"
            import shutil
            if shutil.which(opener) is None:           # pragma: no cover
                raise MusicError("no player here: install one, or open the file "
                                 "yourself - the path is below")
            subprocess.Popen([opener, str(path)])
    except OSError as exc:
        raise MusicError(f"could not open {path.name}: {exc}") from exc
    return {"ok": True, "played": True, "path": str(path),
            "title": song.get("title", path.stem)}


def describe(song: Dict[str, Any]) -> str:
    artist = f" - {song['artist']}" if song.get("artist") else ""
    return f"{song['title']}{artist}   ({song.get('folder', '')})"


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="music",
                                     description="Play songs from your own folders.")
    parser.add_argument("words", nargs="*", help="words from the title or artist")
    parser.add_argument("--folder", action="append", default=[], metavar="PATH",
                        help="where your music is (repeatable)")
    parser.add_argument("--list", action="store_true", help="list matches, play nothing")
    parser.add_argument("--play", action="store_true", help="play the first match")
    parser.add_argument("--dry-run", action="store_true", help="show what would be played")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)

    folders = args.folder or [str(Path.home() / "Music")]
    songs = library(folders)
    if not songs:
        print("No music found in: " + ", ".join(folders), file=sys.stderr)
        print("Tell me where it is: music --folder \"D:/Songs\" --list", file=sys.stderr)
        return 2
    hits = find(songs, " ".join(args.words))
    if not hits:
        print(f"Nothing matched {' '.join(args.words)!r} in {len(songs)} songs.",
              file=sys.stderr)
        return 1
    for song in hits[: args.limit]:
        print(describe(song))
    if len(hits) > args.limit:
        print(f"  ... and {len(hits) - args.limit} more")
    if args.play and not args.list:
        out = play(hits[0], dry_run=args.dry_run)
        print(("playing: " if out["played"] else "would play: ") + out["path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
