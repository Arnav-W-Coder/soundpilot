"""Descriptor measurements for the complaint rules (spec F5 lexicon table)."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import theilslopes

from mixassist.analysis import BAND_EDGES, THIRDS, Record, Spectrum
from mixassist.analysis.spectrum import power_db, third_edges

FIT_LO_HZ, FIT_HI_HZ = 50, 16_000   # nominal third-octave centres used for the fitted line


def _fit_thirds(spec: Spectrum) -> list[tuple[float, float, float]]:
    """(exact centre, lo edge, hi edge) for the thirds 50 Hz–16 kHz below Nyquist."""
    out = []
    for nominal, exact in THIRDS:
        lo, hi = third_edges(exact)
        if FIT_LO_HZ <= nominal <= FIT_HI_HZ and hi <= spec.nyquist:
            out.append((exact, lo, hi))
    return out


class ReferenceBaseline:
    """Expected band level = the other spectrum, level-aligned on the median third-octave difference
    (50 Hz–16 kHz). The median ignores the EQ'd region, so the broadband offset left after loudness
    matching (which the EQ itself shifts) isn't counted as excess."""

    basis = "the reference"

    def __init__(self, spec: Spectrum, other: Spectrum):
        self.spec, self.other = spec, other
        diffs = [power_db(spec.power(lo, hi)) - power_db(other.power(lo, hi)) for _, lo, hi in _fit_thirds(spec)]
        self.offset = float(np.median(diffs)) if diffs else 0.0

    def band(self, lo: float, hi: float) -> tuple[float, float]:
        hi = min(hi, self.spec.nyquist, self.other.nyquist)
        return power_db(self.spec.power(lo, hi)), power_db(self.other.power(lo, hi)) + self.offset


class LineBaseline:
    """Expected band level from a robust (Theil–Sen) line through third_octave_db vs log-frequency,
    50 Hz–16 kHz, integrated over the band and compared within the fitted range only."""

    basis = "a line fitted to the spectrum"

    def __init__(self, spec: Spectrum):
        self.spec = spec
        thirds = _fit_thirds(spec)
        x = np.array([math.log10(c / 1000) for c, _, _ in thirds])
        y = np.array([power_db(spec.power(lo, hi)) for _, lo, hi in thirds])
        self.slope, self.intercept = theilslopes(y, x)[:2]  # dB per third-octave level, per decade
        self.lo_hz, self.hi_hz = thirds[0][1], thirds[-1][2]

    def _model_power(self, lo: float, hi: float) -> float:
        # third-octave level L(x) = a + b·x is the power in a 0.1-decade band, so the density per unit
        # log10(f) is 10^(L/10) / 0.1
        a, b = self.intercept, self.slope
        x1, x2 = math.log10(lo / 1000), math.log10(hi / 1000)
        if abs(b) < 1e-9:
            return 10 ** (a / 10) * (x2 - x1) / 0.1
        return (10 / (b * math.log(10))) * (10 ** ((a + b * x2) / 10) - 10 ** ((a + b * x1) / 10)) / 0.1

    def band(self, lo: float, hi: float) -> tuple[float, float]:
        lo, hi = max(lo, self.lo_hz), min(hi, self.hi_hz)
        return power_db(self.spec.power(lo, hi)), power_db(self._model_power(lo, hi))


def baseline_for(record: Record, reference: Record | None):
    return ReferenceBaseline(record.spectrum, reference.spectrum) if reference else LineBaseline(record.spectrum)


@dataclass
class Measurement:
    descriptor: str
    value: float
    target: float
    delta: float
    unit: str
    excess: float      # amount in the complaint's direction (positive = worse), in `unit`
    severity: float    # >= 1 when the rule fires; ratios compare descriptors
    basis: str
    summary: str       # short human text, e.g. "+5.4 dB at 5–20 kHz"

    @property
    def triggered(self) -> bool:
        return self.severity >= 1.0

    def finding(self) -> dict:
        return {"descriptor": self.descriptor, "value": round(self.value, 2), "target": round(self.target, 2),
                "delta": round(self.delta, 2), "unit": self.unit, "triggered": self.triggered,
                "basis": self.basis, "summary": self.summary}


def fmt_hz(hz: float) -> str:
    return f"{hz / 1000:g} kHz" if hz >= 1000 else f"{hz:g} Hz"


def fmt_range(bands: list[str]) -> str:
    lo = min(BAND_EDGES[b][0] for b in bands)
    hi = max(BAND_EDGES[b][1] for b in bands)
    if lo >= 1000:
        return f"{lo / 1000:g}–{hi / 1000:g} kHz"
    return f"{lo:g}–{fmt_hz(hi)}" if hi < 1000 else f"{fmt_hz(lo)}–{fmt_hz(hi)}"


def band_levels(baseline, bands: list[str]) -> tuple[float, float]:
    vals, targets = zip(*(baseline.band(*BAND_EDGES[b]) for b in bands))
    return float(np.mean(vals)), float(np.mean(targets))


def measure(name: str, spec: dict, record: Record, reference: Record | None) -> Measurement | None:
    """Measure one lexicon descriptor; None when the file can't show it (e.g. quiet with no reference)."""
    m = spec["measure"]
    out = record.output
    kind = m["kind"]
    if kind == "band":
        baseline = baseline_for(record, reference)
        value, target = band_levels(baseline, m["bands"])
        delta = value - target
        excess = delta if m["direction"] == "excess" else -delta
        thr = m["threshold_db"]
        return Measurement(name, value, target, delta, "dB", excess, excess / thr, baseline.basis,
                           f"{delta:+.1f} dB at {fmt_range(m['bands'])}")
    if kind == "loud":
        tp, crest = out["true_peak_dbtp"], out["crest_db"]
        tp_max, crest_min = m["max_true_peak_dbtp"], m["min_crest_db"]
        tp_m = Measurement(name, tp, tp_max, tp - tp_max, "dBTP", tp - tp_max,
                           1 + (tp - tp_max) if tp > tp_max else 0.0, "the −1 dBTP ceiling",
                           f"true peak {tp:+.1f} dBTP")
        if crest is not None and crest < crest_min and not tp_m.triggered:
            return Measurement(name, crest, crest_min, crest - crest_min, "dB", crest_min - crest,
                               1 + (crest_min - crest) / 3, "a 6 dB minimum crest factor", f"crest {crest:.1f} dB")
        return tp_m
    if kind == "quiet":
        ref = out.get("reference")
        if not ref or ref.get("lufs_diff") is None:
            return None
        d, thr = ref["lufs_diff"], m["max_lufs_diff"]
        return Measurement(name, out["integrated_lufs"], out["integrated_lufs"] - d, d, "LU", thr - d,
                           1 + (thr - d) / 2 if d < thr else 0.0, "the reference",
                           f"{d:+.1f} LU vs the reference")
    if kind == "corr_mean":
        c = out["correlation_mean"]
        if c is None:
            if out.get("channels") != 1:
                return None
            c = 1.0
        thr = m["above"]
        return Measurement(name, c, thr, c - thr, "correlation", c - thr,
                           1 + (c - thr) / 0.05 if c > thr else 0.0, "a 0.95 correlation limit",
                           f"correlation {c:.2f}")
    if kind == "corr_p5":
        c = out["correlation_p5"]
        if c is None:
            return None
        thr = m["below"]
        return Measurement(name, c, thr, c - thr, "correlation", thr - c,
                           1 + (thr - c) / 0.25 if c < thr else 0.0, "0 correlation (5th percentile)",
                           f"5th-percentile correlation {c:.2f}")
    raise ValueError(f"unknown measure kind {kind!r} for {name}")
