"""Normalized <-> unit tables for the FL mixer fader and built-in EQ.

PROBE P3 (fader) and P4 (EQ): every table below is a PLACEHOLDER until the probes in
internal/probes.md measure the real curves on the target FL build. Only the fader point
0.8 -> 0.0 dB is sourced (mixer.setTrackVolume docs: "default value is 0.8").

The tables are monotonic and are inverted by search (bisection), never by a closed formula,
so replacing them with measured points needs no code changes.
"""
from __future__ import annotations

Table = list[tuple[float, float]]

# PROBE P3: normalized fader position -> dB (0.0 is -inf in FL; capped at -100 here).
VOLUME_TABLE: Table = [
    (0.00, -100.00),
    (0.05, -69.57),
    (0.10, -52.18),
    (0.15, -42.01),
    (0.20, -34.79),
    (0.25, -29.19),
    (0.30, -24.61),
    (0.35, -20.74),
    (0.40, -17.39),
    (0.45, -14.44),
    (0.50, -11.79),
    (0.55, -9.40),
    (0.60, -7.22),
    (0.65, -5.21),
    (0.70, -3.35),
    (0.75, -1.62),
    (0.80, 0.00),
    (0.85, 1.52),
    (0.90, 2.96),
    (0.95, 4.31),
    (1.00, 5.60),
]

# PROBE P4: built-in EQ gain, normalized -> dB.
EQ_GAIN_TABLE: Table = [
    (0.00, -18.00),
    (0.05, -15.70),
    (0.10, -13.47),
    (0.15, -11.32),
    (0.20, -9.27),
    (0.25, -7.31),
    (0.30, -5.47),
    (0.35, -3.76),
    (0.40, -2.22),
    (0.45, -0.90),
    (0.50, 0.00),
    (0.55, 0.90),
    (0.60, 2.22),
    (0.65, 3.76),
    (0.70, 5.47),
    (0.75, 7.31),
    (0.80, 9.27),
    (0.85, 11.32),
    (0.90, 13.47),
    (0.95, 15.70),
    (1.00, 18.00),
]

# PROBE P4: built-in EQ frequency, normalized -> Hz.
EQ_FREQ_TABLE: Table = [
    (0.00, 20.0),
    (0.05, 28.3),
    (0.10, 39.9),
    (0.15, 56.4),
    (0.20, 79.6),
    (0.25, 112.5),
    (0.30, 158.9),
    (0.35, 224.4),
    (0.40, 317.0),
    (0.45, 447.7),
    (0.50, 632.5),
    (0.55, 893.4),
    (0.60, 1261.9),
    (0.65, 1782.5),
    (0.70, 2517.9),
    (0.75, 3556.6),
    (0.80, 5023.8),
    (0.85, 7096.3),
    (0.90, 10023.7),
    (0.95, 14158.9),
    (1.00, 20000.0),
]

DEFAULT_VOLUME_NORM = 0.8
EQ_BANDS = ("low", "mid", "high")
# PROBE P4: default band positions (normalized frequency) and bandwidth.
EQ_DEFAULT_FREQ_NORM = (0.25, 0.55, 0.85)
EQ_DEFAULT_GAIN_NORM = 0.5
EQ_DEFAULT_WIDTH_NORM = 0.5


def interp(table: Table, x: float) -> float:
    """Piecewise-linear lookup, clamped to the table's ends."""
    if x <= table[0][0]:
        return table[0][1]
    if x >= table[-1][0]:
        return table[-1][1]
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return table[-1][1]


def invert(table: Table, y: float, iterations: int = 50) -> float:
    """Find x with interp(table, x) == y by bisection (table must be monotonic increasing)."""
    lo, hi = table[0][0], table[-1][0]
    if y <= table[0][1]:
        return lo
    if y >= table[-1][1]:
        return hi
    for _ in range(iterations):
        mid = (lo + hi) / 2
        if interp(table, mid) < y:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def table_range(table: Table) -> tuple[float, float]:
    return table[0][1], table[-1][1]


def norm_to_db(norm: float, table: Table = VOLUME_TABLE) -> float:
    return interp(table, norm)


def db_to_norm(db: float, table: Table = VOLUME_TABLE) -> float:
    return invert(table, db)


def eq_gain_norm_to_db(norm: float, table: Table = EQ_GAIN_TABLE) -> float:
    return interp(table, norm)


def eq_gain_db_to_norm(db: float, table: Table = EQ_GAIN_TABLE) -> float:
    return invert(table, db)


def eq_freq_norm_to_hz(norm: float, table: Table = EQ_FREQ_TABLE) -> float:
    return interp(table, norm)


def eq_freq_hz_to_norm(hz: float, table: Table = EQ_FREQ_TABLE) -> float:
    return invert(table, hz)
