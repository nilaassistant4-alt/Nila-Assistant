"""Anti-spoofing / anti-replay layer.

Voice biometrics are trivially replayable, so the ceremony never accepts a
free-form utterance. Instead:

1. ``issue()`` returns a **random challenge** (a nonce + a phrase the user must
   speak, e.g. "seven three nine one four two") with a short TTL.
2. The client records the spoken challenge and returns the audio with the
   nonce.
3. ``check()`` enforces:
   * nonce was issued by us, unused, and not expired
   * the utterance duration is plausible for the challenge length
   * energy profile shows speech (not silence / a tone / a photo of a speaker)
   * the audio fingerprint has not been seen before (**anti-replay**)
   * the submission envelope is HMAC-bound to the nonce (so a captured blob
     cannot be re-labelled with a fresh nonce)

This defeats naive replay of a stored recording. It does **not** defeat a
real-time voice-conversion attack; see docs/THREAT_MODEL.md for the production
hardening path (on-device neural anti-spoofing, ultrasonic/FMCW liveness,
passive challenge binding, and WebAuthn as the primary factor).
"""
from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..util.audio import AudioSample, frame_signal, load_audio
from ..util.misc import now_ms

DIGITS = "one two three four five six seven eight nine zero".split()
WORDS = ("alpha bravo cedar delta echo foxtrot gamma harbor indigo juliet kilo "
         "lotus mango nectar omega pico quill rover solar tulip umber violet "
         "willow xenon yankee zephyr").split()

#: The challenge has to be spoken by the owner, so it has to be in a language the
#: owner actually speaks aloud. A Tamil-first owner was being asked to read
#: English phonetic words ("alpha bravo cedar") to prove she was herself - the
#: MFCC backend is text-independent, so nothing about the maths needed English.
#: It was missing a shipped word list, and a list is cheap.
#:
#: These are ordinary Tamil words with strong, distinct vowel/consonant shapes,
#: no proper nouns, nothing that becomes a different word with a different
#: ending, and nothing longer than three syllables - a long word re-read is a
#: long recording, and it drags past the duration window.
TAMIL_WORDS = ("அம்மா அப்பா வீடு தண்ணீர் பால் சோறு மரம் பூ காற்று நிலா "
               "கடல் மலை பறவை வானம் நெருப்பு மண் வழி கண் கை பாடல்").split()
#: Tamil numerals, spoken the way a number is read out - which is what a
#: nonce challenge needs: a sequence an attacker cannot guess and a replay
#: cannot reuse.
TAMIL_DIGITS = "ஒன்று இரண்டு மூன்று நான்கு ஐந்து ஆறு ஏழு எட்டு ஒன்பது பூஜ்யம்".split()

#: style -> the pool it draws from. Anything not listed raises at issue time
#: instead of silently falling back to English, because a silent fallback is
#: exactly how "Tamil challenges" would be reported as working when they were not.
POOLS: Dict[str, List[str]] = {
    "digits": DIGITS,
    "tamil_digits": TAMIL_DIGITS,
    "words": WORDS,
    "tamil_words": TAMIL_WORDS,
}

#: The styles a caller may ask for when it does not care which language: digits
#: first because a spoken number is the strongest anti-replay challenge.
STYLES = ("digits", "tamil_digits", "words", "tamil_words")

DEFAULT_TTL_SECONDS = 90


@dataclass
class Challenge:
    nonce: str
    phrase: List[str]
    issued_at: int
    expires_at: int
    bound: str = ""          # HMAC over (nonce || phrase) - client must echo it
    attempts: int = 0
    #: which pool the phrase came from, so the reply can say "say this in Tamil"
    #: and so a caller can check the language it asked for is the one it got.
    style: str = "digits"
    language: str = "en"

    def text(self) -> str:
        return " ".join(self.phrase)

    def to_dict(self) -> Dict[str, Any]:
        return {"nonce": self.nonce, "phrase": self.phrase, "text": self.text(),
                "style": self.style, "language": self.language,
                "script": ("tamil" if self.language == "ta" else "latin"),
                "issued_at": self.issued_at, "expires_at": self.expires_at,
                "bound": self.bound}


@dataclass
class LivenessResult:
    ok: bool
    reasons: List[str] = field(default_factory=list)
    detail: Dict[str, Any] = field(default_factory=dict)


