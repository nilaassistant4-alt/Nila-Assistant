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
    return {
        "ok": True,
        "platform": platform.system(),
        "sapi": bool(sapi),
        "piper": piper or "",
        "piper_voice": str(voice) if voice else "",
        "wav_only": True,
        "can_speak": bool(sapi or (piper and voice)),
        "note": ("SAPI (Windows) speaks any installed voice; Piper speaks offline "
                 "with a voice file you place; without either, Nila writes a WAV "
                 "and says it could not speak."),
    }


def speak_text(text: str, voice: str = "", offline: bool = True,
               voice_dir: Optional[Path] = None,
               out_wav: Optional[Path] = None) -> Dict[str, Any]:
    """Say something, or report honestly that nothing could say it.

    Returns ``{"ok", "spoken", "engine", "wav", "reason"}``. `spoken` is true
    only when sound was actually produced (or handed to the OS player).
    """
    clean = " ".join(str(text or "").split())
    if not clean:
        return {"ok": False, "spoken": False, "engine": "", "wav": "",
                "reason": "nothing to say"}
    found = engines(voice_dir)

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
        "Offline voice (Piper):\n"
        "  1. install the program:  pip install piper-tts\n"
        "  2. put a Tamil voice in ~/.nila/voices/ (a .onnx and its .onnx.json)\n"
        "  3. run: brain speak \"வணக்கம்\"\n"
        "On Windows nothing needs installing: SAPI is built in, and\n"
        "`brain speak --list-voices` shows the voices Windows already has."
    )


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
