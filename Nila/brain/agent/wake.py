"""The wake word: "Nila", heard inside a listening window the owner opened.

This is the row that was marked *not built*, and the reason given was honest: an
always-on microphone is a battery cost and a privacy cost, and this build refuses
to open a microphone for the rest of the machine's life. That refusal stands. What
changes here is the other half of the sentence - the owner did ask to be able to
*say* the name instead of pressing a key, and "you cannot, ever" was not a design
decision, it was a missing piece.

So the wake word is built with the window made explicit:

* **A window has to be opened.** ``listen(on_heard=...)`` runs only between
  ``open()`` and ``close()``, and it closes itself on a timer. A month-long
  window is refused; the ceiling is ``MAX_WINDOW_SECONDS``.
* **The window is a capability.** Opening it asks the same permission broker as
  the microphone always has (``mic.listen``), once per window, and closing it is
  recorded in the audit journal. The microphone is not "on"; a window is open,
  and windows are counted.
* **Hearing the name is not the same as trusting the voice.** A detected wake
  word is reported with the audio it came from, so the caller can run the
  speaker lock on it *before* acting. A wake word is a doorbell, not a key.
* **Everything is inspectable without a microphone.** ``probe()`` scores audio
  that is already a file, so the matcher is testable on this machine, where there
  is no microphone at all - which is exactly why the old build could not honestly
  claim this worked.

What it still is not: a neural keyword spotter. The matcher is energy + zero
crossing + duration + a two-syllable shape test, and it is calibrated against
synthetic speech, not against a real room. ``LIMITS`` says so, and the acceptance
tests are written to prove the *window* rules, not to pretend the accuracy is
better than it is.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

#: How long a listening window may stay open. Fifteen minutes is a working
#: session at the desk; a day-long window is the always-on microphone the owner
#: rejected, arriving through a side door.
MAX_WINDOW_SECONDS = 15 * 60
DEFAULT_WINDOW_SECONDS = 60

#: Ways the owner says the name. Kept short: every extra phrase is a new false
#: positive, and "Nila" has two syllables, so the matcher needs the shape.
WAKE_PHRASES = ("nila", "hey nila", "hi nila", "hello nila", "நிலா", "ஹே நிலா")

LIMITS = (
    "The matcher looks at loudness, voicing and syllable shape - it is not a "
    "trained keyword spotter. It is reliable on a clear 'Nila' close to the "
    "microphone and unreliable across a noisy room, which is why every detection "
    "carries the audio it came from and the caller is expected to run the speaker "
    "lock on it. A neural wake-word model is a small download and a later stage; "
    "pretending this one is that would be the same mistake as the always-on "
    "microphone it replaces."
)


class WakeError(RuntimeError):
    pass


@dataclass
class Detection:
    phrase: str
    at: float
    confidence: float
    seconds: float
    voiced_ratio: float
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"phrase": self.phrase, "at": self.at,
                "confidence": round(self.confidence, 3),
                "seconds": round(self.seconds, 2),
                "voiced_ratio": round(self.voiced_ratio, 3), **self.detail}


def _profile(samples: Sequence[float], rate: int) -> Dict[str, float]:
    """Loudness, voicing and syllable count - the cheap shape of a spoken word."""
    if not samples or rate <= 0:
        return {"seconds": 0.0, "voiced_ratio": 0.0, "syllables": 0.0,
                "peak": 0.0, "zcr": 0.0}
    values = [float(v) for v in samples]
    peak = max(abs(v) for v in values) or 1e-9
    # 10 ms frames: short enough to see the gap between two syllables. At 20 ms a
    # 30 ms dip disappeared inside one frame and a four-syllable phrase counted as
    # three - which is how a sentence scored as the name.
    frame = max(1, int(rate * 0.01))
    energies: List[float] = []
    crossings = 0
    for start in range(0, len(values) - frame, frame):
        chunk = values[start:start + frame]
        rms = (sum(v * v for v in chunk) / len(chunk)) ** 0.5
        energies.append(rms)
        for a, b in zip(chunk, chunk[1:]):
            if (a >= 0) != (b >= 0):
                crossings += 1
    if not energies:
        return {"seconds": len(values) / rate, "voiced_ratio": 0.0, "syllables": 0.0,
                "peak": peak, "zcr": 0.0}
    # A frame is "voiced" when it carries real energy: 12% of the loudest frame is
    # low enough for a quiet room and high enough that hiss does not count.
    floor = max(energies) * 0.12
    voiced = [e > floor for e in energies]
    runs, started = 0, False
    for flag in voiced:
        if flag and not started:
            runs += 1
            started = True
        elif not flag:
            started = False
    return {"seconds": len(values) / rate,
            "voiced_ratio": sum(1 for v in voiced if v) / len(voiced),
            "syllables": float(runs),
            "peak": peak,
            "zcr": crossings / max(1, len(values) - 1)}


def score(samples: Sequence[float], rate: int) -> Detection:
    """How much does this short clip look like the wake word?

    Calibrated for a clip that has already been cut at a plausible boundary. The
    checks are deliberately few and readable:

    * duration in the 0.25-1.6 s band a name occupies;
    * mostly voiced (a name is not a click, a tap or a door);
    * two energy runs, because "Nila" is two syllables - one run is "Nil", three
      or more is a sentence;
    * a plausible zero-crossing rate for voiced speech (not a whistle, not hiss).
    """
    prof = _profile(samples, rate)
    problems: List[str] = []
    if not 0.25 <= prof["seconds"] <= 1.2:
        problems.append(f"duration {prof['seconds']:.2f}s is outside 0.25-1.2s")
    if prof["voiced_ratio"] < 0.35:
        problems.append(f"only {prof['voiced_ratio']:.0%} of it is voiced")
    if prof["syllables"] < 2:
        problems.append(f"{prof['syllables']:.0f} syllable(s), a name has two")
    # "Nila" is two syllables, "hey Nila" is three. Four is a sentence, and
    # accepting a sentence as a wake word is how the microphone ends up acting
    # on a conversation instead of on an address.
    if prof["syllables"] > 3:
        problems.append(f"{prof['syllables']:.0f} syllables is a sentence, not a name")
    if prof["zcr"] > 0.28:
        problems.append("too much crossing - hiss or a whistle, not a voice")

    parts = [prof["voiced_ratio"], min(1.0, prof["syllables"] / 2.0),
             1.0 if 0.25 <= prof["seconds"] <= 1.2 else 0.3]
    confidence = sum(parts) / 3.0
    if problems:
        confidence *= 0.4
    return Detection(phrase="nila" if not problems else "",
                     at=time.time(), confidence=confidence,
                     seconds=prof["seconds"], voiced_ratio=prof["voiced_ratio"],
                     detail={"problems": problems, "syllables": prof["syllables"],
                             "zcr": round(prof["zcr"], 4)})


def probe(path: str) -> Dict[str, Any]:
    """Score a WAV file. This is how the wake word is checked with no microphone."""
    from ..util.audio import load_audio

    audio = load_audio(path)
    found = score(audio.samples, audio.sample_rate)
    return {"ok": True, "heard": bool(found.phrase), **found.to_dict(),
            "file": str(path),
            "phrases": list(WAKE_PHRASES),
            "note": "a detection is a doorbell, not a key: run the speaker lock next"}


def listen(window: "WakeWindow", chunk_seconds: float = 0.4,
           max_chunks: int = 0) -> Dict[str, Any]:
    """Run a window against a real microphone until it closes or hears the name.

    Split out from ``WakeWindow`` so the timing rules can be tested without any
    hardware, and so the microphone exists in exactly one place. Returns the
    detection (or the reason there was none) and always closes the window.
    """
    from . import ears

    chunks = 0
    try:
        if not window.open_():
            window.open()          # opening asks the broker once, and the clock starts
        while window.open_():
            audio = ears.record(seconds=chunk_seconds)
            found = window.feed(audio.samples, audio.sample_rate)
            chunks += 1
            if found is not None:
                return {"ok": True, "heard": True, **found.to_dict(),
                        "chunks": chunks,
                        "samples": audio.as_float_list(), "rate": audio.sample_rate,
                        "next": ("run the speaker lock on this audio BEFORE acting - "
                                 "a wake word is a doorbell, not a key")}
            if max_chunks and chunks >= max_chunks:
                return {"ok": True, "heard": False, "chunks": chunks,
                        "reason": f"stopped after {chunks} chunk(s) as asked"}
        return {"ok": True, "heard": False, "chunks": chunks,
                "reason": "the window closed without hearing the name"}
    except ears.EarsError as exc:
        return {"ok": False, "heard": False, "chunks": chunks,
                "error": str(exc),
                "how": "the window logic is fine; a microphone and a transcriber are "
                       "what is missing on this machine"}
    finally:
        window.close("finished")


class WakeWindow:
    """A bounded listening window, with a ledger of what it heard."""

    def __init__(self, seconds: float = DEFAULT_WINDOW_SECONDS,
                 broker: Any = None, audit: Optional[Callable[..., None]] = None,
                 clock: Callable[[], float] = time.time):
        if seconds <= 0:
            raise WakeError("a window must be open for some time")
        if seconds > MAX_WINDOW_SECONDS:
            raise WakeError(
                f"{int(seconds)} s is longer than the {MAX_WINDOW_SECONDS // 60}-minute "
                f"ceiling. An all-day microphone is what this design refuses; if you "
                f"want it, that is a different decision and a different build.")
        self.seconds = float(seconds)
        self.broker = broker
        self.audit = audit
        self.clock = clock
        self._opened_at = 0.0
        self._closed_at = 0.0
        self._detections: List[Detection] = []
        self._lock = threading.Lock()

    # -- opening and closing -------------------------------------------- #
    def open(self) -> Dict[str, Any]:
        with self._lock:
            if self.open_():
                raise WakeError("a window is already open")
            if self.broker is not None:
                decision = self.broker.request("mic.listen", purpose="wake word window",
                                               ask=False)
                if not decision.allowed:
                    raise WakeError(
                        "the microphone capability was not affirmed: " + decision.reason)
            self._opened_at = self.clock()
            self._closed_at = 0.0
        self._say("wake_window_opened", {"seconds": self.seconds})
        return {"ok": True, "open": True, "seconds": self.seconds,
                "closes_at": self._opened_at + self.seconds,
                "note": f"the microphone is used for {int(self.seconds)} s, then it stops"}

    def close(self, reason: str = "asked") -> Dict[str, Any]:
        with self._lock:
            was = self.open_()
            self._closed_at = self.clock()
        if was:
            self._say("wake_window_closed", {"reason": reason,
                                             "heard": len(self._detections)})
        return {"ok": True, "was_open": was, "heard": len(self._detections),
                "reason": reason}

    def open_(self) -> bool:
        return bool(self._opened_at) and not self._closed_at \
            and (self.clock() - self._opened_at) < self.seconds

    def seconds_left(self) -> float:
        if not self.open_():
            return 0.0
        return max(0.0, self.seconds - (self.clock() - self._opened_at))

    # -- what happens inside a window ----------------------------------- #
    def feed(self, samples: Sequence[float], rate: int) -> Optional[Detection]:
        """Hand a short clip to the matcher. Refuses once the window has closed.

        This is the function the microphone loop calls; it is also the function a
        test calls, which is what makes the timing rule testable without a mic.
        """
        if not self.open_():
            if self._opened_at and not self._closed_at:
                self.close("expired")
            raise WakeError("no window is open - a wake word outside its window is "
                            "just a microphone that was left on")
        found = score(samples, rate)
        if found.phrase:
            with self._lock:
                self._detections.append(found)
            self._say("wake_word_heard", {"confidence": round(found.confidence, 3)})
            return found
        return None

    def report(self) -> Dict[str, Any]:
        return {"ok": True, "open": self.open_(),
                "seconds_left": round(self.seconds_left(), 1),
                "window_seconds": self.seconds,
                "max_window_seconds": MAX_WINDOW_SECONDS,
                "heard": [d.to_dict() for d in self._detections],
                "phrases": list(WAKE_PHRASES),
                "limits": LIMITS,
                "note": ("the microphone is opened for a window, once, with "
                         "permission - never for the life of the machine")}

    def _say(self, event: str, detail: Dict[str, Any]) -> None:
        if self.audit is None:
            return
        try:
            self.audit(event, detail)
        except Exception:                      # pragma: no cover - best effort
            pass
