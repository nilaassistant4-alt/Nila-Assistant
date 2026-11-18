"""Audio loading + a pure-Python DSP stack (FFT, mel filterbank, MFCC, F0).

numpy is used when present (much faster); everything below also works on the
standard library alone so the shipped ZIP has zero mandatory dependencies.
"""
from __future__ import annotations

import base64
import math
import os
import struct
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence

from .misc import try_import

np = try_import("numpy")
sf = try_import("soundfile")

SAMPLE_RATE = 16_000


class AudioError(RuntimeError):
    pass


@dataclass
class AudioSample:
    samples: Sequence[float]  # mono float32 in [-1, 1]
    sample_rate: int

    @property
    def duration(self) -> float:
        return len(self.samples) / float(self.sample_rate) if self.sample_rate else 0.0

    def as_float_list(self) -> List[float]:
        return list(self.samples)


def _decode_wave_bytes(data: bytes) -> AudioSample:
    with wave.open(__import__("io").BytesIO(data), "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    if width not in (1, 2, 3, 4):
        raise AudioError(f"unsupported PCM width: {width}")
    fmt = {1: "b", 2: "h", 3: None, 4: "i"}[width]
    mono: List[float] = []
    if fmt is None:  # 24-bit packed
        count = len(frames) // 3
        vals = []
        for i in range(count):
            chunk = frames[i * 3:(i + 1) * 3]
            v = int.from_bytes(chunk, "little", signed=False)
            if v & 0x800000:
                v -= 1 << 24
            vals.append(v / 8388608.0)
    else:
        n = len(frames) // width
        vals = list(struct.unpack("<" + fmt * n, frames[: n * width]))
        scale = {1: 128.0, 2: 32768.0, 4: 2147483648.0}[width]
        vals = [v / scale for v in vals]
    if channels > 1:
        vals = [
            sum(vals[i:i + channels]) / channels
            for i in range(0, len(vals) - channels + 1, channels)
        ]
    return AudioSample(vals, rate)


def load_audio(source: str | bytes | os.PathLike) -> AudioSample:
    """Load audio from a path, raw bytes, or a ``data:`` URI."""
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
        path = None
    else:
        text = str(source)
        if text.startswith("data:"):
            header, _, payload = text.partition(",")
            data = base64.b64decode(payload)
            path = None
        else:
            path = Path(text).expanduser()
            if not path.exists():
                raise AudioError(f"audio file not found: {path}")
            data = path.read_bytes()

    if data[:4] == b"RIFF":
        return _decode_wave_bytes(data)

    if sf is not None:
        try:
            import io

            arr, rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
            mono = [float(sum(row) / len(row)) for row in arr]
            return AudioSample(mono, int(rate))
        except Exception as exc:
            raise AudioError(f"could not decode audio ({type(exc).__name__}: {exc})") from exc
    raise AudioError(
        "unsupported audio format: only WAV/PCM works without the optional "
        "'soundfile' extra (pip install personal-brain[audio])"
    )


def record_microphone(seconds: float = 4.0, rate: int = SAMPLE_RATE) -> AudioSample:
    """Live microphone capture. Requires the optional 'audio' extra."""
    sd = try_import("sounddevice")
    if sd is None:
        raise AudioError(
            "microphone capture needs the optional audio extra: "
            "pip install sounddevice numpy"
        )
    import numpy as _np

    data = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype="float32")
    sd.wait()
    return AudioSample([float(x) for x in _np.asarray(data).reshape(-1)], rate)


def to_mono_float(audio: AudioSample) -> List[float]:
    return [float(x) for x in audio.samples]


def resample_linear(samples: Sequence[float], src_rate: int, dst_rate: int) -> List[float]:
    if src_rate == dst_rate or not samples:
        return list(samples)
    ratio = dst_rate / float(src_rate)
    out_len = int(len(samples) * ratio)
    if out_len <= 0:
        return []
    out = [0.0] * out_len
    step = src_rate / float(dst_rate)
    n = len(samples)
    for i in range(out_len):
        pos = i * step
        i0 = int(pos)
        frac = pos - i0
        a = samples[i0] if i0 < n else samples[-1]
        b = samples[i0 + 1] if i0 + 1 < n else a
        out[i] = a + (b - a) * frac
    return out


def normalize(samples: Sequence[float]) -> List[float]:
    vals = list(samples)
    if not vals:
        return vals
    mean = sum(vals) / len(vals)
    vals = [v - mean for v in vals]
    peak = max((abs(v) for v in vals), default=0.0)
    if peak < 1e-8:
        return vals
    scale = 0.95 / peak
    return [v * scale for v in vals]


def hz_to_mel(f: float) -> float:
    return 2595.0 * math.log10(1.0 + f / 700.0)


