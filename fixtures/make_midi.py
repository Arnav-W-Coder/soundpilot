"""MIDI fixtures for F7/F8 (spec §7). 120 BPM, 4/4, PPQ 96 like an FL piano roll export.

    python -m fixtures.make_midi [out_dir]     (default: fixtures/midi)
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import mido

PPQ = 96
TEMPO_US = 500_000  # 120 BPM
ROBOTIC_VELOCITY = 100
SLOPPY_JITTER_MS = 40.0
SLOPPY_SEED = 1

# (pitch, length_beats, velocity); monophonic, notes back to back. C major with planted F#4, C#4, G#4.
MELODY_C_MAJOR_3WRONG = [
    (60, 1, 96), (64, .5, 80), (67, .5, 84), (72, 1, 104), (71, 1, 88),
    (69, .5, 82), (67, .5, 78), (66, .5, 74), (64, .5, 80), (62, 1, 90), (60, 1, 98),
    (64, 1, 92), (65, .5, 76), (67, .5, 84), (69, 1, 100), (67, 1, 88),
    (65, .5, 80), (64, .5, 78), (62, .5, 72), (61, .5, 76), (60, 2, 102),
    (67, 1, 94), (69, .5, 82), (71, .5, 86), (72, 1, 108), (67, 1, 90),
    (69, .5, 84), (68, .5, 76), (67, 1, 92), (64, 1, 86), (65, .5, 78), (64, .5, 96),
]
WRONG_PCS = {1, 6, 8}  # C#, F#, G#
PLANTED_WRONG = tuple(i for i, (p, _, _) in enumerate(MELODY_C_MAJOR_3WRONG) if p % 12 in WRONG_PCS)

# Demo melody: A minor, flat velocities, three wrong notes (F#, C#, D#).
MELODY_DEMO = [
    (69, 1), (72, .5), (71, .5), (69, .5), (67, .5), (64, 1),
    (65, .5), (66, .5), (67, 1), (69, 1), (64, 1),
    (69, .5), (71, .5), (72, 1), (74, .5), (73, .5), (72, 1),
    (71, .5), (69, .5), (67, .5), (65, .5), (64, 2),
    (62, .5), (64, .5), (65, 1), (63, .5), (64, .5), (69, 2),
    (72, 1), (71, 1), (69, 2),
]

ROBOTIC = [60, 64, 67, 72, 67, 64, 62, 65, 69, 72, 69, 65, 64, 67, 71, 74,
           72, 67, 64, 60, 62, 65, 69, 65, 64, 67, 72, 67, 65, 62, 59, 60]

SLOPPY = [72, 71, 69, 67, 69, 71, 72, 74, 76, 74, 72, 71, 69, 67, 65, 64,
          65, 67, 69, 71, 72, 69, 67, 65, 64, 62, 60, 62, 64, 65, 67, 72]

SCALE_8 = [60, 62, 64, 65, 67, 69, 71, 72]


def _write(path: Path, notes: list[tuple[int, float, float, int]]) -> Path:
    """notes: (pitch, start_beats, length_beats, velocity)."""
    events = []
    for pitch, start, length, vel in notes:
        on = round(start * PPQ)
        off = max(on + 1, round((start + length) * PPQ))
        events.append((on, 1, mido.Message("note_on", note=pitch, velocity=vel)))
        events.append((off, 0, mido.Message("note_off", note=pitch, velocity=0)))
    events.sort(key=lambda e: (e[0], e[1]))
    tempo = mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=TEMPO_US, time=0),
        mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0),
        mido.MetaMessage("end_of_track", time=0),
    ])
    part = mido.MidiTrack()
    last = 0
    for tick, _, msg in events:
        part.append(msg.copy(time=tick - last))
        last = tick
    part.append(mido.MetaMessage("end_of_track", time=0))
    mf = mido.MidiFile(type=1, ticks_per_beat=PPQ)
    mf.tracks.extend([tempo, part])
    path.parent.mkdir(parents=True, exist_ok=True)
    mf.save(path)
    return path


def _sequence(rows) -> list[tuple[int, float, float, int]]:
    out, t = [], 0.0
    for row in rows:
        pitch, length = row[0], row[1]
        vel = row[2] if len(row) > 2 else ROBOTIC_VELOCITY
        out.append((pitch, t, length, vel))
        t += length
    return out


def melody_3wrong() -> list[tuple[int, float, float, int]]:
    return _sequence(MELODY_C_MAJOR_3WRONG)


def robotic() -> list[tuple[int, float, float, int]]:
    return [(p, i * 0.5, 0.5, ROBOTIC_VELOCITY) for i, p in enumerate(ROBOTIC)]


def sloppy() -> list[tuple[int, float, float, int]]:
    rng = random.Random(SLOPPY_SEED)
    ms_per_beat = TEMPO_US / 1000.0
    out = []
    for i, p in enumerate(SLOPPY):
        jitter = rng.uniform(-SLOPPY_JITTER_MS, SLOPPY_JITTER_MS) / ms_per_beat
        vel = rng.randint(70, 110)
        out.append((p, max(0.0, i * 0.5 + jitter), 0.4, vel))
    return out


def scale_8() -> list[tuple[int, float, float, int]]:
    return [(p, i * 0.5, 0.5, ROBOTIC_VELOCITY) for i, p in enumerate(SCALE_8)]


def make_all(out_dir) -> list[Path]:
    out = Path(out_dir)
    return [
        _write(out / "melody_c_major_3wrong.mid", melody_3wrong()),
        _write(out / "robotic.mid", robotic()),
        _write(out / "sloppy.mid", sloppy()),
        _write(out / "scale_8.mid", scale_8()),
        _write(out / "melody.mid", _sequence(MELODY_DEMO)),
    ]


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "midi"
    for p in make_all(target):
        print(p)
