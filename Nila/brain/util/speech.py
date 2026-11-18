"""Voice: Nila speaking. SAPI on Windows, Piper anywhere, and honesty about both.

The owner asked for three things, and this module only does the first two:

1. **Speak Tamil.** Piper with a Tamil voice is the offline answer; Windows SAPI
   is the built-in one.
2. **Speak it out loud**, or hand back a WAV file when no speech engine exists.
3. **Never pretend.** A machine with no engine must say "no voice engine is
   installed" - not silently do nothing, and not claim it spoke.

Nothing here downloads a model. A Piper voice is a file the owner places, and the
list is read from disk. WAV output is always possible, because writing a file
needs no engine - but a file is not speech, so the two are reported separately.

The WAV that is written when no engine exists is **marked**: it is a short,
honest tone rather than a fake voice, and the result says `spoken: false`.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .audio import SAMPLE_RATE, write_wav

#: Where a Piper voice is looked for, in order. A voice is a pair of files
#: (``.onnx`` + ``.onnx.json``); the first one found wins.
VOICE_DIRS = (
    Path.home() / ".nila" / "voices",
    Path.home() / ".local" / "share" / "piper",
    Path("voices"),
)

#: Tamil voices published for Piper. Names only - the file is the owner's to add.
KNOWN_TAMIL_VOICES = ("ta_IN-voice-medium", "ta_IN-anu-medium")


def _is_windows() -> bool:
    return platform.system().lower().startswith("win")


def find_piper() -> Optional[str]:
    """The Piper executable, if the owner installed one."""
    for name in ("piper", "piper.exe"):
        path = shutil.which(name)
        if path:
            return path
    return None


def find_espeak() -> Optional[str]:
    """espeak-ng, if it is installed.

    This is the third engine and the one that made "Nila speaks Tamil" untrue on
    Linux for a long time: SAPI is Windows-only and Piper needs BOTH a program and
    a voice file, so a machine with neither could only write a tone. espeak-ng is
    one apt/winget package, ships a Tamil voice, and - the point - its version can
    be checked, so "it is installed" is a fact rather than a hope.
    """
    for name in ("espeak-ng", "espeak", "espeak-ng.exe", "espeak.exe"):
        path = shutil.which(name)
        if path:
            return path
    return None


def find_voice(directory: Optional[Path] = None) -> Optional[Path]:
    """The first Piper `.onnx` voice on disk."""
    dirs = [directory] if directory else list(VOICE_DIRS)
    for folder in dirs:
        try:
            if not folder or not folder.exists():
                continue
        except OSError:
            continue
        for pattern in ("*.onnx", "**/*.onnx"):
            for hit in sorted(folder.glob(pattern)):
                return hit
    return None


def engines(voice_dir: Optional[Path] = None) -> Dict[str, Any]:
    """What can actually make sound on this machine, right now.

    Reported separately because they fail differently: SAPI exists only on
    Windows, Piper needs both the program and a voice file, and `wav_only` always
    works but produces a file rather than speech.
    """
    piper = find_piper()
    voice = find_voice(voice_dir)
    sapi = _is_windows() and shutil.which("powershell") is not None
    espeak = find_espeak()
    return {
        "ok": True,
        "platform": platform.system(),
        "sapi": bool(sapi),
        "piper": piper or "",
        "piper_voice": str(voice) if voice else "",
        "espeak": espeak or "",
        "wav_only": True,
        #: the order `speak_text` will try, so a UI can show it before speaking
        "order": [n for n, on in (("piper", bool(piper and voice)), ("sapi", bool(sapi)),
                                  ("espeak", bool(espeak))) if on],
        "can_speak": bool(sapi or (piper and voice) or espeak),
        "note": ("SAPI (Windows) speaks any installed voice; Piper speaks offline "
                 "with a voice file you place; espeak-ng speaks offline out of the "
                 "box, Tamil included. With none of the three, Nila writes a WAV "
                 "and says it could not speak."),
    }


def speak_text(text: str, voice: str = "", offline: bool = True,
               voice_dir: Optional[Path] = None,
               out_wav: Optional[Path] = None,
               only: str = "") -> Dict[str, Any]:
    """Say something, or report honestly that nothing could say it.

    Returns ``{"ok", "spoken", "engine", "wav", "reason"}``. `spoken` is true
    only when sound was actually produced (or handed to the OS player).
    """
    clean = " ".join(str(text or "").split())
    if not clean:
        return {"ok": False, "spoken": False, "engine": "", "wav": "",
                "reason": "nothing to say"}
    found = engines(voice_dir)
    if only:
        # Test one engine on its own, so "which one actually made sound" is an
        # answer and not a guess. Unknown names cannot silently do nothing.
        if only not in ("piper", "sapi", "espeak"):
            return {"ok": False, "spoken": False, "engine": only, "wav": "",
                    "reason": f"unknown engine '{only}' (choose espeak, piper or sapi)"}
        found = {**found, "piper": found["piper"] if only == "piper" else "",
                 "piper_voice": found["piper_voice"] if only == "piper" else "",
                 "sapi": found["sapi"] and only == "sapi",
                 "espeak": found["espeak"] if only == "espeak" else ""}

    # ---- Piper first when a voice file exists: it is the offline Tamil path -- #
    if found["piper"] and found["piper_voice"]:
        target = Path(out_wav) if out_wav else Path(tempfile.mkdtemp()) / "nila.wav"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run(
                [found["piper"], "--model", found["piper_voice"], "--output_file", str(target)],
                input=clean.encode("utf-8"), capture_output=True, timeout=120)
            if proc.returncode == 0 and target.exists() and target.stat().st_size > 44:
                return {"ok": True, "spoken": True, "engine": "piper",
                        "wav": str(target), "reason": "",
                        "voice": found["piper_voice"]}
            return {"ok": False, "spoken": False, "engine": "piper", "wav": "",
                    "reason": (proc.stderr or b"").decode("utf-8", "ignore")[:300]
                              or "piper produced no audio"}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "spoken": False, "engine": "piper", "wav": "",
                    "reason": f"piper could not run: {exc}"}

    # ---- Windows SAPI: built in, no install --------------------------------- #
    if found["sapi"]:
        safe = clean.replace("'", "''")
        selection = f"$v.SelectVoice('{voice}');" if voice else ""
        script = ("Add-Type -AssemblyName System.Speech;"
                  "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                  f"{selection}$s.Speak('{safe}');")
        try:
            proc = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                                  capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "spoken": False, "engine": "sapi", "wav": "",
                    "reason": f"powershell could not run: {exc}"}
        if proc.returncode == 0:
            return {"ok": True, "spoken": True, "engine": "sapi", "wav": "",
                    "reason": "", "voice": voice or "(default)"}
        return {"ok": False, "spoken": False, "engine": "sapi", "wav": "",
                "reason": (proc.stderr or b"").decode("utf-8", "ignore")[:300]
                          or "SAPI failed (the voice name may not be installed)"}

    # ---- espeak-ng: one package, Tamil included, works on every platform --- #
    if found["espeak"]:
        target = Path(out_wav) if out_wav else Path(tempfile.mkdtemp()) / "nila.wav"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run(
                [found["espeak"], "-v", voice or "ta", "-s", "150", "-w", str(target), clean],
                capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "spoken": False, "engine": "espeak", "wav": "",
                    "reason": f"espeak-ng could not run: {exc}"}
        if proc.returncode == 0 and target.exists() and target.stat().st_size > 44:
            return {"ok": True, "spoken": True, "engine": "espeak",
                    "wav": str(target), "reason": "", "voice": voice or "ta"}
        detail = (proc.stderr or b"").decode("utf-8", "ignore").strip()[:300]
        if "not found" in detail or "Error" in detail:
            # A missing Tamil voice must not silently fall back to English: say
            # which voices this espeak actually has and how to add the one asked.
            return {"ok": False, "spoken": False, "engine": "espeak", "wav": "",
                    "reason": (detail or f"espeak-ng has no voice '{voice or 'ta'}'"),
                    "fix": "install the language data, or use -v en and let Nila "
                           "answer in English"}
        return {"ok": False, "spoken": False, "engine": "espeak", "wav": "",
                "reason": detail or "espeak-ng produced no audio"}

    # ---- nothing can speak: write a WAV and say so -------------------------- #
    target = Path(out_wav) if out_wav else Path(tempfile.mkdtemp()) / "nila.wav"
    try:
        write_wav(target, _placeholder_tone(), SAMPLE_RATE)
        wav = str(target)
    except OSError as exc:
        return {"ok": False, "spoken": False, "engine": "", "wav": "",
                "reason": f"no speech engine, and the WAV could not be written: {exc}"}
    return {"ok": False, "spoken": False, "engine": "", "wav": wav,
            "reason": ("no speech engine on this machine - install Piper with a "
                       "Tamil voice, or run on Windows where SAPI is built in. "
                       "A WAV placeholder was written instead; it is not speech.")}


def _placeholder_tone(seconds: float = 0.12) -> List[float]:
    """A short marker tone. Deliberately not a voice: a silent file would look
    like a bug, and a synthetic voice would look like a success."""
    import math

    n = int(SAMPLE_RATE * seconds)
    return [0.18 * math.sin(2 * math.pi * 660 * i / SAMPLE_RATE) * (1 - i / n)
            for i in range(n)]


def piper_install_help() -> str:
    return (
        "Offline voice, any of the three (first one that works is used):\n"
        "  espeak-ng   one package, Tamil included, easiest on Linux/Windows:\n"
        "              sudo apt install espeak-ng     (Windows: winget install "
        "eSpeak-NG.eSpeak-NG)\n"
        "  Piper       natural voice, needs BOTH the program and a voice file:\n"
        "              pip install piper-tts, then put ta_IN .onnx + .onnx.json in "
        "~/.nila/voices/\n"
        "  SAPI        built into Windows, nothing to install (Sinhala/Tamil voices "
        "depend on Windows)\n"
        "Then: brain speak \"வணக்கம்\"     and check with: brain speak --engines"
    )


def verify(voice: str = "ta") -> Dict[str, Any]:
    """Actually try every engine and report what produced audio.

    "It is installed" is not "it works": a Piper binary without a voice file, an
    espeak without Tamil data, or a SAPI with only an English voice all fail at the
    moment the owner expects to hear Tamil. This runs the real path with a short
    sentence and reports, per engine, whether a WAV came out and how many bytes it
    is - the same evidence a person gets by listening.
    """
    sample = "வணக்கம் நிலா" if str(voice).lower().startswith("ta") else "Hello Nila"
    have = engines()
    installed = {"espeak": bool(have["espeak"]), "piper": bool(have["piper"]),
                 "sapi": bool(have["sapi"])}
    rows: List[Dict[str, Any]] = []
    for name in ("espeak", "piper", "sapi"):
        started = _perf()
        if not installed[name]:
            rows.append({"engine": name, "installed": False, "spoken": False, "bytes": 0,
                         "reason": "not installed on this machine",
                         "ms": int((_perf() - started) * 1000)})
            continue
        try:
            got = speak_text(sample, voice=voice, out_wav=Path(tempfile.mkdtemp()) / f"{name}.wav",
                             only=name)
        except Exception as exc:                 # a broken engine is a row, not a crash
            got = {"ok": False, "spoken": False, "engine": name, "wav": "",
                   "reason": f"{type(exc).__name__}: {exc}"}
        rows.append({"engine": name, "installed": True, "spoken": bool(got.get("spoken")),
                     "bytes": (Path(got["wav"]).stat().st_size
                               if got.get("wav") and Path(got["wav"]).exists() else 0),
                     "reason": str(got.get("reason") or "")[:200],
                     "ms": int((_perf() - started) * 1000)})
    working = [r["engine"] for r in rows if r["spoken"] and r["bytes"] > 44]
    return {"ok": bool(working), "voice": voice, "sample": sample, "engines": rows,
            "working": working,
            "verdict": (f"speaks with: {', '.join(working)}" if working else
                        "no engine produced audio for this sentence - see the per-engine "
                        "reason and `brain speak --help`")}


def _perf() -> float:
    import time as _t
    return _t.perf_counter()


def sapi_voices() -> List[str]:
    """The voice names Windows reports. Empty on any other system."""
    if not _is_windows():
        return []
    script = ("Add-Type -AssemblyName System.Speech;"
              "(New-Object System.Speech.Synthesis.SpeechSynthesizer)"
              ".GetInstalledVoices() | ForEach-Object "
              "{ $_.VoiceInfo.Name + '|' + $_.VoiceInfo.Culture }")
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                              capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    out = (proc.stdout or b"").decode("utf-8", "ignore")
    return [line.strip() for line in out.splitlines() if line.strip()]
