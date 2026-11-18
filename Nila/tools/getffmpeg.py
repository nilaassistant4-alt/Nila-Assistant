#!/usr/bin/env python3
"""Get ffmpeg (one file, from one place) and prove it works - or say why not.

Why this exists: two rows on the owner's list said "needs ffmpeg", and telling a
non-technical person to `winget install` something is the same as saying no. So
there is one double-click, `getffmpeg.bat`, which:

1. **looks first** - `tools\\ffmpeg.exe`, then the PATH. If it is already there,
   it says so and stops; nothing is downloaded twice;
2. **downloads one archive** from `github.com/BtbN/FFmpeg-Builds/releases` (the
   standard Windows build, no installer, no admin rights), verifies it is really
   a zip, and unpacks **only `ffmpeg.exe` and `ffprobe.exe`** into `tools\\`;
3. **proves it** by running `ffmpeg -version` and printing the first line.

No PATH editing, no registry, no admin prompt, nothing installed system-wide: two
files next to the program, which the .bat can delete again.

`--check` says what is present without touching the network; `--url` overrides the
download address for a mirror or a newer build.
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_URL = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
               "ffmpeg-master-latest-win64-gpl.zip")
ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
MAX_BYTES = 400 * 1024 * 1024          # a full build is ~150 MB; refuse anything wild
WANTED = ("ffmpeg.exe", "ffprobe.exe")


def find_binary(name: str = "ffmpeg") -> str:
    """Where ffmpeg is, in the order the owner would expect: ours first, then PATH."""
    local = TOOLS / f"{name}.exe"
    if local.is_file():
        return str(local)
    return shutil.which(name) or shutil.which(f"{name}.exe") or ""


def prove(binary: str) -> Dict[str, Any]:
    """Run it once. A download that does not run is not an install."""
    if not binary:
        return {"ok": False, "why": "ffmpeg is not here"}
    try:
        proc = subprocess.run([binary, "-version"], capture_output=True, text=True,
                              timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "why": f"{binary} would not run: {exc}"}
    first = (proc.stdout or proc.stderr or "").strip().splitlines()
    return {"ok": proc.returncode == 0 and bool(first), "path": binary,
            "version": first[0] if first else "",
            "why": "" if proc.returncode == 0 else "it exited with an error"}


def check() -> Dict[str, Any]:
    ffmpeg = prove(find_binary("ffmpeg"))
    ffprobe = prove(find_binary("ffprobe"))
    return {"ok": ffmpeg["ok"], "ffmpeg": ffmpeg, "ffprobe": ffprobe,
            "where": "" if ffmpeg["ok"] else "tools\\ffmpeg.exe or the PATH",
            "next": "" if ffmpeg["ok"] else "double-click getffmpeg.bat once"}


def _download(url: str) -> bytes:
    """One GET, no retries, no redirect chains to places the owner did not choose."""
    import urllib.error
    import urllib.request

    try:
        request = urllib.request.Request(url, headers={"User-Agent": "nila-ffmpeg/0.2"})
        with urllib.request.urlopen(request, timeout=120) as response:
            length = int(response.headers.get("Content-Length") or 0)
            if length and length > MAX_BYTES:
                raise RuntimeError(f"that download is {length // 1048576} MB - too big")
            data = response.read(MAX_BYTES + 1)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"could not reach the download: {exc}") from exc
    if len(data) > MAX_BYTES:
        raise RuntimeError("the download was larger than expected; stopped")
    return data


def install(url: str = DEFAULT_URL, data: Optional[bytes] = None,
            dest: Optional[Path] = None) -> Dict[str, Any]:
    """Unpack the two .exe files next to the program. Nothing else is touched."""
    dest = Path(dest or TOOLS)
    dest.mkdir(parents=True, exist_ok=True)
    # Only look when nothing was handed in: unpacking a payload the caller
    # already has is the point of the second half of this function, and a check
    # that says "it is already here" would silently skip it.
    already = prove(find_binary("ffmpeg")) if data is None else {"ok": False}
    if already["ok"]:
        return {"ok": True, "installed": False, "path": already["path"],
                "version": already.get("version", ""),
                "note": "ffmpeg is already here - nothing was downloaded"}
    payload = data if data is not None else _download(url)
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise RuntimeError("what came back is not a zip file - the download may be "
                           "blocked by a proxy or the link may have moved") from exc
    written: List[str] = []
    for member in archive.namelist():
        name = Path(member).name
        if name not in WANTED:
            continue
        target = dest / name
        with archive.open(member) as source, target.open("wb") as sink:
            shutil.copyfileobj(source, sink)
        try:
            target.chmod(0o755)
        except OSError:                          # pragma: no cover - Windows
            pass
        written.append(str(target))
    if not written:
        raise RuntimeError("the archive did not contain ffmpeg.exe - the download "
                           "link has probably changed")
    verified = prove(find_binary("ffmpeg"))
    return {"ok": verified["ok"], "installed": True, "files": written,
            "version": verified.get("version", ""), "why": verified.get("why", ""),
            "note": ("these two files live in tools\\ and nothing was installed "
                     "system-wide - delete them to undo this")}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="getffmpeg",
                                     description="Get ffmpeg for scene detection.")
    parser.add_argument("--check", action="store_true", help="say what is here, touch nothing")
    parser.add_argument("--url", default=DEFAULT_URL, help="download from here instead")
    args = parser.parse_args(argv)
    if args.check:
        state = check()
        print(f"ffmpeg : {'yes - ' + state['ffmpeg'].get('version', '') if state['ffmpeg']['ok'] else 'no'}")
        print(f"ffprobe: {'yes' if state['ffprobe']['ok'] else 'no'}")
        if not state["ok"]:
            print(f"next   : {state['next']}")
        return 0 if state["ok"] else 1
    try:
        out = install(args.url)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("  You can also install it yourself: winget install Gyan.FFmpeg",
              file=sys.stderr)
        return 2
    print(out["note"])
    if out.get("version"):
        print(out["version"])
    return 0 if out["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
