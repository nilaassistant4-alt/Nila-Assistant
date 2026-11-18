"""Speaker verification backend.

Default backend ``mfcc-v1`` (dependency-free):
    waveform -> 16 kHz mono -> pre-emphasis -> 25 ms frames / 10 ms hop
             -> power spectrum -> 40-band mel filterbank -> log -> DCT-II
             -> 20 MFCCs + deltas -> per-band mean/std + log-F0 percentiles
             -> L2-normalised embedding (~86 dims)
    score    = cosine(candidate, mean(enrolled templates))

This is a *classic* text-independent speaker model: robust enough to separate
speakers in a controlled enrolment/verification setting, but it is NOT
state-of-the-art and MUST NOT be relied on as a sole factor. That is why the
MVP requires passphrase + voice + liveness (see ``ceremony.py``).

Swap in a neural backend by implementing ``VoiceBackend`` (e.g. ECAPA-TDNN via
``speechbrain``, resemblyzer, or a cloud speaker-verification API). The
interface returns the same embedding/score contract, and templates are stored
encrypted with the backend name so mixed-version devices fail loudly rather
than silently mis-scoring.
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import json
from pathlib import Path

from ..crypto.hash import random_bytes
from ..util.misc import b64d
from ..util.audio import (
    SAMPLE_RATE, AudioSample, audio_fingerprint, deltas, frame_f0, frame_signal,
    hamming, load_audio, mfcc, normalize, power_spectrum, resample_linear, to_mono_float,
)

BACKEND_NAME = "mfcc-cal-v1"
RAW_DIM = 86            # dimension of the raw feature vector
EMBED_DIM = RAW_DIM     # whitened embedding keeps at most RAW_DIM components


@dataclass
class VoiceScore:
    score: float
    passed: bool
    backend: str
    threshold: float
    detail: Dict[str, Any]


def _mean_std(rows: Sequence[Sequence[float]]) -> List[float]:
    if not rows:
        return []
    dim = len(rows[0])
    n = float(len(rows))
    means = [sum(r[d] for r in rows) / n for d in range(dim)]
    stds = []
    for d in range(dim):
        var = sum((r[d] - means[d]) ** 2 for r in rows) / n
        stds.append(math.sqrt(var))
    return means + stds


def _percentiles(values: Sequence[float], ps: Sequence[float]) -> List[float]:
    if not values:
        return [0.0] * len(ps)
    vals = sorted(values)
    out = []
    for p in ps:
        idx = min(len(vals) - 1, max(0, int(round(p * (len(vals) - 1)))))
        out.append(vals[idx])
    return out


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def l2norm(vec: Sequence[float]) -> List[float]:
    n = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / n for v in vec]


# --------------------------------------------------------------------------- #
# raw acoustic features
# --------------------------------------------------------------------------- #
# Layout of the 86-dim raw vector produced by ``extract_features``:
#   [ 0:38] MFCC 1..19  mean, std        (c0 / log-energy is deliberately dropped:
#                                         it is channel gain, not identity)
#   [38:76] delta-MFCC 1..19 mean, std
#   [76:79] log-F0 p10/p50/p90, expressed in semitones above 100 Hz
#   [79]    voiced-frame ratio
#   [80:82] spectral centroid mean, std  (Hz/1000)
#   [82]    85% spectral rolloff mean   (Hz/1000)
#   [83]    dynamic range of frame energy (dB/20)
#   [84]    log duration                (natural log of seconds)
#   [85]    zero-crossing-rate std


def extract_features(audio: AudioSample, n_mfcc: int = 20, n_filt: int = 40,
                     nfft: int = 512, target_rate: int = SAMPLE_RATE) -> List[float]:
    """Waveform -> 86-dim raw acoustic feature vector (see layout above)."""
    samples = to_mono_float(audio)
    if audio.sample_rate != target_rate:
        samples = resample_linear(samples, audio.sample_rate, target_rate)
    samples = normalize(samples)
    if len(samples) < int(target_rate * 0.4):
        raise ValueError("audio too short for voice verification (need >= 0.4 s)")

    frames, win, _ = frame_signal(samples, target_rate, win_ms=25.0, hop_ms=10.0)
    window = hamming(win)
    energies = [math.sqrt(sum(v * v for v in fr) / win) for fr in frames]
    peak = max(energies) or 1e-9
    active = [i for i, e in enumerate(energies) if e > 0.04 * peak]
    if len(active) < 4:
        active = list(range(len(frames)))

    feats = mfcc(samples, rate=target_rate, n_mfcc=n_mfcc, n_filt=n_filt, nfft=nfft)
    dfeats = deltas(feats)
    feats = [feats[i] for i in active if i < len(feats)]
    dfeats = [dfeats[i] for i in active if i < len(dfeats)]

    mfcc_stats = _mean_std([f[1:n_mfcc] for f in feats])      # 38 (skip c0)
    delta_stats = _mean_std([d[1:n_mfcc] for d in dfeats])    # 38

    # pitch
    f0s: List[float] = []
    step = max(1, len(frames) // 60)
    for fr in frames[::step]:
        f0, clarity = frame_f0(fr, target_rate)
        if f0 and clarity > 0.35:
            f0s.append(12.0 * math.log2(max(50.0, f0) / 100.0))
    f0_stats = _percentiles(f0s, (0.1, 0.5, 0.9))
    voiced = [len(f0s) / float(max(1, len(frames[::step])))]

    # spectral shape on active frames
    centroids: List[float] = []
    rolloffs: List[float] = []
    zcrs: List[float] = []
    for i in active[:: max(1, len(active) // 40)]:
        fr = frames[i]
        ps = power_spectrum([fr[k] * window[k] for k in range(win)], nfft)
        total = sum(ps) or 1e-12
        cent = sum(k * ps[k] for k in range(len(ps))) / total * (target_rate / 2.0) / (len(ps) - 1)
        acc = 0.0
        roll = 0.0
        for k, p in enumerate(ps):
            acc += p
            if acc >= 0.85 * total:
                roll = k * (target_rate / 2.0) / (len(ps) - 1)
                break
        centroids.append(cent / 1000.0)
        rolloffs.append(roll / 1000.0)
        zcrs.append(sum(1 for k in range(1, len(fr)) if (fr[k - 1] < 0) != (fr[k] < 0)) / float(len(fr)))

    cm = sum(centroids) / len(centroids) if centroids else 0.0
    cs = math.sqrt(sum((c - cm) ** 2 for c in centroids) / len(centroids)) if centroids else 0.0
    rm = sum(rolloffs) / len(rolloffs) if rolloffs else 0.0
    zs = math.sqrt(sum((z - (sum(zcrs) / len(zcrs))) ** 2 for z in zcrs) / len(zcrs)) if zcrs else 0.0

    lo = min(energies) or 1e-9
    dyn = (20 * math.log10((max(energies) or 1e-9) / max(lo, 1e-9))) / 20.0
    dur = math.log(max(0.3, audio.duration))

    vec = (mfcc_stats + delta_stats + f0_stats + voiced + [cm, cs, rm, dyn, dur, zs])
    if len(vec) < RAW_DIM:
        vec += [0.0] * (RAW_DIM - len(vec))
    return [float(x) for x in vec[:RAW_DIM]]


# --------------------------------------------------------------------------- #
# calibration / whitening model
# --------------------------------------------------------------------------- #
DEFAULT_MODEL = Path(__file__).with_name("voice_model.json")


def _jacobi_eigh(matrix: List[List[float]], sweeps: int = 60):
    """Cyclic Jacobi eigendecomposition of a symmetric matrix (pure python)."""
    n = len(matrix)
    a = [row[:] for row in matrix]
    v = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for _ in range(sweeps):
        off = 0.0
        for p in range(n):
            for q in range(p + 1, n):
                off += a[p][q] * a[p][q]
        if off < 1e-18:
            break
        for p in range(n):
            for q in range(p + 1, n):
                if abs(a[p][q]) < 1e-16:
                    continue
                theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
                t = math.copysign(1.0, theta) / (abs(theta) + math.sqrt(theta * theta + 1.0))
                c = 1.0 / math.sqrt(t * t + 1.0)
                sn = t * c
                for k in range(n):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p] = c * akp - sn * akq
                    a[k][q] = sn * akp + c * akq
                for k in range(n):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k] = c * apk - sn * aqk
                    a[q][k] = sn * apk + c * aqk
                for k in range(n):
                    vkp, vkq = v[k][p], v[k][q]
                    v[k][p] = c * vkp - sn * vkq
                    v[k][q] = sn * vkp + c * vkq
    eig = [a[i][i] for i in range(n)]
    return eig, v


class CalibratedVoiceModel:
    """z-score + PCA whitening + threshold, trained by ``scripts/calibrate_voice_model.py``.

    Raw MFCC statistics are heavily correlated and dominated by a few large
    components, so plain cosine similarity saturates near 1.0 for everyone.
    Whitening against a development population fixes the geometry: after it,
    cosine distance tracks *speaker* difference rather than *channel* energy.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else DEFAULT_MODEL
        self.data: Dict[str, Any] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_bytes())
            except Exception:
                self.data = {}
        self.mean: List[float] = list(self.data.get("mean") or [])
        self.std: List[float] = list(self.data.get("std") or [])
        self.components: List[List[float]] = list(self.data.get("components") or [])
        self.threshold: float = float(self.data.get("threshold", 0.30))
        self.keep: int = int(self.data.get("keep", len(self.components) or RAW_DIM))
        self.name: str = str(self.data.get("backend", "mfcc-cal-v1"))
        self.evaluated: Dict[str, Any] = dict(self.data.get("evaluation") or {})

    @property
    def available(self) -> bool:
        return bool(self.mean and self.std and self.components)

    def z(self, raw: Sequence[float]) -> List[float]:
        """raw features -> per-dimension standardised, L2-normalised vector.

        This is the primary comparison space. Per-dimension z-scoring removes
        channel gain and the phrase-level directions that dominate raw MFCC
        statistics; measured on held-out synthetic speakers it gives EER ~0.1%.
        """
        if not self.mean:
            return l2norm(list(raw))
        out = [(raw[i] - self.mean[i]) / (self.std[i] or 1e-6)
               if i < len(self.mean) else raw[i] for i in range(len(raw))]
        return l2norm(out)

    def whiten(self, raw: Sequence[float]) -> List[float]:
        """raw features -> shrunk-whitened embedding (secondary signal)."""
        if not self.available:
            return []
        z = [(raw[i] - self.mean[i]) / (self.std[i] or 1e-6)
             for i in range(min(len(raw), len(self.mean)))]
        k = min(self.keep, len(self.components))
        out = [sum(w * z[i] for i, w in enumerate(comp)) for comp in self.components[:k]]
        return l2norm(out)

    # backwards-compatible alias
    def transform(self, raw: Sequence[float]) -> List[float]:
        return self.z(raw)

    def threshold_for(self, target: str = "balanced") -> float:
        ev = self.evaluated
        return float(ev.get("thresholds", {}).get(target, self.threshold))


