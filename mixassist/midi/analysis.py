"""F7 analysis: key (Krumhansl–Kessler), out-of-key notes, timing vs grid, velocity, density, flags."""
from __future__ import annotations

import re

import numpy as np

from mixassist.errors import ToolError
from mixassist.midi.io import Note, Part

# Krumhansl & Kessler (1982) probe-tone profiles, index 0 = tonic.
KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

MAJOR_STEPS = (0, 2, 4, 5, 7, 9, 11)
MINOR_STEPS = (0, 2, 3, 5, 7, 8, 10)  # natural minor

TONIC_NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
_LETTER_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_KEY_RE = re.compile(r"^([A-G])([#b]?) (major|minor)$")

GRID_CANDIDATES = (1.0, 0.5, 0.25, 0.125)  # coarsest first
GRID_FIT_FRACTION = 0.9

ROBOTIC_VELOCITY_STD = 3.0
ROBOTIC_MEDIAN_OFFSET_MS = 1.0
LOOSE_MEDIAN_OFFSET_MS = 15.0
LOW_CONFIDENCE = 0.5
FEW_NOTES = 8


def parse_key(text: str) -> tuple[int, str]:
    m = _KEY_RE.match(text.strip()) if isinstance(text, str) else None
    if not m:
        raise ToolError("OUT_OF_RANGE", f"key must look like 'C major' or 'F# minor', got {text!r}")
    letter, acc, mode = m.groups()
    pc = (_LETTER_PC[letter] + {"#": 1, "b": -1, "": 0}[acc]) % 12
    return pc, mode


def key_name(tonic_pc: int, mode: str) -> str:
    return f"{TONIC_NAMES[tonic_pc]} {mode}"


def scale_pcs(tonic_pc: int, mode: str) -> frozenset[int]:
    steps = MAJOR_STEPS if mode == "major" else MINOR_STEPS
    return frozenset((tonic_pc + s) % 12 for s in steps)


def pitch_class_histogram(notes: list[Note]) -> np.ndarray:
    hist = np.zeros(12)
    for n in notes:
        hist[n.pitch % 12] += n.length
    return hist


def detect_key(notes: list[Note]) -> tuple[int, str, float]:
    """Best of 24 keys by Pearson r between the duration-weighted histogram and the rotated profile."""
    hist = pitch_class_histogram(notes)
    if np.ptp(hist) == 0:
        return 0, "major", 0.0
    best = (0, "major", -2.0)
    for mode, profile in (("major", KK_MAJOR), ("minor", KK_MINOR)):
        for tonic in range(12):
            r = float(np.corrcoef(hist, np.roll(profile, tonic))[0, 1])
            if r > best[2]:
                best = (tonic, mode, r)
    return best


def grid_offsets_beats(notes: list[Note], grid: float) -> np.ndarray:
    starts = np.array([n.start for n in notes])
    return starts - np.round(starts / grid) * grid


def infer_grid(notes: list[Note]) -> float:
    """Coarsest grid on which >= 90% of onsets sit within a quarter of a grid step."""
    for g in GRID_CANDIDATES:
        off = np.abs(grid_offsets_beats(notes, g))
        if np.mean(off <= g / 4 + 1e-9) >= GRID_FIT_FRACTION:
            return g
    return GRID_CANDIDATES[-1]


def timing(notes: list[Note], ms_per_beat: float, grid: float | None = None) -> dict:
    g = grid or infer_grid(notes)
    off_ms = np.abs(grid_offsets_beats(notes, g)) * ms_per_beat
    return {
        "grid_beats": g,
        "median_offset_ms": round(float(np.median(off_ms)), 2),
        "max_offset_ms": round(float(np.max(off_ms)), 2),
    }


def out_of_key(notes: list[Note], tonic_pc: int, mode: str) -> list[dict]:
    pcs = scale_pcs(tonic_pc, mode)
    return [
        {"index": i, "pitch": n.pitch, "beat": round(n.start, 4)}
        for i, n in enumerate(notes)
        if n.pitch % 12 not in pcs
    ]


def analyze_part(part: Part, grid_beats: float | None = None) -> dict:
    notes = part.notes
    tonic, mode, conf = detect_key(notes)
    ook = out_of_key(notes, tonic, mode)
    tim = timing(notes, part.ms_per_beat, grid_beats)
    vels = np.array([n.velocity for n in notes], dtype=float)
    span = max(n.end for n in notes) - min(n.start for n in notes)
    density = len(notes) / max(span, 1.0)

    vel_std = float(np.std(vels))
    flags = []
    if ook:
        flags.append("out_of_key")
    if vel_std < ROBOTIC_VELOCITY_STD and tim["median_offset_ms"] < ROBOTIC_MEDIAN_OFFSET_MS:
        flags.append("robotic")
    if tim["median_offset_ms"] > LOOSE_MEDIAN_OFFSET_MS:
        flags.append("loose_timing")
    if conf < LOW_CONFIDENCE:
        flags.append("low_key_confidence")
    if len(notes) < FEW_NOTES:
        flags.append("few_notes")

    return {
        "key": {"tonic": TONIC_NAMES[tonic], "mode": mode, "confidence": round(conf, 3)},
        "note_count": len(notes),
        "out_of_key": ook,
        "timing": tim,
        "velocity": {"min": int(vels.min()), "max": int(vels.max()), "std": round(vel_std, 2)},
        "density_notes_per_beat": round(density, 3),
        "flags": flags,
        "bpm": round(part.bpm, 3),
    }
