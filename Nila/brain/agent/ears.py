"""Hearing: speech to text, with the microphone gated like any other power.

The owner wants to *speak* to Nila - "same as typing, but voice" - and the stage
before this one built the other half (Nila speaking). This is the listening half,
and the design choices are about what happens when the pieces are missing, which
on this machine is every one of them:

* **The microphone is a capability, not an ambient power.** `mic.listen` is asked
  for once, per use, through the same broker as files and hosts. A background
  listener is still not offered, so nothing here runs without an explicit call.
* **A transcriber may not exist, and that is said, not hidden.** Whisper (the
  `openai-whisper` package), `faster-whisper`, and `vosk` are probed in that
  order. With none installed the answer is the exact command to install one and
  the fact that nothing was heard - never an empty string that looks like silence.
* **A WAV file works without a microphone.** `brain ears --file x.wav` transcribes
  a recording, which is how this can be tested and used before any microphone
  permission is granted.
* **Recording is separate from transcribing.** `record` returns samples; `hear`
  does both. That split is what lets the speaker lock judge the same audio that
  becomes text, so a stranger's voice can be refused before any words are used.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..util.audio import AudioError, AudioSample, load_audio, record_microphone

#: Transcribers, most preferred first, with what each needs.
ENGINES = {
    "whisper": {"module": "whisper", "why": "OpenAI Whisper: good Tamil, slow on CPU",
                "install": "pip install openai-whisper", "tamil": "good"},
    "faster_whisper": {"module": "faster_whisper",
                       "why": "CTranslate2 Whisper: same accuracy, several times faster",
                       "install": "pip install faster-whisper", "tamil": "good"},
    "vosk": {"module": "vosk", "why": "50 MB model, very fast, weaker on Tamil",
             "install": "pip install vosk  (+ a ta model from alphacephei.com)",
             "tamil": "fair"},
}


class EarsError(RuntimeError):
    pass


def available() -> Dict[str, Any]:
    """Which transcriber is installed, and what to do about it if none is."""
    found: List[str] = []
    for name, spec in ENGINES.items():
        if importlib.util.find_spec(spec["module"]) is not None:
            found.append(name)
    mic = importlib.util.find_spec("sounddevice") is not None
    return {
        "ok": True,
        "engines": found,
        "preferred": found[0] if found else "",
        "microphone": mic,
        "can_hear": bool(found),
        "can_record": bool(mic),
        "how": ("" if found else
                "no speech-to-text engine is installed; "
                + "; ".join(f"{n}: {s['install']}" for n, s in ENGINES.items())),
        "note": ("recording needs sounddevice; transcribing a WAV file does not "
                 "need a microphone at all"),
    }


def record(seconds: float = 5.0) -> AudioSample:
    """Capture from the microphone. Raises with the install line if absent."""
    if importlib.util.find_spec("sounddevice") is None:
        raise EarsError("microphone capture needs `pip install sounddevice numpy`")
    try:
        return record_microphone(seconds=seconds)
    except AudioError as exc:                 # pragma: no cover - depends on hardware
        raise EarsError(str(exc)) from exc


def _whisper(audio: AudioSample, language: str, model_size: str) -> Dict[str, Any]:
    import whisper                          # noqa: F401 - probed in available()

    path = Path(tempfile.mkdtemp()) / "in.wav"
    from ..util.audio import write_wav

    write_wav(path, audio.as_float_list(), audio.sample_rate)
    model = whisper.load_model(model_size)
    result = model.transcribe(str(path), language=None if language in ("", "auto") else language,
                              fp16=False)
    return {"text": str(result.get("text") or "").strip(),
            "language": str(result.get("language") or language),
            "engine": f"whisper ({model_size})", "segments": len(result.get("segments") or [])}


def _faster_whisper(audio: AudioSample, language: str, model_size: str) -> Dict[str, Any]:
    from faster_whisper import WhisperModel

    from ..util.audio import write_wav

    path = Path(tempfile.mkdtemp()) / "in.wav"
    write_wav(path, audio.as_float_list(), audio.sample_rate)
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(path), language=None if language in ("", "auto")
                                      else language)
    pieces = [s.text for s in segments]
    return {"text": " ".join(p.strip() for p in pieces).strip(),
            "language": getattr(info, "language", language) or language,
            "engine": f"faster-whisper ({model_size})", "segments": len(pieces)}


def _vosk(audio: AudioSample, language: str, model_path: str = "") -> Dict[str, Any]:
    """Vosk wants raw signed-16-bit little-endian PCM, handed over in one go."""
    import json as _json
    import struct

    import vosk

    model = vosk.Model(model_path) if model_path else vosk.Model(lang="ta")
    rec = vosk.KaldiRecognizer(model, audio.sample_rate)
    pcm = b"".join(struct.pack("<h", int(max(-1.0, min(1.0, x)) * 32767))
                   for x in audio.as_float_list())
    rec.AcceptWaveform(pcm)
    out = _json.loads(rec.FinalResult() or "{}")
    return {"text": str(out.get("text") or "").strip(), "language": language or "ta",
            "engine": "vosk", "segments": 1}


def transcribe(audio: AudioSample, language: str = "ta", model_size: str = "small",
               engine: str = "") -> Dict[str, Any]:
    """Turn audio into text with whichever engine is installed."""
    found = available()
    order = [engine] if engine else found["engines"]
    if not order:
        raise EarsError(found["how"])
    last_error = ""
    for name in order:
        try:
            if name == "whisper":
                return {"ok": True, **_whisper(audio, language, model_size)}
            if name == "faster_whisper":
                return {"ok": True, **_faster_whisper(audio, language, model_size)}
            if name == "vosk":
                return {"ok": True, **_vosk(audio, language)}
        except Exception as exc:               # a missing model is not a crash
            last_error = f"{name}: {exc}"
            continue
    raise EarsError(f"no engine could transcribe that ({last_error})")


def hear(seconds: float = 5.0, language: str = "ta", model_size: str = "small",
         source: Optional[str | os.PathLike] = None) -> Dict[str, Any]:
    """Listen (or read a WAV) and return both the audio and the words.

    The samples come back with the text on purpose: the caller can put the same
    audio through the speaker lock before it acts on the words.
    """
    if source:
        audio = load_audio(str(Path(source).expanduser()))
    else:
        audio = record(seconds)
    out = transcribe(audio, language=language, model_size=model_size)
    out["seconds"] = round(len(audio.as_float_list()) / float(audio.sample_rate or 1), 2)
    out["rate"] = audio.sample_rate
    out["samples"] = audio.as_float_list()
    return out


def help_text() -> str:
    return ("Hearing (offline): install one transcriber, then\n"
            "  brain ears --listen 5            record 5 s and transcribe\n"
            "  brain ears --file note.wav       transcribe a recording\n"
            "  brain ears --status              what is installed here\n"
            "Whisper: pip install openai-whisper   (best Tamil, slow on CPU)\n"
            "faster-whisper: pip install faster-whisper   (same accuracy, faster)\n"
            "vosk: pip install vosk + a ta model (50 MB, weakest Tamil)")