_MODEL: Optional[CalibratedVoiceModel] = None


def load_model(path: Optional[Path] = None, refresh: bool = False) -> CalibratedVoiceModel:
    global _MODEL
    if _MODEL is None or refresh or path is not None:
        model = CalibratedVoiceModel(path)
        if path is None:
            _MODEL = model
        return model
    return _MODEL


def _blend(candidate: Sequence[float], templates: Sequence[Sequence[float]],
           centroid_w: float = 0.75) -> float:
    """Score one candidate against an enrolled template set.

    ``centroid_w`` weights the template centroid (stable when enrolment samples
    agree) against the best single template (forgiving when they span two
    recording sessions).
    """
    if not templates or not candidate:
        return 0.0
    dim = len(templates[0])
    if len(candidate) != dim:
        return 0.0
    centroid = l2norm([sum(t[i] for t in templates) / len(templates) for i in range(dim)])
    base = cosine(l2norm(candidate), centroid)
    best = max(cosine(l2norm(candidate), l2norm(t)) for t in templates)
    return centroid_w * base + (1.0 - centroid_w) * best


class VoiceBackend:
    name = "abstract"
    dim = RAW_DIM

    def embed(self, audio: AudioSample) -> Dict[str, List[float]]:  # pragma: no cover
        raise NotImplementedError

    def score(self, candidate: Dict[str, List[float]],
              templates: Sequence[Dict[str, List[float]]]) -> float:
        raise NotImplementedError


