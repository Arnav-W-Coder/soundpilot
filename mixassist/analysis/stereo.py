"""Stereo descriptors: windowed L/R correlation and side-to-mid energy."""
from __future__ import annotations

import math

import numpy as np

CORRELATION_WINDOW_S = 0.4
SIDE_MID_LIMIT_DB = 120.0
_SILENT_VAR = 1e-12  # below −120 dBFS: no meaningful correlation


def window_correlations(left: np.ndarray, right: np.ndarray, sr: int) -> np.ndarray:
    """Pearson r per non-overlapping 400 ms window; silent windows are skipped."""
    w = int(round(CORRELATION_WINDOW_S * sr))
    n = (len(left) // w) * w
    if n == 0:
        w, n = len(left), len(left)
    lw = left[:n].reshape(-1, w)
    rw = right[:n].reshape(-1, w)
    lw = lw - lw.mean(axis=1, keepdims=True)
    rw = rw - rw.mean(axis=1, keepdims=True)
    vl = np.mean(lw ** 2, axis=1)
    vr = np.mean(rw ** 2, axis=1)
    ok = (vl > _SILENT_VAR) & (vr > _SILENT_VAR)
    cov = np.mean(lw * rw, axis=1)
    return cov[ok] / np.sqrt(vl[ok] * vr[ok])


def correlation_stats(left: np.ndarray, right: np.ndarray, sr: int) -> tuple[float | None, float | None]:
    r = window_correlations(left, right, sr)
    if r.size == 0:
        return None, None
    return float(np.mean(r)), float(np.percentile(r, 5))


def side_mid_db(left: np.ndarray, right: np.ndarray) -> float | None:
    """10·log10(E[S²]/E[M²]) with M = (L+R)/2, S = (L−R)/2, clamped to ±120 dB (L = R gives −120)."""
    em = float(np.mean(((left + right) / 2) ** 2))
    es = float(np.mean(((left - right) / 2) ** 2))
    if em == 0 and es == 0:
        return None
    if es == 0:
        return -SIDE_MID_LIMIT_DB
    if em == 0:
        return SIDE_MID_LIMIT_DB
    return max(-SIDE_MID_LIMIT_DB, min(SIDE_MID_LIMIT_DB, 10 * math.log10(es / em)))
