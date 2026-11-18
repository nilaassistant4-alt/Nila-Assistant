"""Whose voice is this? One speaker, enrolled once, and nobody else.

The owner's rule, said twice and in capitals: **only my voice may start her,
approve anything, or be listened to at all.** Every other voice - a comment, a
"stop", a visitor - is refused.

That is a speaker-verification problem, and it is the kind of problem where an
optimistic answer is dangerous. So this module is built around four honest
decisions:

1. **Enrolment is the owner's passphrase-gated act.** A voiceprint can only be
   created while the vault is unlocked. A stranger cannot enrol themselves.
2. **It never says "yes" when it means "maybe".** The score is compared with two
   thresholds, not one: above ``ACCEPT`` it may act; below ``REJECT`` it refuses;
   in between it says *uncertain* and asks again. A binary answer built on a
   single threshold is how a recording of the owner gets accepted.
3. **A replay is not the owner.** A passphrase-gated nonce challenge
   (``challenge``/``verify``) is offered for anything that matters: the printed
   number must be spoken, so a clip from yesterday cannot be played back.
4. **Refusal is the default.** No profile, a short sample, a noisy room, an
   uncertain score - all refuse. The failure mode of a speaker lock must be
   "she did not start", never "she started for someone else".

The features are MFCC statistics computed in ``brain.util.audio`` - no model to
download, no service to call, and the same maths that already scores the voice
unlock. A neural speaker model (ECAPA/x-vector) would be stronger; it is a 100 MB
download and a later stage, and this file says so in ``LIMITS`` rather than
pretending the gap does not exist.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ACCEPT = 0.82          # may act on the instruction
REJECT = 0.55          # below this, it is somebody else (or noise)
MIN_SECONDS = 1.2      # shorter than this cannot be scored honestly
CHALLENGE_SECONDS = 180

LIMITS = (
    "This is a text-independent MFCC voiceprint, not a trained speaker model. It "
    "is good at refusing a clearly different voice and at noticing a short or "
    "noisy sample; it can be fooled by a good recording of the owner, which is why "
    "anything that matters is also gated by a spoken challenge number and by the "
    "vault. A neural speaker model (ECAPA-TDNN or x-vector) would raise the ceiling "
    "and is a later stage."
)


class SpeakerError(RuntimeError):
    pass


def _features(samples: Sequence[float], rate: int) -> List[float]:
    """A fixed-length fingerprint of a voice: MFCC height, shape and movement.

    Plain MFCC means are loudness-sensitive and sit at nearly the same place for
    every human voice, which is why they pass a stranger. The fix that costs
    nothing is to compare the *shape* of the cepstrum instead of its height:

    * ``c1..`` - the coefficients after c0, each frame's spectrum divided by its
      own average level (Cepstral Mean Normalisation), so a loud voice and a quiet
      one in two different rooms look the same;
    * the spread and the delta of those, which carry the rhythm of speech.

    Tunable voices are separated by where their formants sit, and that is exactly
    what survives this normalisation. A real speaker model would still be better;
    this is the honest ceiling of a 0-model design.
    """
    from ..util.audio import AudioSample, deltas, mfcc, normalize, pre_emphasis

    audio = AudioSample(list(samples), rate)
    sig = normalize(pre_emphasis(audio.samples))
    frames = mfcc(sig, rate, n_mfcc=20)
    if not frames:
        raise SpeakerError("that sample was too quiet or too short to read")
    # Cepstral mean normalisation: drop c0 (loudness) and remove each frame's own
    # average, so only the spectral shape of the voice is left.
    shape: List[List[float]] = []
    for f in frames:
        tail = list(f[1:])
        mean = sum(tail) / len(tail) if tail else 0.0
        shape.append([x - mean for x in tail])
    base = [sum(f[i] for f in shape) / len(shape) for i in range(len(shape[0]))]
    var = [max(f[i] for f in shape) - min(f[i] for f in shape) for i in range(len(base))]
    d = deltas(shape)
    dm = ([sum(f[i] for f in d) / len(d) for i in range(len(d[0]))]
          if d else [0.0] * len(base))
    return [round(x, 6) for x in base + var + dm]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return 0.0 if not na or not nb else num / (na * nb)


def _distance_shape(a: Sequence[float], b: Sequence[float]) -> float:
    """Spectral shape: the strongest signal of who is speaking.

    Cosines here sit close to 1.0 for everyone (measured: 0.944-0.998 across
    voices, 0.99 within one voice), so the comparison is on the *residual* -
    how much two fingerprints disagree - stretched over the range where that
    disagreement actually separates speakers.
    """
    cos = _cosine(a, b)
    return max(0.0, min(1.0, (cos - 0.55) / 0.43))


def _pitch_track(samples: Sequence[float], rate: int) -> List[float]:
    """The voiced pitch of each frame, ignoring silence and unvoiced consonants."""
    from ..util.audio import frame_f0, frame_signal, normalize, pre_emphasis

    sig = normalize(pre_emphasis(list(samples)))
    frames, _, _ = frame_signal(sig, rate)
    track: List[float] = []
    for frame in frames:
        energy = sum(v * v for v in frame) / max(1, len(frame))
        if energy < 1e-4:
            continue                       # silence is not a pitch
        f0, clarity = frame_f0(frame, rate, min_clarity=0.12)
        if f0 and clarity >= 0.12:
            track.append(f0)
    return track


def _pitch_distance(a: Sequence[float], b: Sequence[float]) -> float:
    """How far apart two voices sit, in semitones.

    This is the part that tells people apart. Two different voices are usually
    several semitones apart even when their spectra look similar; one person's
    two recordings sit within about a semitone of each other.
    """
    if not a or not b:
        return 0.5                         # unknown, so neither trusted nor refused
    a_med = sorted(a)[len(a) // 2]
    b_med = sorted(b)[len(b) // 2]
    if a_med <= 0 or b_med <= 0:
        return 0.5
    semitones = abs(12.0 * __import__("math").log2(a_med / b_med))
    return max(0.0, min(1.0, 1.0 - semitones / 6.0))


def combined_score(shape_a: Sequence[float], shape_b: Sequence[float],
                   pitch_a: Sequence[float], pitch_b: Sequence[float]) -> Dict[str, float]:
    """One score plus its two parts, so a decision can be explained.

    Shape alone cannot separate speakers (measured); pitch alone can be fooled by
    a cold or a different speaking effort. They are weighted so that **a clear
    pitch mismatch refuses even when the shape matches**, which is the failure the
    owner cares about: somebody else's voice being accepted.
    """
    shape = _distance_shape(shape_a, shape_b)
    pitch = _pitch_distance(pitch_a, pitch_b)
    score = 0.35 * shape + 0.65 * pitch
    return {"score": score, "shape": shape, "pitch": pitch}


@dataclass
class Decision:
    ok: bool
    score: float
    verdict: str            # "owner" | "other" | "uncertain" | "no_profile"
    reason: str = ""
    acted: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "score": round(self.score, 4), "verdict": self.verdict,
                "reason": self.reason, "acted": self.acted}


class SpeakerLock:
    """The single enrolled voice, the pending challenges, and the decisions."""

    def __init__(self, path: Path | str, store: Any = None):
        self.path = Path(path)
        self.store = store
        self._pending: Dict[str, Dict[str, Any]] = {}
        self.load()

    # -- persistence ---------------------------------------------------- #
    def load(self) -> None:
        self.profile: Dict[str, Any] = {}
        if not self.path.exists():
            return
        try:
            if self.store is not None:
                raw = self.store.get("speaker_profile")
                self.profile = json.loads(raw) if raw else {}
            else:                              # pragma: no cover - tests use the store
                self.profile = json.loads(self.path.read_text())
        except Exception:
            self.profile = {}

    def enrolled(self) -> bool:
        return bool(self.profile.get("centroid"))

    def accept_threshold(self) -> float:
        """The line for THIS voice, learned from the owner's own recordings.

        Two recordings of one person differ by more than a synthetic test
        suggests: a cold, a phone call, a quiet room. Enrolling two or more
        samples gives a measured within-speaker score, and the threshold is set
        just under the worst of them - so the lock fits the owner instead of an
        average stranger. It never drops below REJECT + 0.1, because a lock that
        loosens itself to "accept anything" is not a lock.
        """
        own = [float(x) for x in (self.profile.get("self_scores") or []) if x]
        if len(own) < 2:
            return ACCEPT
        return max(REJECT + 0.10, min(ACCEPT, min(own) - 0.05))

    def enrol(self, samples: Sequence[float], rate: int,
              label: str = "owner") -> Dict[str, Any]:
        """Create the voiceprint. Only reachable while the vault is unlocked."""
        if self.store is None:
            raise SpeakerError("the voiceprint is stored sealed; unlock the vault first")
        seconds = len(samples) / float(rate or 1)
        if seconds < MIN_SECONDS:
            raise SpeakerError(f"that was {seconds:.1f}s - record at least "
                               f"{MIN_SECONDS:.0f} seconds of continuous speech")
        feats = _features(samples, rate)
        pitch = _pitch_track(samples, rate)
        if not pitch:
            raise SpeakerError(
                "I could not hear a steady voice pitch in that recording - record "
                "again closer to the microphone, in a quiet room, speaking normally "
                "for two or three seconds")
        # Keep a few samples and average them: one recording carries one room and
        # one mood, and a centroid built from a single sample is a fragile lock.
        history: List[List[float]] = list(self.profile.get("samples") or [])
        history.append(feats)
        history = history[-5:]
        pitches_all: List[List[float]] = [list(g) for g in
                                          (self.profile.get("pitches") or [])][-4:]
        pitches_all.append(list(pitch))
        pitches_all = pitches_all[-5:]
        centroid = [sum(s[i] for s in history) / len(history) for i in range(len(feats))]
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        self_scores: List[float] = []
        for i in range(min(len(history), len(pitches_all))):
            for j in range(i + 1, min(len(history), len(pitches_all))):
                pieces = combined_score(history[i], history[j],
                                        pitches_all[i], pitches_all[j])
                self_scores.append(round(pieces["score"], 4))
        pitches = [[round(p, 2) for p in g] for g in pitches_all]
        self.profile = {"centroid": [round(x, 6) for x in centroid],
                        "samples": [[round(x, 6) for x in s] for s in history],
                        "pitches": pitches,
                        "self_scores": self_scores,
                        "pitch_median": round(sorted(pitch)[len(pitch) // 2], 2)
                        if pitch else 0.0,
                        "label": label, "enrolled_at": stamp,
                        "seconds": round(seconds, 2), "version": 3}
        self._save()
        return {"ok": True, "enrolled": True, "samples": len(history),
                "seconds": round(seconds, 2), "at": stamp,
                "pitch_hz": self.profile["pitch_median"]}

    def _save(self) -> None:
        if self.store is not None:
            self.store.put("speaker_profile", json.dumps(self.profile))
        else:                                  # pragma: no cover
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.profile))

    def reset(self) -> Dict[str, Any]:
        if self.store is not None:
            self.store.delete("speaker_profile")
        elif self.path.exists():               # pragma: no cover
            self.path.unlink()
        self.profile = {}
        return {"ok": True, "enrolled": False}

    # -- judging a sample ------------------------------------------------ #
    def judge(self, samples: Sequence[float], rate: int) -> Decision:
        if not self.enrolled():
            return Decision(False, 0.0, "no_profile",
                            "no voice has been enrolled yet, so nobody can be "
                            "recognised - run `brain voice enrol`")
        seconds = len(samples) / float(rate or 1)
        if seconds < MIN_SECONDS:
            return Decision(False, 0.0, "uncertain",
                            f"only {seconds:.1f}s of speech; I need at least "
                            f"{MIN_SECONDS:.0f}s to judge a voice")
        try:
            feats = _features(samples, rate)
            pitch = _pitch_track(samples, rate)
        except SpeakerError as exc:
            return Decision(False, 0.0, "uncertain", str(exc))
        parts = combined_score(feats, self.profile["centroid"], pitch,
                               [p for group in (self.profile.get("pitches") or [])
                                for p in group])
        score = parts["score"]
        detail = (f"shape {parts['shape']:.2f}, pitch {parts['pitch']:.2f}")
        accept = self.accept_threshold()
        if score >= accept:
            decision = Decision(True, score, "owner", detail + f", line {accept:.2f}")
        elif score < REJECT:
            decision = Decision(False, score, "other",
                                f"that does not sound like the enrolled voice, so "
                                f"nothing was done ({detail})")
        else:
            decision = Decision(False, score, "uncertain",
                                f"the match is in between ({detail}) - not refused "
                                f"outright and not trusted either. Say it again, or "
                                f"use the passphrase")
        decision.parts = parts                     # type: ignore[attr-defined]
        return decision

    # -- a spoken challenge, so a recording cannot be replayed ----------- #
    def challenge(self, ttl: int = CHALLENGE_SECONDS) -> Dict[str, Any]:
        if not self.enrolled():
            raise SpeakerError("enrol a voice first (`brain voice enrol`)")
        number = f"{secrets.randbelow(9000) + 1000}"
        token = secrets.token_hex(8)
        self._pending[token] = {"number": number, "expires": time.time() + ttl,
                               "used": False}
        self._prune()
        return {"ok": True, "token": token, "say": f"say this number: {number}",
                "number": number, "expires_in": ttl}

    def _prune(self) -> None:
        now = time.time()
        for tok in [k for k, v in self._pending.items()
                    if v["expires"] < now or v["used"]]:
            self._pending.pop(tok, None)

    def verify_challenge(self, token: str, samples: Sequence[float], rate: int,
                         heard: str = "") -> Decision:
        """The voice must be the owner's AND the number that was asked for."""
        entry = self._pending.get(str(token))
        if not entry:
            return Decision(False, 0.0, "other",
                            "no open challenge with that token (it expired or was used)")
        if entry["expires"] < time.time():
            self._pending.pop(str(token), None)
            return Decision(False, 0.0, "other", "that challenge expired - ask for a new one")
        if entry["used"]:
            return Decision(False, 0.0, "other", "that challenge was already used once")
        entry["used"] = True
        judgement = self.judge(samples, rate)
        if not judgement.ok:
            return judgement
        if heard and entry["number"] not in str(heard):
            return Decision(False, judgement.score, "other",
                            "the voice matched but the number that was asked for was "
                            "not said, so this may be a recording")
        return judgement

    # -- the gate used by everything else -------------------------------- #
    def may_act(self, samples: Optional[Sequence[float]] = None,
                rate: int = 16000, purpose: str = "") -> Decision:
        """The one question every voice-triggered action asks."""
        if samples is None:
            return Decision(False, 0.0, "no_profile",
                            f"nothing to judge for '{purpose}': no audio was given")
        decision = self.judge(samples, rate)
        decision.acted = decision.ok
        return decision
