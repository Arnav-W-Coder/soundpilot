"""Loudness (ITU-R BS.1770-4 via pyloudnorm), loudness range (EBU Tech 3342) and true peak."""
from __future__ import annotations

import math

import numpy as np
import pyloudnorm
from scipy.signal import resample_poly

SHORT_TERM_S = 3.0
TRUE_PEAK_OVERSAMPLE = 4
_TP_CHUNK = 1 << 18
_TP_PAD = 64  # > half the resample_poly filter length at the input rate


def _finite(x: float) -> float | None:
    return float(x) if x is not None and math.isfinite(x) else None


def integrated_lufs(x: np.ndarray, sr: int) -> float | None:
    """Gated integrated loudness: 400 ms blocks, 75 % overlap, −70 LUFS and −10 LU gates."""
    return _finite(pyloudnorm.Meter(sr).integrated_loudness(x))


def short_term_max_and_lra(x: np.ndarray, sr: int) -> tuple[float | None, float | None]:
    """Max short-term (3 s) loudness and LRA (3 s blocks at > 10 Hz, −70 / −20 LU gates, 10th–95th
    percentile). Both are None for signals shorter than one short-term block."""
    if x.shape[0] < SHORT_TERM_S * sr:
        return None, None
    meter = pyloudnorm.Meter(sr)
    lra = meter.loudness_range(x)
    st = [v for v in meter.blockwise_loudness if math.isfinite(v)]
    return (max(st) if st else None), _finite(lra)


def sample_peak(x: np.ndarray) -> float:
    return float(np.max(np.abs(x))) if x.size else 0.0


def true_peak(x: np.ndarray) -> float:
    """Linear true peak: 4× polyphase oversampling (BS.1770 Annex 2), processed in chunks."""
    n = x.shape[0]
    peak = 0.0
    for start in range(0, n, _TP_CHUNK):
        stop = min(n, start + _TP_CHUNK)
        lo, hi = max(0, start - _TP_PAD), min(n, stop + _TP_PAD)
        y = resample_poly(x[lo:hi], TRUE_PEAK_OVERSAMPLE, 1, axis=0)
        a = (start - lo) * TRUE_PEAK_OVERSAMPLE
        b = a + (stop - start) * TRUE_PEAK_OVERSAMPLE
        peak = max(peak, float(np.max(np.abs(y[a:b]))))
    return max(peak, sample_peak(x))


def lin_to_db(v: float, floor_db: float = -150.0) -> float:
    return 20 * math.log10(v) if v > 10 ** (floor_db / 20) else floor_db