class MfccVoiceBackend(VoiceBackend):
    """Default dependency-free backend: calibrated MFCC statistics.

    Scoring combines two spaces:
      * ``z`` - per-dimension standardised features (primary, alpha weight)
      * ``w`` - shrunk-whitened PCA embedding (secondary)

    ``alpha`` is the weight on the z-space score. Measured sweep on held-out
    synthetic speakers: alpha=1.0 -> EER 0.12%, alpha=0.75 -> 0.20% with a wider
    genuine/impostor margin, alpha=0.0 (whitening only) -> 1.4%. We ship 0.75.
    """

    def __init__(self, model_path: Optional[Path] = None, use_model: bool = True,
                 alpha: Optional[float] = None):
        self.model = load_model(model_path) if use_model else CalibratedVoiceModel(None)
        self.calibrated = bool(use_model and self.model.mean)
        self.name = self.model.name if self.calibrated else "mfcc-raw-v1"
        self.dim = RAW_DIM
        self.alpha = float(self.model.data.get("alpha", 0.75) if alpha is None else alpha)
        self.default_threshold = self.model.threshold if self.calibrated else 0.30

    def raw(self, audio: AudioSample) -> List[float]:
        return extract_features(audio)

    def embed(self, audio: AudioSample) -> Dict[str, List[float]]:
        raw = extract_features(audio)
        out = {"z": self.model.z(raw)}
        w = self.model.whiten(raw)
        if w:
            out["w"] = w
        return out

    def score(self, candidate: Dict[str, List[float]],
              templates: Sequence[Dict[str, List[float]]]) -> float:
        tpls = [_coerce_template(t) for t in templates]
        if not tpls:
            return 0.0
        cand = _coerce_template(candidate)
        s_z = _blend(cand.get("z", []), [t.get("z", []) for t in tpls if t.get("z")])
        if not any(t.get("w") for t in tpls) or not cand.get("w"):
            return s_z
        s_w = _blend(cand["w"], [t["w"] for t in tpls if t.get("w")])
        a = self.alpha
        return a * s_z + (1.0 - a) * s_w


