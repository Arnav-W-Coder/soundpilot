"""Read and write single-part MIDI files as a flat, index-stable note list."""
from __future__ import annotations

import os
from dataclasses import dataclass, replace

import mido

from mixassist.errors import ToolError

DEFAULT_TEMPO_US = 500_000  # 120 BPM, the MIDI default when no set_tempo is present
OUTPUT_MIN_PPQ = 960        # fixed files keep sub-tick humanize offsets


@dataclass(frozen=True)
class Note:
    pitch: int
    start: float      # beats (quarter notes)
    length: float     # beats
    velocity: int     # 1..127
    channel: int = 0

    @property
    def end(self) -> float:
        return self.start + self.length

    def moved(self, **kw) -> "Note":
        return replace(self, **kw)


@dataclass
class Part:
    notes: list[Note]
    ppq: int
    tempo_us: int
    path: str = ""

    @property
    def bpm(self) -> float:
        return 60_000_000 / self.tempo_us

    @property
    def ms_per_beat(self) -> float:
        return self.tempo_us / 1000.0


def sort_notes(notes: list[Note]) -> list[Note]:
    return sorted(notes, key=lambda n: (n.start, n.pitch))


def resolve_path(path: str) -> str:
    p = os.path.expanduser(str(path or ""))
    if not p or not os.path.isabs(p):
        raise ToolError("FILE_ERROR", f"path must be absolute: {path!r}", hint="pass the full path to the .mid file")
    if not os.path.isfile(p):
        raise ToolError("FILE_ERROR", f"file not found: {p}")
    return p


def load_part(path: str, track: int | None = None) -> Part:
    """Parse a .mid into notes sorted by (start, pitch). `track` selects one track; default merges all."""
    p = resolve_path(path)
    try:
        mf = mido.MidiFile(p)
    except (OSError, EOFError, ValueError, KeyError, IndexError) as exc:
        raise ToolError("FILE_ERROR", f"cannot parse MIDI file {p}: {exc}") from exc
    if mf.type == 2:
        raise ToolError("UNSUPPORTED", "type-2 MIDI files (independent sequences) are not supported")
    ppq = mf.ticks_per_beat
    if track is not None and not 0 <= track < len(mf.tracks):
        raise ToolError("NOT_FOUND", f"track {track} not in file (it has {len(mf.tracks)} tracks: 0..{len(mf.tracks) - 1})")

    tempo_us = None
    for tr in mf.tracks:
        tick = 0
        for msg in tr:
            tick += msg.time
            if msg.type == "set_tempo" and tempo_us is None:
                tempo_us = msg.tempo
    tempo_us = tempo_us or DEFAULT_TEMPO_US

    notes: list[Note] = []
    tracks = [mf.tracks[track]] if track is not None else mf.tracks
    for tr in tracks:
        tick = 0
        open_notes: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for msg in tr:
            tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                open_notes.setdefault((msg.channel, msg.note), []).append((tick, msg.velocity))
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                stack = open_notes.get((msg.channel, msg.note))
                if stack:
                    on_tick, vel = stack.pop(0)
                    notes.append(Note(msg.note, on_tick / ppq, max(tick - on_tick, 1) / ppq, vel, msg.channel))
        for (ch, pitch), stack in open_notes.items():
            for on_tick, vel in stack:  # unterminated: close at the end of the track
                notes.append(Note(pitch, on_tick / ppq, max(tick - on_tick, 1) / ppq, vel, ch))

    if not notes:
        where = f" track {track}" if track is not None else ""
        raise ToolError("NOT_FOUND", f"no notes found in {p}{where}")
    return Part(sort_notes(notes), ppq, tempo_us, p)


def save_part(part: Part, path: str) -> str:
    """Write a type-0 file: tempo + notes at max(source PPQ, 960)."""
    ppq = max(part.ppq, OUTPUT_MIN_PPQ)
    events: list[tuple[int, int, mido.Message]] = []
    for n in part.notes:
        on = max(0, round(n.start * ppq))
        off = max(on + 1, round(n.end * ppq))
        events.append((on, 1, mido.Message("note_on", channel=n.channel, note=n.pitch, velocity=n.velocity)))
        events.append((off, 0, mido.Message("note_off", channel=n.channel, note=n.pitch, velocity=0)))
    events.sort(key=lambda e: (e[0], e[1]))  # offs before ons on the same tick

    tr = mido.MidiTrack()
    tr.append(mido.MetaMessage("set_tempo", tempo=part.tempo_us, time=0))
    tr.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    last = 0
    for tick, _, msg in events:
        tr.append(msg.copy(time=tick - last))
        last = tick
    tr.append(mido.MetaMessage("end_of_track", time=0))
    mf = mido.MidiFile(type=0, ticks_per_beat=ppq)
    mf.tracks.append(tr)
    try:
        mf.save(path)
    except OSError as exc:
        raise ToolError("FILE_ERROR", f"cannot write {path}: {exc}") from exc
    return path


def written_resolution(part: Part) -> int:
    return max(part.ppq, OUTPUT_MIN_PPQ)


def snap_to_resolution(notes: list[Note], ppq: int) -> list[Note]:
    """Round starts/lengths to what save_part will write, so reports match the file."""
    out = []
    for n in notes:
        on = max(0, round(n.start * ppq))
        off = max(on + 1, round(n.end * ppq))
        out.append(n.moved(start=on / ppq, length=(off - on) / ppq))
    return out