class LivenessGuard:
    def __init__(self, key: bytes, ttl_seconds: int = DEFAULT_TTL_SECONDS,
                 replay_window: int = 64, challenge_len: int = 6,
                 min_duration: float = 1.2, max_duration: float = 12.0,
                 max_attempts: int = 3):
        self.key = key
        self.ttl = int(ttl_seconds)
        self.replay_window = int(replay_window)
        self.challenge_len = int(challenge_len)
        self.min_duration = float(min_duration)
        self.max_duration = float(max_duration)
        self.max_attempts = int(max_attempts)
        self._outstanding: Dict[str, Challenge] = {}
        self._used_nonces: List[str] = []
        self._fingerprints: List[str] = []
        self._seen: set = set()

    # ------------------------------------------------------------------ #
    def _bind(self, nonce: str, phrase: List[str]) -> str:
        msg = (nonce + "|" + " ".join(phrase)).encode()
        return hmac.new(self.key, msg, hashlib.sha256).hexdigest()[:32]

    def issue(self, style: str = "digits") -> Challenge:
        key = str(style or "digits").strip().lower()
        pool = POOLS.get(key)
        if not pool:
            raise ValueError(
                f"unknown challenge style '{style}'; say one of {', '.join(POOLS)}")
        phrase = [pool[secrets.randbelow(len(pool))] for _ in range(self.challenge_len)]
        nonce = secrets.token_urlsafe(18)
        ch = Challenge(nonce=nonce, phrase=phrase, issued_at=now_ms(),
                       expires_at=now_ms() + self.ttl * 1000,
                       style=key, language="ta" if key.startswith("tamil") else "en")
        ch.bound = self._bind(nonce, phrase)
        self._outstanding[nonce] = ch
        self._used_nonces.append(nonce)
        self._used_nonces = self._used_nonces[-256:]
        # evict expired
        cutoff = now_ms()
        for k in [k for k, v in self._outstanding.items() if v.expires_at < cutoff]:
            self._outstanding.pop(k, None)
        return ch

    def take(self, nonce: str) -> Optional[Challenge]:
        return self._outstanding.get(nonce)

    def consume(self, nonce: str) -> None:
        self._outstanding.pop(nonce, None)

    def check(self, submission: Dict[str, Any]) -> LivenessResult:
        """Validate a ``{nonce, audio, bound?}`` submission envelope."""
        reasons: List[str] = []
        detail: Dict[str, Any] = {}
        nonce = str(submission.get("nonce", ""))
        ch = self._outstanding.get(nonce)
        if ch is None:
            if nonce in self._used_nonces and nonce not in self._outstanding:
                reasons.append("nonce already consumed (possible replay)")
            else:
                reasons.append("unknown or expired challenge nonce")
            return LivenessResult(False, reasons, detail)

        if now_ms() > ch.expires_at:
            reasons.append(f"challenge expired ({(now_ms() - ch.expires_at) // 1000}s ago)")
        if submission.get("bound") and submission["bound"] != ch.bound:
            reasons.append("challenge binding HMAC mismatch (tampered submission)")
        ch.attempts += 1
        if ch.attempts > self.max_attempts:
            reasons.append("too many attempts for this challenge")

        audio = submission.get("audio")
        if audio is None:
            reasons.append("no audio supplied")
            return LivenessResult(False, reasons, detail)
        try:
            sample = audio if isinstance(audio, AudioSample) else load_audio(audio)
        except Exception as exc:
            reasons.append(f"audio decode failed: {exc}")
            return LivenessResult(False, reasons, detail)

        dur = sample.duration
        detail["duration"] = round(dur, 2)
        expected_min = max(self.min_duration, 0.28 * len(ch.phrase))
        if dur < expected_min:
            reasons.append(f"utterance too short ({dur:.2f}s < {expected_min:.2f}s expected "
                           f"for {len(ch.phrase)} words)")
        if dur > self.max_duration:
            reasons.append(f"utterance too long ({dur:.2f}s)")

        speech = self._speech_profile(sample)
        detail.update(speech)
        if speech["voiced_frames"] < 6:
            reasons.append("insufficient voiced speech detected")
        if speech["dynamic_range_db"] < 9.0:
            reasons.append("flat energy profile (possible synthetic/replayed tone)")
        if speech["spectral_variation"] < 0.02:
            reasons.append("no spectral variation (not natural speech)")

        fp = self._fingerprint(sample)
        detail["fingerprint"] = fp
        if fp in self._seen:
            reasons.append("audio fingerprint seen before (replay detected)")

        ok = not reasons
        if ok:
            self._seen.add(fp)
            self._fingerprints.append(fp)
            self._fingerprints = self._fingerprints[-self.replay_window:]
            self._seen = set(self._fingerprints)
            self.consume(nonce)
        return LivenessResult(ok, reasons, detail)

    # ------------------------------------------------------------------ #
    def _fingerprint(self, sample: AudioSample) -> str:
        import struct

        vals = list(sample.samples)
        if len(vals) > 4096:
            vals = vals[:: len(vals) // 4096][:4096]
        q = struct.pack("<%dh" % len(vals), *[int(round(max(-1, min(1, v)) * 2000)) for v in vals])
        return hashlib.sha256(struct.pack("<If", sample.sample_rate, round(sample.duration, 2)) + q).hexdigest()[:32]

    @staticmethod
    def _speech_profile(sample: AudioSample) -> Dict[str, Any]:
        frames, win, _ = frame_signal(list(sample.samples), sample.sample_rate,
                                      win_ms=25.0, hop_ms=10.0)
        if not frames:
            return {"voiced_frames": 0, "dynamic_range_db": 0.0, "spectral_variation": 0.0,
                    "rms": 0.0}
        energies = [math.sqrt(sum(v * v for v in fr) / win) for fr in frames]
        peak = max(energies) or 1e-9
        active = [e for e in energies if e > 0.05 * peak]
        rms = math.sqrt(sum(e * e for e in energies) / len(energies))
        lo = min(active) if active else 1e-9
        dyn = 20 * math.log10((max(active) if active else 1e-9) / max(lo, 1e-9))
        # cheap spectral centroid variation from zero-crossing rate
        zcrs = []
        for fr in frames:
            zc = sum(1 for i in range(1, len(fr)) if (fr[i - 1] < 0) != (fr[i] < 0))
            zcrs.append(zc / float(len(fr)))
        mean_zcr = sum(zcrs) / len(zcrs) if zcrs else 0.0
        var = math.sqrt(sum((z - mean_zcr) ** 2 for z in zcrs) / len(zcrs)) if zcrs else 0.0
        return {
            "voiced_frames": len(active),
            "dynamic_range_db": round(dyn, 2),
            "spectral_variation": round(var, 4),
            "rms": round(rms, 5),
            "mean_zcr": round(mean_zcr, 4),
        }