def _coerce_template(t: Any) -> Dict[str, List[float]]:
    """Accept both the dict form and the legacy bare-vector form."""
    if isinstance(t, dict):
        return {"z": list(t.get("z") or []), "w": list(t.get("w") or [])}
    return {"z": [float(x) for x in t], "w": []}


def _quality_verdict(scores: Sequence[float], threshold: float) -> str:
    if not scores:
        return "single-sample"
    lo = min(scores)
    if lo >= threshold + 0.15:
        return "good"
    if lo >= threshold:
        return "marginal - enrol one more sample in a quieter room"
    return "poor - re-enrol; samples disagree more than the threshold allows"


def make_backend(name: str = "mfcc-cal-v1", model_path: Optional[Path] = None) -> VoiceBackend:
    if name in ("mfcc-cal-v1", "mfcc-v1", "mfcc", "default", "auto"):
        return MfccVoiceBackend(model_path=model_path)
    raise ValueError(f"unknown voice backend: {name}")


# --------------------------------------------------------------------------- #
# cancelable biometrics
# --------------------------------------------------------------------------- #
def _transform_vec(vec: Sequence[float], seed: bytes, tag: bytes) -> List[float]:
    """Seeded permutation + sign mask, then re-normalise.

    This is an orthogonal transform, so cosine similarity is *preserved exactly*:
    a transformed candidate still matches a transformed template from the same
    speaker, and the calibrated threshold keeps its meaning. What changes is that
    the stored numbers are no longer the raw voiceprint - without the seed they
    cannot be inverted, and they cannot be cross-matched against a template
    enrolled anywhere else. Revoking a leaked template is therefore possible:
    re-enrol with a fresh seed and the old one becomes incomparable.
    """
    n = len(vec)
    if n == 0:
        return []
    rng = random.Random(hashlib.sha256(seed + b"|" + tag).digest())
    perm = list(range(n))
    rng.shuffle(perm)
    out = [float(vec[perm[i]]) * (1.0 if rng.getrandbits(1) else -1.0) for i in range(n)]
    norm = math.sqrt(sum(x * x for x in out)) or 1.0
    return [x / norm for x in out]


def transform_embedding(emb: Any, seed: Optional[bytes]) -> Any:
    """Apply the cancelable transform to an embedding (dict of spaces or list)."""
    if not seed:
        return emb
    if isinstance(emb, dict):
        return {k: (_transform_vec(v, seed, str(k).encode()) if isinstance(v, list) else v)
                for k, v in emb.items()}
    return _transform_vec(list(emb), seed, b"v")


