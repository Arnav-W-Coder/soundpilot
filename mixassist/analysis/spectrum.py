"""Welch power spectrum, the seven report bands and IEC third-octave bands."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.signal import welch

NPERSEG = 8192
POWER_FLOOR_DB = -150.0

BANDS: tuple[tuple[str, float, float], ...] = (
    ("20-60", 20, 60),
    ("60-150", 60, 150),
    ("150-500", 150, 500),
    ("500-2k", 500, 2_000),
    ("2k-5k", 2_000, 5_000),
    ("5k-10k", 5_000, 10_000),
    ("10k-20k", 10_000, 20_000),
)
BAND_EDGES = {name: (lo, hi) for name, lo, hi in BANDS}

_NOMINAL = (25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000,
            1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000)
# IEC 61260 base-10 third octaves: exact centre 1000·10^(k/10), edges centre·10^(±1/20).
THIRDS: tuple[tuple[float, float], ...] = tuple(
    (nom, 1000 * 10 ** (k / 10)) for k, nom in zip(range(-16, 13), _NOMINAL)
)
THIRD_HALF_WIDTH = 10 ** (1 / 20)


def third_edges(exact_centre: float) -> tuple[float, float]:
    return exact_centre / THIRD_HALF_WIDTH, exact_centre * THIRD_HALF_WIDTH


def power_db(p: float) -> float:
    return 10 * math.log10(p) if p > 10 ** (POWER_FLOOR_DB / 10) else POWER_FLOOR_DB


@dataclass
class Spectrum:
    """One-sided PSD averaged over channels (full-scale²/Hz), so ∫psd df = mean square per channel."""

    freqs: np.ndarray
    psd: np.ndarray
    sample_rate: int

    @property
    def nyquist(self) -> float:
        return self.sample_rate / 2

    def power(self, lo: float, hi: float) -> float:
        """Band power, crediting each bin by its overlap with [lo, hi]."""
        df = self.freqs[1] - self.freqs[0]
        left = np.maximum(self.freqs - df / 2, lo)
        right = np.minimum(self.freqs + df / 2, hi)
        return float(np.sum(self.psd * np.clip(right - left, 0, None)))

    def band_db(self, lo: float, hi: float) -> float | None:
        if lo >= self.nyquist:
            return None
        return power_db(self.power(lo, min(hi, self.nyquist)))

    def bands_db(self) -> dict[str, float | None]:
        return {name: self.band_db(lo, hi) for name, lo, hi in BANDS}

    def third_octave_db(self) -> list[dict]:
        out = []
        for nominal, exact in THIRDS:
            lo, hi = third_edges(exact)
            if hi > self.nyquist:
                break
            out.append({"hz": nominal, "db": power_db(self.power(lo, hi))})
        return out

    def centroid_hz(self) -> float | None:
        total = float(np.sum(self.psd))
        return float(np.sum(self.freqs * self.psd) / total) if total > 0 else None


def spectrum(x: np.ndarray, sr: int) -> Spectrum:
    nperseg = min(NPERSEG, x.shape[0])
    freqs, psd = welch(x, fs=sr, window="hann", nperseg=nperseg, noverlap=nperseg // 2, axis=0,
                       scaling="density")
    return Spectrum(freqs=freqs, psd=psd.mean(axis=1), sample_rate=sr)


def exact_centre(nominal_hz: float) -> float:
    for nom, exact in THIRDS:
        if nom == nominal_hz:
            return exact
    raise KeyError(nominal_hz)
