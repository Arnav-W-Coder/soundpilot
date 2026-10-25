"""F7 fixers. Each returns a new note list with the same length and order as the input."""
from __future__ import annotations

import random

from mixassist.midi.analysis import scale_pcs
from mixassist.midi.io import Note

HUMANIZE_SEED = 7
HUMANIZE_MAX_MS = 10.0
HUMANIZE_MAX_VELOCITY = 12.0


def snap_to_scale(notes: list[Note], tonic_pc: int, mode: str) -> list[Note]:
    """Move out-of-key notes to the nearest scale tone.

    Ties go in the direction of the melodic motion into the note (up if it is above
    the previous note, down if below); the first note or a repeated pitch ties down.
    """
    pcs = scale_pcs(tonic_pc, mode)
    out: list[Note] = []
    for i, n in enumerate(notes):
        if n.pitch % 12 in pcs:
            out.append(n)
            continue
        prev = out[i - 1].pitch if i else None
        direction = 1 if prev is not None and n.pitch > prev else -1
        for d in range(1, 7):
            up = n.pitch + d <= 127 and (n.pitch + d) % 12 in pcs
            down = n.pitch - d >= 0 and (n.pitch - d) % 12 in pcs
            if up and down:
                out.append(n.moved(pitch=n.pitch + direction * d))
                break
            if up or down:
                out.append(n.moved(pitch=n.pitch + d if up else n.pitch - d))
                break
        else:
            out.append(n)
    return out


def quantize(notes: list[Note], grid_beats: float, strength_pct: float = 100.0) -> list[Note]:
    """Move each onset toward the nearest grid line by strength; lengths are kept."""
    k = max(0.0, min(100.0, strength_pct)) / 100.0
    out = []
    for n in notes:
        target = round(n.start / grid_beats) * grid_beats
        out.append(n.moved(start=max(0.0, n.start + (target - n.start) * k)))
    return out


def humanize(notes: list[Note], ms_per_beat: float, seed: int = HUMANIZE_SEED) -> list[Note]:
    """Onset ± uniform(0, 10 ms) and velocity ± uniform(0, 12), deterministic for a seed."""
    rng = random.Random(seed)
    out = []
    for n in notes:
        shift_ms = rng.uniform(-HUMANIZE_MAX_MS, HUMANIZE_MAX_MS)
        dv = rng.uniform(-HUMANIZE_MAX_VELOCITY, HUMANIZE_MAX_VELOCITY)
        start = max(0.0, n.start + shift_ms / ms_per_beat)
        vel = int(min(127, max(1, round(n.velocity + dv))))
        out.append(n.moved(start=start, velocity=vel))
    return out
