#!/usr/bin/env python3
"""Install a Tamil voice for Nila, and prove it speaks before saying "done".

Three ways to speak, checked in the order that costs the owner the least:

1. **Windows SAPI** - already there on Windows. If a Tamil voice is installed in
   Windows it is used; otherwise Windows is told to add one (Settings > Time &
   language > Speech) rather than this script pretending it can.
2. **espeak-ng** - one package, ships Tamil. This script can install it with
   winget / apt / dnf / pacman, but only after asking, and never silently.
3. **Piper** - the natural-sounding one. Needs a program AND a voice file; the
   voice file is a download from Hugging Face, so it is only attempted when the
   owner says `--piper`, and the URL is printed first.

Whatever it installs, it then runs the real speak path on a Tamil sentence and
reports the number of bytes of audio produced. "Installed" is not "works".

    python tools/install_voice.py --check
    python tools/install_voice.py --espeak         # install espeak-ng + Tamil
    python tools/install_voice.py --piper          # the natural voice (bigger)
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SAMPLE_TA = "வணக்கம், நான் நிலா."
SAMPLE_EN = "Hello, I am Nila."
PIPER_VOICE_DIR = Path.home() / ".nila" / "voices"
PIPER_TAMIL = "ta_IN-voice-medium"
PIPER_HINT = ("https://huggingface.co/rhasspy/piper-voices/tree/main/ta/ta_IN "
              "(download the .onnx and the .onnx.json into ~/.nila/voices)")


def _run(cmd: List[str], timeout: float = 900) -> Dict[str, Any]:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"ok": False, "code": 127, "out": "", "err": f"{cmd[0]} is not on PATH"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "code": 1, "out": "", "err": str(exc)}
    return {"ok": done.returncode == 0, "code": done.returncode,
            "out": (done.stdout or "").strip(), "err": (done.stderr or "").strip()}


def _sudo() -> List[str]:
    """Prefix for package commands: never run `sudo` when already root."""
    if platform.system().lower().startswith("win"):
        return []
    return [] if getattr(__import__("os"), "geteuid", lambda: 1)() == 0 else ["sudo"]


def _install_espeak(dry_run: bool = False) -> int:
    system = platform.system().lower()
    if shutil.which("espeak-ng") or shutil.which("espeak"):
        print("espeak-ng  already installed")
        return 0
    if system.startswith("win"):
        winget = shutil.which("winget")
        if not winget:
            print("espeak-ng  needs winget, which is not here. Install 'eSpeak NG' from "
                  "https://github.com/espeak-ng/espeak-ng/releases and run this again.")
            return 3
        cmd = [winget, "install", "--id", "eSpeak-NG.eSpeak-NG", "-e",
               "--accept-source-agreements", "--accept-package-agreements"]
    elif system == "darwin":
        if not shutil.which("brew"):
            print("espeak-ng  needs Homebrew: brew install espeak-ng")
            return 3
        cmd = ["brew", "install", "espeak-ng"]
    else:
        if shutil.which("apt-get"):
            cmd = _sudo() + ["apt-get", "install", "-y", "espeak-ng"]
        elif shutil.which("dnf"):
            cmd = _sudo() + ["dnf", "install", "-y", "espeak-ng"]
        elif shutil.which("pacman"):
            cmd = _sudo() + ["pacman", "-S", "--noconfirm", "espeak-ng"]
        else:
            print("espeak-ng  no package manager found (apt/dnf/pacman). Install "
                  "espeak-ng by hand, then run `brain speak --engines`.")
            return 3
    print("espeak-ng  would run: " + " ".join(cmd) if dry_run else
          "espeak-ng  installing: " + " ".join(cmd))
    if dry_run:
        return 0
    got = _run(cmd)
    print(f"           exit {got['code']}" + (f" - {got['err'][:200]}" if got["err"] else ""))
    if not (shutil.which("espeak-ng") or shutil.which("espeak")):
        print("           it is still not on PATH. Open a NEW window (PATH changes "
              "only apply to new windows) and run this again.")
        return 4
    return 0


def _install_piper(dry_run: bool = False) -> int:
    print(f"piper      voice files go in {PIPER_VOICE_DIR}")
    print(f"           download from {PIPER_HINT}")
    if not shutil.which("piper") and not shutil.which("piper.exe"):
        print("piper      the program is not installed; `pip install piper-tts` "
              "(this script will not pip-install into an unknown environment)")
    if dry_run:
        return 0
    found = sorted(PIPER_VOICE_DIR.glob("*.onnx")) if PIPER_VOICE_DIR.is_dir() else []
    if not found:
        print(f"piper      no .onnx voice in {PIPER_VOICE_DIR} yet - nothing to test")
        return 3
    print(f"piper      found {found[0].name}")
    return 0


def check() -> Dict[str, Any]:
    from brain.util import speech

    engines = speech.engines()
    result = speech.verify("ta")
    return {"installed": engines, "verified": result,
            "sapi_voices": speech.sapi_voices()[:8] if engines.get("sapi") else [],
            "hint": speech.piper_install_help()}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Install and verify a Tamil voice for Nila.")
    ap.add_argument("--check", action="store_true", help="what works right now")
    ap.add_argument("--espeak", action="store_true", help="install espeak-ng (Tamil included)")
    ap.add_argument("--piper", action="store_true", help="set up the natural voice")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if args.check or not (args.espeak or args.piper):
        info = check()
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return 0 if info["verified"]["ok"] else 1

    code = 0
    if args.espeak:
        code = max(code, _install_espeak(args.dry_run))
    if args.piper:
        code = max(code, _install_piper(args.dry_run))
    if args.dry_run:
        return code

    # The only part that makes this a promise kept: say the sentence and count bytes.
    from brain.util import speech

    print("\nchecking that it really speaks Tamil…")
    got = speech.verify("ta")
    for row in got["engines"]:
        state = ("spoke, " + str(row["bytes"]) + " bytes") if row["spoken"] else (
            "not installed" if not row.get("installed") else (row["reason"] or "failed"))
        print(f"  {row['engine']:<7} {state}")
    print("  " + got["verdict"])
    if not got["ok"]:
        print("\nNothing could produce Tamil audio yet. On Windows the built-in SAPI "
              "voice is the cheapest: Settings > Time & language > Speech > Add voices.")
        print("Read the per-engine reason above; it names the exact missing piece.")
        return code or 5
    return code


if __name__ == "__main__":
    sys.exit(main())
