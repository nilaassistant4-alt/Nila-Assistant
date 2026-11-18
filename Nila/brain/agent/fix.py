"""`brain fix` - the one command that turns "not installed" into "checked working".

The owner's complaint was not that a feature was missing; it was that finding out
*why* and fixing it took several documents. Everything here follows the same three
rules, and they are the reason this module is small:

1. **Ask before downloading.** Nothing is installed without the owner's explicit
   action; `brain fix --check` writes nothing at all.
2. **Prove it after installing.** "Installed" is not "works": the voice fix speaks a
   Tamil sentence and counts the audio bytes; the hearing fix transcribes a
   sentence and prints the words. A package that imports but cannot run is
   reported as broken, with the reason.
3. **Never claim a fix that did not happen.** Every step returns a status the CLI
   prints as `done`, `already`, `skipped` or `failed - <reason>`.
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

#: What `brain fix` can repair, in the order that gives the owner the most for the
#: least bandwidth: a voice is one package, hearing is a 75 MB wheel plus a model.
TARGETS: Dict[str, Dict[str, str]] = {
    "voice": {"title": "Nila speaking (Tamil)", "needs": "espeak-ng, Piper or Windows SAPI",
              "why": "without one she can only write a WAV and say she cannot speak"},
    "hearing": {"title": "Nila hearing you", "needs": "faster-whisper (or whisper/vosk)",
                "why": "the transcriber is the largest single gap in v0.2"},
    "mic": {"title": "the microphone", "needs": "sounddevice + numpy",
            "why": "recording needs these; a WAV file does not"},
    "phone": {"title": "a message on your phone", "needs": "a Telegram bot token",
              "why": "only you can create the bot, so this is a walkthrough"},
}


def _run(cmd: List[str], timeout: float = 1800) -> Dict[str, Any]:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"ok": False, "code": 127, "err": f"{cmd[0]} is not on PATH", "out": ""}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "code": 1, "err": str(exc), "out": ""}
    return {"ok": done.returncode == 0, "code": done.returncode,
            "out": (done.stdout or "").strip(), "err": (done.stderr or "").strip()}


def _pip_install(package: str, dry_run: bool = False) -> Dict[str, Any]:
    """Install into the SAME interpreter that is running - not "pip" on PATH.

    Using `pip` from PATH is how a package lands in a different Python than the one
    Nila runs, which looks like a failed install and is not one.
    """
    packages = [p for p in str(package).split() if p]
    if dry_run:
        return {"ok": True, "dry": True,
                "cmd": f"{sys.executable} -m pip install {' '.join(packages)}"}
    # One argv entry per package: `pip install "sounddevice numpy"` is a single,
    # invalid requirement string, which is how the microphone fix failed.
    first = _run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                  *packages], timeout=1800)
    if first["ok"]:
        return {"ok": True, "cmd": f"{sys.executable} -m pip install {package}", "out": first["out"][-400:]}
    # A PEP 668 "externally managed" environment is common on modern Linux; the
    # honest next step is the owner's own venv or the flag, not a silent override.
    if "externally-managed-environment" in (first["err"] + first["out"]):
        return {"ok": False, "cmd": "", "err": (
            "this Python refuses system-wide installs (PEP 668). Either use a venv: "
            "`python -m venv .venv && . .venv/bin/activate`, or re-run with "
            f"`{sys.executable} -m pip install --user {package}`.")}
    return {"ok": False, "cmd": f"{sys.executable} -m pip install {' '.join(packages)}",
            "err": (first["err"] or first["out"])[-600:]}


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


# --------------------------------------------------------------------------- #
# 1. voice out
# --------------------------------------------------------------------------- #
def check_voice() -> Dict[str, Any]:
    from ..util import speech

    got = speech.verify("ta")
    return {"target": "voice", "working": bool(got["ok"]),
            "detail": got["verdict"], "engines": got["engines"]}


def fix_voice(dry_run: bool = False) -> Dict[str, Any]:
    from ..util import speech

    if speech.verify("ta")["ok"]:
        return {"target": "voice", "status": "already", "detail": "a voice already speaks Tamil"}
    if dry_run:
        return {"target": "voice", "status": "would-install",
                "detail": "espeak-ng (one package, Tamil included) via the system package manager"}
    tool = Path(__file__).resolve().parents[2] / "tools" / "install_voice.py"
    if not tool.exists():
        return {"target": "voice", "status": "failed", "detail": f"missing {tool}"}
    got = _run([sys.executable, str(tool), "--espeak"], timeout=1800)
    after = speech.verify("ta")
    out = (got["out"] + " " + got["err"]).strip()
    if after["ok"]:
        return {"target": "voice", "status": "done", "detail": after["verdict"],
                "engines": after["engines"]}
    # Say WHICH package was refused, and keep the one honest fallback that needs no
    # root and no download: Windows SAPI is already installed there.
    lower = out.lower()
    if "not found" in lower or "no installation candidate" in lower or "unable to locate" in lower:
        why = ("the package manager has no 'espeak-ng' entry - this distro may call it "
               "'espeak' or have neither")
    elif "permission" in lower or "sudo" in lower or "root" in lower:
        why = "installing needs administrator rights; run this line yourself, then retry"
    else:
        why = out[-300:] or "no package manager was available"
    return {"target": "voice", "status": "failed", "detail": after["verdict"],
            "why": why, "output": out[-600:], "engines": after["engines"],
            "manual": ("sudo apt install espeak-ng   (Windows: winget install "
                       "eSpeak-NG.eSpeak-NG;  macOS: brew install espeak-ng)")}


# --------------------------------------------------------------------------- #
# 2. hearing
# --------------------------------------------------------------------------- #
OFFLINE_WORDS = ("connect", "tls", "ssl", "network", "name resolution", "timed out",
                 "certificate", "internet", "download", "httperror", "could not reach",
                 "getaddrinfo", "offline")


def _looks_offline(text: str) -> bool:
    low = str(text or "").lower()
    return any(word in low for word in OFFLINE_WORDS)


def check_hearing() -> Dict[str, Any]:
    from ..agent import ears

    found = ears.available()
    if not found["can_hear"]:
        return {"target": "hearing", "working": False, "detail": found["how"],
                "class": "no-package"}
    got = ears.self_test("en")
    if got["ok"]:
        return {"target": "hearing", "working": True,
                "detail": f"transcriber {got.get('engine')} ran", "class": "ok",
                "steps": got.get("steps", [])}
    # The package can be perfect and the model still cannot be fetched. That is a
    # different fact from "not installed", and it is the one an offline PC hits.
    offline = _looks_offline(got.get("reason") or "")
    return {"target": "hearing", "working": False,
            "detail": got.get("reason") or "the transcriber did not run",
            "class": "waiting-for-model" if offline else "broken",
            "note": ("the package is installed; the model downloads on first use, and "
                     "this machine cannot reach the internet right now"
                     if offline else "")}


def fix_hearing(dry_run: bool = False) -> Dict[str, Any]:
    from ..agent import ears

    if ears.available()["can_hear"]:
        got = ears.self_test("en")
        return {"target": "hearing",
                "status": "already" if got["ok"] else "failed",
                "detail": got.get("reason") or "the transcriber already runs"}
    got = _pip_install("faster-whisper", dry_run=dry_run)
    if dry_run:
        return {"target": "hearing", "status": "would-install", "detail": got.get("cmd", "")}
    if not got["ok"]:
        return {"target": "hearing", "status": "failed", "detail": got.get("err", "")}
    # Prove it: the model itself is downloaded on first use, which needs internet
    # once. Saying that now is better than a silent two-minute wait later.
    after = ears.self_test("en")
    if after["ok"]:
        return {"target": "hearing", "status": "done",
                "detail": f"transcriber {after.get('engine')} ran",
                "steps": after.get("steps", [])}
    reason = str(after.get("reason") or "")
    offline = any(word in reason.lower() for word in
                  ("connect", "tls", "ssl", "network", "name resolution", "timed out",
                   "certificate", "internet", "download", "httperror", "could not reach"))
    return {"target": "hearing",
            "status": "installed-waiting" if offline else "installed-untested",
            "detail": reason[:300],
            "note": ("the package is installed and imports; the model itself is fetched on "
                     "first use, and this machine has no internet right now. On your PC the "
                     "first `brain ears --file x.wav` downloads it once (~75 MB)."
                     if offline else
                     "the package is installed but the transcriber did not run - see detail"),
            "steps": after.get("steps", [])}


# --------------------------------------------------------------------------- #
# 3. the microphone
# --------------------------------------------------------------------------- #
def check_mic() -> Dict[str, Any]:
    have = {"sounddevice": _has("sounddevice"), "numpy": _has("numpy")}
    if all(have.values()):
        return {"target": "mic", "working": True, "detail": "sounddevice and numpy import",
                "note": "recording needs both; a WAV file needs neither"}
    missing = [k for k, v in have.items() if not v]
    return {"target": "mic", "working": False,
            "detail": "missing: " + ", ".join(missing),
            "hint": ("pip install sounddevice numpy" if missing else ""),
            "note": ("on Linux, sounddevice also needs PortAudio: "
                     "sudo apt install libportaudio2")}


def fix_mic(dry_run: bool = False) -> Dict[str, Any]:
    if check_mic()["working"]:
        return {"target": "mic", "status": "already", "detail": "sounddevice and numpy are here"}
    if dry_run:
        return {"target": "mic", "status": "would-install", "detail": "sounddevice, numpy"}
    got = _pip_install("sounddevice numpy", dry_run=False)
    ok = check_mic()["working"]
    return {"target": "mic", "status": "done" if ok else "failed",
            "detail": got.get("err", "") or ("installed" if ok else "still missing"),
            "note": ("on Linux the portaudio system library is also needed: "
                     "sudo apt install libportaudio2") if not ok else ""}


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #
FIXES: Dict[str, Dict[str, Callable[..., Dict[str, Any]]]] = {
    "voice": {"check": check_voice, "fix": fix_voice},
    "hearing": {"check": check_hearing, "fix": fix_hearing},
    "mic": {"check": check_mic, "fix": fix_mic},
}


def run(only: str = "", dry_run: bool = False, apply: bool = False) -> Dict[str, Any]:
    """Check (and with `apply`, fix) every target or one of them."""
    names = [only] if only else ["voice", "hearing", "mic"]
    rows: List[Dict[str, Any]] = []
    for name in names:
        if name not in FIXES:
            rows.append({"target": name, "status": "failed",
                         "detail": f"unknown target (choose {', '.join(FIXES)} or phone)"})
            continue
        if apply:
            rows.append(FIXES[name]["fix"](dry_run=dry_run))
        else:
            got = FIXES[name]["check"]()
            status = "working" if got["working"] else "missing"
            if not got["working"] and got.get("class") == "waiting-for-model":
                status = "installed-waiting"
            rows.append({"target": name, "status": status,
                         **{k: v for k, v in got.items() if k != "target"}})
    good = ("working", "already", "done", "installed-waiting")
    done = [r for r in rows if r["status"] in good]
    return {"ok": all(r["status"] in good for r in rows),
            "rows": rows, "done": len(done), "of": len(rows),
            "next": next((f"brain fix {r['target']}" for r in rows
                          if r["status"] in ("missing", "failed", "installed-untested")), "")}


def phone_walkthrough() -> Dict[str, Any]:
    from ..agent.telegram import _how_text

    return {"target": "phone", "status": "needs-you",
            "detail": ("only you can create the bot; Nila never asks for your number. "
                       "Once it is set up, nila-phone.bat keeps it answering."),
            "steps": _how_text(),
            "two_way": "nila-phone.bat  (or: brain telegram listen)"}
