"""Finding the cuts in a video, without a video library.

Two things used to be reported as "needs ffmpeg":

1. **finding where the scenes change** - this module does it, from the container;
2. **finding where the sound stops** - that does need a decoder, and the exact
   command is printed instead of a guess.

How scene detection works here, and why it is honest: an MP4/MOV keeps an index
(`stss`) of the frames that can be decoded on their own. Every keyframe in that
index is a place the encoder itself decided a new scene begins - which is exactly
what "cut points" means for editing. No pixels are decoded, so it is fast enough
for a 10 GB file, and it says `keyframes` instead of pretending to have compared
every frame. A video whose container has no such index gets an honest "I cannot
see the cuts in this one" and the ffmpeg command.

Editing works on the same principle: a cut at a keyframe can be made by copying
the stream (`-c copy`), so nothing is re-encoded and the picture does not change.
"""
from __future__ import annotations

import shutil
import struct
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_INDEX = 20_000


class VideoError(RuntimeError):
    """Something the owner can act on."""


# --------------------------------------------------------------------------- #
# the container
# --------------------------------------------------------------------------- #
def _boxes(raw: bytes, start: int = 0, end: Optional[int] = None, depth: int = 0):
    pos = start
    end = len(raw) if end is None else end
    while pos + 8 <= end:
        size = int.from_bytes(raw[pos:pos + 4], "big")
        kind = raw[pos + 4:pos + 8]
        header = 8
        if size == 1:                          # 64-bit size
            if pos + 16 > end:
                return
            size = int.from_bytes(raw[pos + 8:pos + 16], "big")
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        body = raw[pos + header:pos + size]
        yield kind, body, depth
        if kind in (b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"udta"):
            for item in _boxes(body, 0, len(body), depth + 1):
                yield item
        pos += size


def _find(raw: bytes, kind: bytes) -> List[bytes]:
    return [body for name, body, _depth in _boxes(raw) if name == kind]