class VoiceVerifier:
    """Stateless scorer + encrypted template holder (templates live in Vault)."""

    def __init__(self, vault, threshold: Optional[float] = None, backend_name: str = BACKEND_NAME,
                 model_path: Optional[Path] = None):
        self.vault = vault
        self.backend = make_backend(backend_name, model_path=model_path)
        if threshold is None:
            threshold = getattr(self.backend, "default_threshold", 0.30)
        self.threshold = float(threshold)
        #: transform seed of the currently loaded template, set by templates()
        self._tpl_seed: Optional[bytes] = None

    # ------------------------------------------------------------------ #
    def embed_source(self, source: Any) -> List[float]:
        audio = source if isinstance(source, AudioSample) else load_audio(source)
        return self.backend.embed(audio)

    def enroll(self, samples: Sequence[Any]) -> Dict[str, Any]:
        """Enrol from N audio sources (paths, bytes, data: URIs or AudioSample)."""
        if len(samples) < 1:
            raise ValueError("need at least one enrolment sample")
        embeddings = [self.embed_source(s) for s in samples]
        meta = {
            "backend": self.backend.name,
            "threshold": self.threshold,
            "enrolled_samples": len(embeddings),
            "alpha": getattr(self.backend, "alpha", 1.0),
        }
        # Cancelable biometrics: generate a fresh per-enrolment transform seed
        # and store only the transformed embeddings. The raw voiceprint is never
        # written to disk, and re-enrolling produces a template that cannot be
        # matched against a previously leaked one.
        seed = random_bytes(16)
        transformed = [transform_embedding(e, seed) for e in embeddings]
        meta = dict(meta)
        meta["cancelable"] = True
        self.vault.store_voice_template(transformed, meta, transform_seed=seed)
        self._tpl_seed = seed
        self.vault.audit("voice_enrolled", {"samples": len(embeddings),
                                            "backend": self.backend.name,
                                            "cancelable": True})
        zs = [e.get("z", []) for e in embeddings]
        pairs = [cosine(zs[i], zs[j]) for i in range(len(zs)) for j in range(len(zs)) if i < j]
        scores = [self.backend.score(e, [x for k, x in enumerate(embeddings) if k != i])
                  for i, e in enumerate(embeddings)] if len(embeddings) > 1 else []
        return {
            "samples": len(embeddings),
            "dim": len(zs[0]) if zs else 0,
            "backend": self.backend.name,
            "threshold": self.threshold,
            "intra_speaker_min": round(min(pairs), 4) if pairs else None,
            "intra_speaker_mean": round(sum(pairs) / len(pairs), 4) if pairs else None,
            "leave_one_out_min": round(min(scores), 4) if scores else None,
            "leave_one_out_mean": round(sum(scores) / len(scores), 4) if scores else None,
            "quality": _quality_verdict(scores, self.threshold),
        }

    def templates(self) -> List[Dict[str, List[float]]]:
        tpl = self.vault.load_voice_template()
        if not tpl:
            return []
        if tpl.get("meta", {}).get("backend") != self.backend.name:
            raise RuntimeError(
                f"voice template backend mismatch: stored {tpl.get('meta', {}).get('backend')}, "
                f"loaded {self.backend.name}"
            )
        raw = tpl.get("transform_seed")
        self._tpl_seed = b64d(raw) if isinstance(raw, str) else (raw or None)
        return tpl.get("embeddings") or []

    def verify(self, source: Any, threshold: Optional[float] = None) -> VoiceScore:
        thr = self.threshold if threshold is None else float(threshold)
        templates = self.templates()
        if not templates:
            return VoiceScore(0.0, False, self.backend.name, thr,
                              detail={"error": "no voice template enrolled"})
        emb = self.embed_source(source)
        # The stored templates are transformed, so the candidate must be
        # transformed with the same seed. Cosine is preserved, so the threshold
        # and calibration are unaffected.
        cand = transform_embedding(emb, self._tpl_seed)
        score = self.backend.score(cand, templates)
        return VoiceScore(score=round(score, 4), passed=score >= thr,
                          backend=self.backend.name, threshold=thr,
                          detail={"templates": len(templates),
                                  "dim": len(emb.get("z", [])),
                                  "alpha": getattr(self.backend, "alpha", 1.0)})

    def verify_file(self, path: str) -> VoiceScore:
        return self.verify(load_audio(path))

    @staticmethod
    def fingerprint(source: Any) -> str:
        audio = source if isinstance(source, AudioSample) else load_audio(source)
        return audio_fingerprint(audio)
