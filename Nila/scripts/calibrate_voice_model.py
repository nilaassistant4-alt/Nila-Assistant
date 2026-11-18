#!/usr/bin/env python3
"""Train and ship ``brain/identity/voice_model.json``.

The default voice backend scores a candidate utterance against enrolled
templates with cosine similarity in a *whitened* feature space. Whitening needs
population statistics, and real ones need a labelled corpus of many speakers
(VoxCeleb etc.) that we obviously cannot ship in a ZIP.

So this script fits the calibration on a **synthetic development population**:
randomly generated vocal-tract profiles (pitch, formants, jitter, speaking rate)
rendered by ``scripts/synth_voice.py``. That yields honest *relative* geometry -
it decorrelates the features and removes the channel/energy directions that
otherwise dominate cosine - and it lets us measure a threshold on held-out
synthetic speakers instead of guessing one.

**Important:** numbers measured on synthetic speech are not real-world EER.
Before trusting this backend with real users, re-run this script against a real
development set (see ``docs/ROADMAP.md`` -> "Voice backend v2"), or swap in a
neural backend (ECAPA-TDNN / resemblyzer) behind the same ``VoiceBackend``
interface.

Usage::

    python scripts/calibrate_voice_model.py --dev-speakers 40 --eval-speakers 24
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from brain.identity.voice import (  # noqa: E402
    RAW_DIM, MfccVoiceBackend, _jacobi_eigh, cosine, extract_features, l2norm,
)
from scripts.synth_voice import synth_speaker  # noqa: E402

PHRASES = [
    "seven three nine one four two",
    "alpha cedar mango quill solar umber",
    "two eight five zero six one",
    "violet nectar harbor zephyr kilo lotus",
]
NAMED = ["alice", "bob", "carol", "dave"]


def collect(speakers: Sequence[str], utterances: int = 2, phrases: Sequence[str] = PHRASES
            ) -> Dict[str, List[List[float]]]:
    out: Dict[str, List[List[float]]] = {}
    for si, spk in enumerate(speakers):
        vecs = []
        for u in range(utterances):
            text = phrases[(si + u) % len(phrases)]
            audio = synth_speaker(spk, text, seed=1000 * si + u)
            vecs.append(extract_features(audio))
        out[spk] = vecs
    return out


def moments(vectors: Sequence[Sequence[float]]) -> Tuple[List[float], List[float]]:
    n = float(len(vectors))
    mean = [sum(v[d] for v in vectors) / n for d in range(RAW_DIM)]
    std = []
    for d in range(RAW_DIM):
        var = sum((v[d] - mean[d]) ** 2 for v in vectors) / n
        std.append(math.sqrt(var) + 1e-6)
    return mean, std


def zscore(vectors: Sequence[Sequence[float]], mean: Sequence[float],
           std: Sequence[float]) -> List[List[float]]:
    return [[(v[d] - mean[d]) / std[d] for d in range(RAW_DIM)] for v in vectors]


def pca(vectors: Sequence[Sequence[float]], keep: int = 40, ridge: float = 1e-6,
        floor: float = 0.1) -> Tuple[List[List[float]], Dict[str, float]]:
    """PCA with *shrunk* whitening.

    Two failure modes we measured and fixed:

    1. **Full whitening is catastrophic here.** The development population has
       fewer utterances (``n``) than feature dimensions (86), so the covariance
       is rank-deficient: the smallest eigenvalues are pure ridge/noise. Dividing
       by ``sqrt(1e-6)`` amplifies noise ~1000x, and *every* pair of speakers
       then looks nearly identical (cosine -> 0.95+). Measured: floor=0,
       keep=86 -> EER 18.8%.
    2. **Truncating components without flooring** loses low-variance but
       genuinely discriminative directions.

    So we keep ``keep`` components and scale each by ``1/sqrt(max(lambda_c,
    floor * lambda_max))`` - i.e. whitening with shrinkage toward PCA. Measured
    on held-out synthetic speakers: floor=0.1, keep=40 -> EER ~0.1%, genuine
    mean 0.83 vs impostor 99th pct 0.52.
    """
    n = float(len(vectors))
    cov = [[0.0] * RAW_DIM for _ in range(RAW_DIM)]
    for v in vectors:
        for i in range(RAW_DIM):
            vi = v[i]
            row = cov[i]
            for j in range(i, RAW_DIM):
                row[j] += vi * v[j]
    denom = (n - 1) if n > 1 else 1.0
    for i in range(RAW_DIM):
        for j in range(i, RAW_DIM):
            cov[i][j] /= denom
            cov[j][i] = cov[i][j]
        cov[i][i] += ridge                      # Tikhonov regularisation
    eig, vecs = _jacobi_eigh(cov)
    order = sorted(range(RAW_DIM), key=lambda i: -eig[i])
    vals = [max(eig[i], 1e-12) for i in order]
    lmax = vals[0] or 1.0
    total = sum(vals) or 1.0
    comps = [[vecs[k][i] for k in range(RAW_DIM)] for i in order[:keep]]
    scale = [1.0 / math.sqrt(max(vals[c], floor * lmax)) for c in range(len(comps))]
    comps = [[w * scale[c] for w in comp] for c, comp in enumerate(comps)]
    info = {"explained_variance": sum(vals[:keep]) / total,
            "lambda_max": round(lmax, 4), "lambda_min_kept": round(vals[keep - 1], 6),
            "floor": floor, "ridge": ridge, "n_vectors": len(vectors)}
    return comps, info


def transform(vec: Sequence[float], mean: Sequence[float], std: Sequence[float],
              comps: Sequence[Sequence[float]]) -> List[float]:
    z = [(vec[d] - mean[d]) / std[d] for d in range(RAW_DIM)]
    return l2norm([sum(w * z[i] for i, w in enumerate(comp)) for comp in comps])


ALPHA = 0.75   # weight on the z-space score (see MfccVoiceBackend docstring)


def blend_score(candidate: Sequence[float], templates: Sequence[Sequence[float]]) -> float:
    """The centroid/best-template blend used inside one comparison space."""
    dim = len(templates[0])
    centroid = l2norm([sum(t[i] for t in templates) / len(templates) for i in range(dim)])
    base = cosine(l2norm(candidate), centroid)
    best = max(cosine(l2norm(candidate), l2norm(t)) for t in templates)
    return 0.75 * base + 0.25 * best


def dual_space(vec: Sequence[float], mean, std, comps) -> Dict[str, List[float]]:
    zz = l2norm([(vec[d] - mean[d]) / std[d] for d in range(RAW_DIM)])
    ww = l2norm([sum(w * ((vec[i] - mean[i]) / std[i]) for i, w in enumerate(c)) for c in comps])
    return {"z": zz, "w": ww}


def dual_score(cand: Dict[str, List[float]], tpls: Sequence[Dict[str, List[float]]]) -> float:
    """Exactly what ``MfccVoiceBackend.score`` does at runtime."""
    s_z = blend_score(cand["z"], [t["z"] for t in tpls])
    s_w = blend_score(cand["w"], [t["w"] for t in tpls])
    return ALPHA * s_z + (1.0 - ALPHA) * s_w


def stats(vals: List[float]) -> Dict[str, float]:
    if not vals:
        return {}
    s = sorted(vals)
    return {"n": len(s), "min": round(s[0], 4),
            "p05": round(s[int(0.05 * (len(s) - 1))], 4),
            "mean": round(sum(s) / len(s), 4),
            "p95": round(s[int(0.95 * (len(s) - 1))], 4), "max": round(s[-1], 4)}


def eer_point(genuine: List[float], impostor: List[float]) -> Tuple[float, float]:
    lo, hi = min(genuine + impostor), max(genuine + impostor)
    best = (2.0, lo, 1.0)
    for k in range(500):
        thr = lo + (hi - lo) * k / 499.0
        far = sum(1 for x in impostor if x >= thr) / max(1, len(impostor))
        frr = sum(1 for x in genuine if x < thr) / max(1, len(genuine))
        if abs(far - frr) < best[0]:
            best = (abs(far - frr), thr, (far + frr) / 2)
    return best[1], best[2]


def evaluate(dev: Dict[str, List[List[float]]], ev: Dict[str, List[List[float]]],
             mean, std, comps, enrol: int = 3) -> Dict[str, object]:
    """Simulate the production ceremony.

    Per held-out speaker: enrol ``enrol`` utterances, then score the remaining
    utterances as genuine trials. Every other speaker's utterances are scored
    against that template set as impostor trials.
    """
    emb = {spk: [dual_space(v, mean, std, comps) for v in vecs] for spk, vecs in ev.items()}
    speakers = [s for s, v in emb.items() if len(v) > enrol]
    genuine: List[float] = []
    impostor: List[float] = []
    templates: Dict[str, List[List[float]]] = {s: emb[s][:enrol] for s in speakers}
    for spk in speakers:
        for cand in emb[spk][enrol:]:
            genuine.append(dual_score(cand, templates[spk]))
        for other in speakers:
            if other == spk:
                continue
            for cand in emb[other][enrol:]:
                impostor.append(dual_score(cand, templates[spk]))

    thr_eer, err_eer = eer_point(genuine, impostor)
    srt = sorted(impostor)
    def thr_at_far(far: float) -> float:
        idx = min(len(srt) - 1, max(0, int(math.ceil((1.0 - far) * len(srt))) - 1))
        return srt[idx]

    def report(thr: float) -> Dict[str, float]:
        return {"threshold": round(thr, 4),
                "far": round(sum(1 for x in impostor if x >= thr) / max(1, len(impostor)), 4),
                "frr": round(sum(1 for x in genuine if x < thr) / max(1, len(genuine)), 4)}

    secure = thr_at_far(0.01)
    balanced = thr_eer
    convenient = thr_at_far(0.05)
    return {
        "protocol": (f"leave-{enrol}-out, enrol={enrol} utterances, "
                     f"score = {ALPHA}*blend(z) + {1 - ALPHA:.2f}*blend(whitened), "
                     f"blend = 0.75*centroid + 0.25*best-template"),
        "alpha": ALPHA,
        "enrol_utterances": enrol,
        "genuine": stats(genuine),
        "impostor": stats(impostor),
        "eer_threshold": round(thr_eer, 4),
        "eer": round(err_eer, 4),
        "thresholds": {"secure": round(secure, 4), "balanced": round(balanced, 4),
                       "convenient": round(convenient, 4)},
        "at_secure": report(secure),
        "at_balanced": report(balanced),
        "at_convenient": report(convenient),
        "note": ("measured on SYNTHETIC speakers only - not a real-world EER claim; "
                 "recalibrate on a real corpus before production use"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev-speakers", type=int, default=40)
    ap.add_argument("--eval-speakers", type=int, default=24)
    ap.add_argument("--utterances", type=int, default=6,
                    help="utterances per eval speaker (enrol + held-out trials)")
    ap.add_argument("--enrol", type=int, default=3, help="templates used per speaker at eval")
    ap.add_argument("--keep", type=int, default=40,
                    help="PCA components to keep (see pca() docstring)")
    ap.add_argument("--ridge", type=float, default=1e-6)
    ap.add_argument("--floor", type=float, default=0.1,
                    help="whitening shrinkage: eigenvalue floor as a fraction of lambda_max")
    ap.add_argument("--out", default=str(ROOT / "brain/identity/voice_model.json"))
    ap.add_argument("--threshold", default="secure", choices=["secure", "balanced", "convenient"])
    args = ap.parse_args()

    t0 = time.time()
    dev_names = [f"dev-{i:03d}" for i in range(args.dev_speakers)]
    eval_names = NAMED + [f"ev-{i:03d}" for i in range(max(0, args.eval_speakers - len(NAMED)))]
    print(f"rendering {len(dev_names)} dev + {len(eval_names)} eval synthetic speakers "
          f"x {args.utterances} utterances ...", flush=True)
    dev = collect(dev_names, args.utterances)
    ev = collect(eval_names, args.utterances)
    print(f"  features done in {time.time() - t0:.1f}s", flush=True)

    flat = [v for vecs in dev.values() for v in vecs]
    mean, std = moments(flat)
    z = zscore(flat, mean, std)
    comps, info = pca(z, args.keep, args.ridge, args.floor)
    print(f"  PCA keep={args.keep} explained_variance={info['explained_variance']:.3f} "
          f"lambda_max={info['lambda_max']} floor={info['floor']}", flush=True)

    evaluation = evaluate(dev, ev, mean, std, comps, enrol=args.enrol)
    print("  genuine :", evaluation["genuine"])
    print("  impostor:", evaluation["impostor"])
    print(f"  EER={evaluation['eer']} at thr={evaluation['eer_threshold']}")
    for label in ("secure", "balanced", "convenient"):
        print(f"  {label:11s} {evaluation['at_' + label] if 'at_' + label in evaluation else ''}")

    threshold = float(evaluation["thresholds"][args.threshold])
    payload = {
        "backend": "mfcc-cal-v1",
        "version": 1,
        "trained_on": "synthetic-dev-population (scripts/synth_voice.py)",
        "raw_dim": RAW_DIM,
        "keep": len(comps),
        "explained_variance": round(info["explained_variance"], 4),
        "whitening": info,
        "alpha": ALPHA,
        "mean": [round(m, 6) for m in mean],
        "std": [round(s, 6) for s in std],
        "components": [[round(w, 7) for w in c] for c in comps],
        "threshold": threshold,
        "threshold_policy": args.threshold,
        "evaluation": evaluation,
        "enrol_utterances_recommended": args.enrol,
        "trained_at": int(time.time()),
        "warning": evaluation["note"],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1))
    print(f"wrote {out} ({out.stat().st_size/1024:.0f} KiB), default threshold={threshold}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