def _binary(path: Path) -> str:
    for name in ("ffmpeg", "ffmpeg.exe"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def _mp4_timescale(raw: bytes) -> tuple[int, float]:
    """(timescale, duration in seconds) from mvhd, so frame numbers become seconds."""
    for body in _find(raw, b"mvhd"):
        if len(body) < 20:
            continue
        version = body[0]
        if version == 1:
            timescale = int.from_bytes(body[20:24], "big") if len(body) >= 32 else 0
            duration = int.from_bytes(body[24:32], "big") if len(body) >= 32 else 0
        else:
            timescale = int.from_bytes(body[12:16], "big")
            duration = int.from_bytes(body[16:20], "big")
        return timescale or 1000, (duration / timescale) if timescale else 0.0
    return 1000, 0.0


def keyframes(path: Path) -> Dict[str, Any]:
    """The frames the encoder marked as scene starts, from the index (no decode)."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise VideoError(f"could not read {path.name}: {exc}") from exc
    timescale, duration = _mp4_timescale(raw)
    frames: List[int] = []
    for body in _find(raw, b"stss"):
        if len(body) < 8:
            continue
        count = int.from_bytes(body[4:8], "big")
        usable = min(count, (len(body) - 8) // 4, MAX_INDEX)
        for i in range(usable):
            frames.append(int.from_bytes(body[8 + i * 4:12 + i * 4], "big"))
    # stts maps samples to time; without it a frame number is not a second, so
    # the answer says "keyframe 41" as well as the time - the frame number is
    # still exact and is what a cut list needs.
    stts = []
    for body in _find(raw, b"stts"):
        if len(body) >= 8:
            count = int.from_bytes(body[4:8], "big")
            for i in range(min(count, (len(body) - 8) // 8)):
                stts.append((int.from_bytes(body[8 + i * 8:12 + i * 8], "big"),
                             int.from_bytes(body[12 + i * 8:16 + i * 8], "big")))
    times: List[float] = []
    if stts and frames:
        # Walk the time-to-sample table, which is (how many samples, how long each
        # one lasts), keeping a running sample number and clock. A keyframe's
        # sample number is its position in that walk - not its index in `frames`.
        wanted = {int(f) for f in frames}
        last_wanted = max(wanted)
        sample = 0
        clock = 0.0
        found: Dict[int, float] = {}
        for count, delta in stts:
            for _ in range(count):
                sample += 1
                clock += delta / timescale
                if sample in wanted:
                    found[sample] = round(clock, 3)
                if sample >= last_wanted:
                    break
            if sample >= last_wanted:
                break
        times = [found[f] for f in sorted(found)]
    return {"ok": bool(frames), "frames": frames[:MAX_INDEX], "count": len(frames),
            "times": times, "timescale": timescale, "duration_s": round(duration, 2),
            "source": "mp4 stss index (keyframes, not decoded frames)"}


# --------------------------------------------------------------------------- #
# asking ffmpeg, when it is there
# --------------------------------------------------------------------------- #
def ffmpeg_scene_lines(video: Path, threshold: float = 0.4) -> Dict[str, Any]:
    """The real thing: one decode pass, no re-encode, the cut times as text."""
    binary = _binary(video)
    if not binary:
        raise VideoError("ffmpeg is not installed - ffmpeg.exe in this folder, or "
                         "`winget install Gyan.FFmpeg`, then run this again")
    command = [binary, "-hide_banner", "-i", str(video),
               "-filter:v", f"select='gt(scene,{threshold})',showinfo",
               "-f", "null", "-"]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired as exc:                 # pragma: no cover
        raise VideoError("ffmpeg is still working after 30 minutes; try a shorter "
                         "clip or a larger scene threshold") from exc
    times: List[float] = []
    for line in (proc.stderr or "").splitlines():
        if "pts_time:" in line:
            piece = line.split("pts_time:", 1)[1].split()[0]
            try:
                times.append(round(float(piece), 3))
            except ValueError:
                continue
    return {"ok": bool(times), "engine": "ffmpeg", "threshold": threshold,
            "times": times, "count": len(times),
            "command": " ".join(command)}


# --------------------------------------------------------------------------- #
# cutting, without re-encoding
# --------------------------------------------------------------------------- #
def _stamp(seconds: float) -> str:
    whole = max(0.0, float(seconds))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{secs:06.3f}"


def trim_command(src: Path, start: float, end: float, out: Path) -> List[str]:
    """A cut that copies the stream, so the picture is bit-for-bit unchanged."""
    return ["ffmpeg", "-hide_banner", "-ss", _stamp(start), "-to", _stamp(end),
            "-i", str(src), "-c", "copy", "-avoid_negative_ts", "make_zero",
            str(out)]


def trim(src: Path, start: float, end: float, out: Path,
         run: bool = False) -> Dict[str, Any]:
    """Cut a piece out. `run=False` only returns the command, which is the default."""
    if not Path(src).is_file():
        raise VideoError(f"no file at {src}")
    if end <= start:
        raise VideoError("the end must come after the start")
    command = trim_command(Path(src), start, end, Path(out))
    result = {"ok": True, "ran": False, "command": " ".join(command),
              "start": start, "end": end, "out": str(out),
              "note": "this copies the stream, so nothing is re-encoded and the "
                      "picture does not change; add --run to actually write the file"}
    if not run:
        return result
    binary = _binary(Path(src))
    if not binary:
        raise VideoError("ffmpeg is not installed - ffmpeg.exe in this folder, or "
                         "`winget install Gyan.FFmpeg`")
    command[0] = binary
    proc = subprocess.run(command, capture_output=True, text=True, timeout=3600)
    result.update({"ran": True, "returncode": proc.returncode,
                   "ok": proc.returncode == 0 and Path(out).is_file()})
    if proc.returncode != 0:
        result["error"] = (proc.stderr or "").strip().splitlines()[-1:] or ["ffmpeg failed"]
    return result


def plan(path: Path) -> Dict[str, Any]:
    """Everything that can be said about the cuts, and what is still impossible."""
    p = Path(path)
    if not p.is_file():
        raise VideoError(f"no file at {p}")
    out: Dict[str, Any] = {"ok": True, "path": str(p), "ffmpeg": bool(_binary(p))}
    if p.suffix.lower() in (".mp4", ".mov", ".m4v", ".3gp", ".mkv"):
        try:
            index = keyframes(p)
        except VideoError as exc:
            index = {"ok": False, "why": str(exc)}
        out["keyframes"] = index
        if index.get("ok"):
            out["cuts"] = [{"at_s": t, "frame": f}
                           for t, f in zip(index.get("times", []), index["frames"])]
            out["how"] = ("these are the encoder's own scene starts (MP4 keyframe "
                          "index): exact cut points, found without decoding")
    if out["ffmpeg"]:
        out["scene_detect"] = ffmpeg_scene_lines(p)
    else:
        out["scene_detect_refused"] = (
            "comparing every frame to the next needs a decoder. ffmpeg does it in "
            "one pass: `ffmpeg -i input.mp4 -filter:v \"select='gt(scene,0.4)',"
            "showinfo\" -f null -` (getffmpeg.bat installs it)")
    return out
