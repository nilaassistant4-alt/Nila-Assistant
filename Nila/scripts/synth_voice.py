#!/usr/bin/env python3
"""Generate deterministic synthetic 'speakers' as WAV files.

Real speaker verification needs real audio. For automated tests, demos and CI
we synthesise voiced-speech-like signals with a per-speaker vocal-tract profile
(formant frequencies, pitch, jitter, noise). Two different speaker profiles
produce clearly different embeddings; two utterances from the same profile
produce similar ones - which is exactly the property the test suite needs.

Usage::

    python scripts/synth_voice.py --speaker alice --text "seven three nine" --out /tmp/a1.wav
    python scripts/synth_voice.py --speaker bob   --text "seven three nine" --out /tmp/b1.wav

Also importable: ``synth_speaker(name, text, seed) -> AudioSample``.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import random
import sys
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain.util.audio import SAMPLE_RATE, AudioSample, write_wav  # noqa: E402

# Per-speaker vocal tract profiles (Hz) + voice quality knobs.
PROFILES: Dict[str, Dict[str, float]] = {
    "alice": {"f0": 196.0, "f1": 760, "f2": 1250, "f3": 2500, "f4": 3400,
              "jitter": 0.012, "breath": 0.10, "rate": 4.6, "bw": 90},
    "bob":   {"f0": 112.0, "f1": 520, "f2": 1480, "f3": 2350, "f4": 3150,
              "jitter": 0.018, "breath": 0.16, "rate": 3.9, "bw": 110},
    "carol": {"f0": 168.0, "f1": 640, "f2": 1080, "f3": 2760, "f4": 3620,
              "jitter": 0.009, "breath": 0.07, "rate": 5.2, "bw": 80},
    "dave":  {"f0": 96.0,  "f1": 470, "f2": 1620, "f3": 2200, "f4": 2980,
              "jitter": 0.022, "breath": 0.20, "rate": 3.4, "bw": 125},
}


def profile(name: str) -> Dict[str, float]:
    if name in PROFILES:
        return dict(PROFILES[name])
    # deterministic pseudo-profile for arbitrary names
    h = hashlib.sha256(name.encode()).digest()
    rnd = random.Random(int.from_bytes(h[:8], "big"))
    return {
        "f0": rnd.uniform(88, 230),
        "f1": rnd.uniform(420, 820),
        "f2": rnd.uniform(950, 1750),
        "f3": rnd.uniform(2100, 2900),
        "f4": rnd.uniform(2900, 3800),
        "jitter": rnd.uniform(0.006, 0.025),
        "breath": rnd.uniform(0.05, 0.22),
        "rate": rnd.uniform(3.2, 5.5),
        "bw": rnd.uniform(70, 130),
    }


def _resonator(rate: int, freq: float, bw: float) -> Tuple[float, float, float]:
    r = math.exp(-math.pi * bw / rate)
    theta = 2 * math.pi * freq / rate
    a1 = 2.0 * r * math.cos(theta)
    a2 = -(r * r)
    gain = (1.0 - a1 - a2) * 0.9
    return a1, a2, gain


def synth_speaker(name: str, text: str = "one two three four five six",
                  seed: int = 0, rate: int = SAMPLE_RATE,
                  duration: float | None = None) -> AudioSample:
    p = profile(name)
    rnd = random.Random(hashlib.sha256(f"{name}|{seed}".encode()).digest()[:8])
    words = [w for w in text.replace(",", " ").split() if w]
    if duration is None:
        duration = max(1.6, 0.30 * len(words) + rnd.uniform(0.15, 0.5))
    n = int(rate * duration)

    # source: harmonic glottal pulse train with jitter + aspiration noise
    src = [0.0] * n
    phase = 0.0
    for i in range(n):
        f0 = p["f0"] * (1.0 + p["jitter"] * math.sin(2 * math.pi * 5.1 * i / rate + seed))
        f0 *= 1.0 + 0.02 * math.sin(2 * math.pi * 0.7 * i / rate)   # slow intonation
        phase += 2 * math.pi * f0 / rate
        harm = 0.0
        for k in range(1, 13):
            harm += math.sin(k * phase) / (k ** 1.15)
        src[i] = harm + p["breath"] * rnd.gauss(0, 1)

    # syllabic envelope: each word gets a burst with attack/decay
    env = [0.0] * n
    per_word = n / max(1, len(words))
    for wi in range(len(words)):
        start = int(wi * per_word)
        end = min(n, int(start + per_word * 0.92))
        span = max(1, end - start)
        gap = rnd.uniform(0.03, 0.09)
        for i in range(start, end):
            t = (i - start) / span
            amp = math.sin(math.pi * min(1.0, t / 0.85)) ** 1.3
            amp *= 1.0 + 0.18 * math.sin(2 * math.pi * p["rate"] * (i - start) / rate)
            env[i] = amp * (1.0 - gap)
        for i in range(end, min(n, end + int(per_word * 0.08))):
            env[i] = 0.0

    # vocal tract: cascade of 4 formant resonators
    res = [_resonator(rate, p[f"f{k}"], p["bw"] * (1 + 0.25 * (k - 1))) for k in range(1, 5)]
    state = [[0.0, 0.0] for _ in res]
    weights = [1.0, 0.62, 0.34, 0.18]
    out = [0.0] * n
    for i in range(n):
        acc = 0.0
        x = src[i] * env[i]
        for ri, (a1, a2, gain) in enumerate(res):
            y = gain * x + a1 * state[ri][0] + a2 * state[ri][1]
            state[ri][1] = state[ri][0]
            state[ri][0] = y
            acc += weights[ri] * y
        out[i] = acc

    # normalise + gentle room noise
    peak = max((abs(v) for v in out), default=1.0) or 1.0
    scale = 0.85 / peak
    out = [v * scale + rnd.gauss(0, 0.0015) for v in out]
    return AudioSample(out, rate)


def synth_impostor(base: str, text: str, seed: int = 0, rate: int = SAMPLE_RATE) -> AudioSample:
    """A different speaker saying the same challenge."""
    others = [k for k in PROFILES if k != base] or ["impostor-x"]
    return synth_speaker(others[seed % len(others)], text, seed=seed + 991, rate=rate)


def main() -> int:
    ap = argparse.ArgumentParser(description="synthesise test voices")
    ap.add_argument("--speaker", default="alice")
    ap.add_argument("--text", default="one two three four five six")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--seconds", type=float, default=None)
    ap.add_argument("--count", type=int, default=1, help="write N utterances (out.1.wav ...)")
    args = ap.parse_args()
    paths: List[str] = []
    for i in range(args.count):
        audio = synth_speaker(args.speaker, args.text, seed=args.seed + i, duration=args.seconds)
        if args.count == 1:
            path = Path(args.out)
        else:
            path = Path(args.out).with_suffix("").with_name(f"{Path(args.out).stem}.{i+1}.wav")
        write_wav(path, audio.samples, audio.sample_rate)
        paths.append(f"{path}  ({audio.duration:.2f}s)")
    print("\n".join(paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