def mel_to_hz(m: float) -> float:
    return 700.0 * (10.0 ** (m / 2595.0) - 1.0)


# --------------------------------------------------------------------------- #
# FFT
# --------------------------------------------------------------------------- #
def _bit_reverse_permute(n: int) -> List[int]:
    bits = int(math.log2(n))
    out = [0] * n
    for i in range(n):
        r = 0
        for b in range(bits):
            if i & (1 << b):
                r |= 1 << (bits - 1 - b)
        out[i] = r
    return out


_TABLES: dict = {}


def _tables(n: int):
    if n not in _TABLES:
        perm = _bit_reverse_permute(n)
        cos_t = [math.cos(2 * math.pi * k / n) for k in range(n // 2)]
        sin_t = [math.sin(2 * math.pi * k / n) for k in range(n // 2)]
        _TABLES[n] = (perm, cos_t, sin_t)
    return _TABLES[n]


def _fft_py(re: List[float], im: List[float]) -> None:
    """In-place iterative radix-2 FFT (pure python)."""
    n = len(re)
    if n & (n - 1):
        raise ValueError("FFT length must be a power of two")
    perm, cos_t, sin_t = _tables(n)
    for i in range(n):
        j = perm[i]
        if i < j:
            re[i], re[j] = re[j], re[i]
            im[i], im[j] = im[j], im[i]
    length = 2
    while length <= n:
        half = length >> 1
        step = n // length
        for start in range(0, n, length):
            k = 0
            for j in range(start, start + half):
                wr = cos_t[k]
                wi = -sin_t[k]
                tr = re[j + half] * wr - im[j + half] * wi
                ti = re[j + half] * wi + im[j + half] * wr
                re[j + half] = re[j] - tr
                im[j + half] = im[j] - ti
                re[j] += tr
                im[j] += ti
                k += step
        length <<= 1


def power_spectrum(frame: Sequence[float], nfft: int) -> List[float]:
    if np is not None:
        import numpy as _np

        padded = _np.zeros(nfft, dtype=_np.float64)
        m = min(len(frame), nfft)
        padded[:m] = _np.asarray(frame[:m], dtype=_np.float64)
        spec = _np.fft.rfft(padded)
        mag = (spec.real ** 2 + spec.imag ** 2) / nfft
        return [float(x) for x in mag]

    re = [0.0] * nfft
    im = [0.0] * nfft
    m = min(len(frame), nfft)
    for i in range(m):
        re[i] = float(frame[i])
    _fft_py(re, im)
    out = []
    for k in range(nfft // 2 + 1):
        out.append((re[k] * re[k] + im[k] * im[k]) / nfft)
    return out


def frame_signal(
    samples: Sequence[float], rate: int, win_ms: float = 25.0, hop_ms: float = 10.0
):
    win = max(1, int(rate * win_ms / 1000.0))
    hop = max(1, int(rate * hop_ms / 1000.0))
    if len(samples) < win:
        samples = list(samples) + [0.0] * (win - len(samples))
    frames = []
    for start in range(0, len(samples) - win + 1, hop):
        frames.append(list(samples[start:start + win]))
    return frames, win, hop


def pre_emphasis(samples: Sequence[float], coeff: float = 0.97) -> List[float]:
    vals = list(samples)
    if not vals:
        return vals
    out = [vals[0]]
    prev = vals[0]
    for v in vals[1:]:
        out.append(v - coeff * prev)
        prev = v
    return out


def hamming(n: int) -> List[float]:
    if np is not None:
        import numpy as _np

        return [float(x) for x in _np.hamming(n)]
    if n <= 1:
        return [1.0]
    return [0.54 - 0.46 * math.cos(2 * math.pi * i / (n - 1)) for i in range(n)]


def mel_filterbank(n_filters: int, nfft: int, rate: int, fmin: float = 0.0, fmax: float | None = None):
    fmax = fmax or rate / 2.0
    mels = [hz_to_mel(fmin) + (hz_to_mel(fmax) - hz_to_mel(fmin)) * i / (n_filters + 1)
            for i in range(n_filters + 2)]
    hz = [mel_to_hz(m) for m in mels]
    bins = [int(math.floor((nfft + 1) * h / rate)) for h in hz]
    bank = []
    for f in range(n_filters):
        row = [0.0] * (nfft // 2 + 1)
        lo, mid, hi = bins[f], bins[f + 1], bins[f + 2]
        for k in range(lo, mid):
            if 0 <= k < len(row) and mid > lo:
                row[k] = (k - lo) / float(mid - lo)
        for k in range(mid, hi):
            if 0 <= k < len(row) and hi > mid:
                row[k] = (hi - k) / float(hi - mid)
        bank.append(row)
    return bank


def dct_matrix(n_out: int, n_in: int) -> List[List[float]]:
    rows = []
    norm0 = math.sqrt(1.0 / n_in)
    norm = math.sqrt(2.0 / n_in)
    for k in range(n_out):
        scale = norm0 if k == 0 else norm
        rows.append([scale * math.cos(math.pi * k * (2 * n + 1) / (2.0 * n_in))
                     for n in range(n_in)])
    return rows


def mfcc(
    samples: Sequence[float],
    rate: int = SAMPLE_RATE,
    n_mfcc: int = 20,
    n_filt: int = 40,
    nfft: int = 512,
    win_ms: float = 25.0,
    hop_ms: float = 10.0,
) -> List[List[float]]:
    """Standard MFCC feature matrix (frames x n_mfcc)."""
    x = pre_emphasis(samples)
    frames, win, _ = frame_signal(x, rate, win_ms, hop_ms)
    window = hamming(win)
    bank = mel_filterbank(n_filt, nfft, rate)
    dct = dct_matrix(n_mfcc, n_filt)
    out: List[List[float]] = []
    for fr in frames:
        windowed = [fr[i] * window[i] for i in range(win)]
        ps = power_spectrum(windowed, nfft)
        mel_e = []
        for row in bank:
            acc = 0.0
            for k, w in enumerate(row):
                if w:
                    acc += w * ps[k]
            mel_e.append(math.log(acc + 1e-10))
        coeffs = []
        for drow in dct:
            acc = 0.0
            for k, w in enumerate(drow):
                acc += w * mel_e[k]
            coeffs.append(acc)
        out.append(coeffs)
    return out


def deltas(features: Sequence[Sequence[float]], width: int = 2) -> List[List[float]]:
    n = len(features)
    if n == 0:
        return []
    dim = len(features[0])
    denom = 2.0 * sum(w * w for w in range(1, width + 1)) or 1.0
    out = []
    for t in range(n):
        row = [0.0] * dim
        for w in range(1, width + 1):
            lo = features[max(0, t - w)]
            hi = features[min(n - 1, t + w)]
            for d in range(dim):
                row[d] += w * (hi[d] - lo[d])
        out.append([v / denom for v in row])
    return out


def frame_f0(frame: Sequence[float], rate: int, fmin: float = 70.0,
             fmax: float = 380.0, min_clarity: float = 0.3):
    """Crude autocorrelation pitch estimate. Returns (f0 or None, clarity).

    ``min_clarity`` is a parameter because the right threshold depends on what is
    being asked. A 25 ms frame of a 120 Hz voice contains three periods, so the
    normalised autocorrelation at that lag lands near 0.2 even for a perfectly
    clear voice - a fixed 0.3 therefore reported "no pitch" for every low voice,
    which silently disabled speaker pitch comparison (found while building it).
    Callers that need a permissive read pass their own, lower, floor.
    """
    n = len(frame)
    if n < 32:
        return None, 0.0
    energy = sum(v * v for v in frame) / n
    if energy < 1e-6:
        return None, 0.0
    lag_min = max(2, int(rate / fmax))
    lag_max = min(n - 1, int(rate / fmin))
    if lag_max <= lag_min:
        return None, 0.0
    r0 = sum(v * v for v in frame) or 1e-12
    best_lag, best_val = 0, -1.0
    for lag in range(lag_min, lag_max + 1):
        acc = 0.0
        for i in range(n - lag):
            acc += frame[i] * frame[i + lag]
        val = acc / r0
        if val > best_val:
            best_val, best_lag = val, lag
    if best_lag <= 0 or best_val < min_clarity:
        return None, best_val
    return rate / float(best_lag), best_val


def write_wav(path: str | os.PathLike, samples: Sequence[float], rate: int = SAMPLE_RATE) -> Path:
    p = Path(path)
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(p), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = bytearray()
        for v in samples:
            iv = int(max(-1.0, min(1.0, float(v))) * 32767)
            frames += struct.pack("<h", iv)
        wf.writeframes(bytes(frames))
    return p


def audio_fingerprint(audio: AudioSample) -> str:
    """Stable hash of the waveform - used as an anti-replay marker."""
    import hashlib

    vals = list(audio.samples)
    if len(vals) > 8192:
        stride = len(vals) // 8192
        vals = vals[::stride][:8192]
    quant = struct.pack("<%dh" % len(vals), *[int(round(max(-1.0, min(1.0, v)) * 3000)) for v in vals])
    head = struct.pack("<IIf", len(audio.samples), audio.sample_rate, round(audio.duration, 3))
    return hashlib.sha256(head + quant).hexdigest()[:32]
