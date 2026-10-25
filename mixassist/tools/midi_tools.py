"""MIDI tools (spec §3.2): analyze_midi, fix_midi (F7) and fl_play_midi (F8)."""
from __future__ import annotations

import os

from mixassist.errors import ToolError
from mixassist.midi import fixers, pyscript
from mixassist.midi.analysis import analyze_part, detect_key, infer_grid, parse_key
from mixassist.midi.io import Part, load_part, save_part, snap_to_resolution, written_resolution
from mixassist.midi.scheduler import PlayNote, play

GRID_CHOICES = (0.125, 0.25, 0.5, 1)


def analyze_midi(args: dict) -> dict:
    part = load_part(args.get("path"), args.get("track"))
    return analyze_part(part)


def _note_view(n) -> dict:
    return {"pitch": n.pitch, "beat": round(n.start, 4), "velocity": n.velocity}


def fix_midi(args: dict) -> dict:
    fix = args.get("fix")
    if fix not in ("snap_to_scale", "quantize", "humanize"):
        raise ToolError("OUT_OF_RANGE", f"fix must be snap_to_scale, quantize or humanize, got {fix!r}")
    grid = args.get("grid_beats")
    if grid is not None and grid not in GRID_CHOICES:
        raise ToolError("OUT_OF_RANGE", f"grid_beats must be one of {list(GRID_CHOICES)}, got {grid!r}")
    strength = float(args.get("strength_pct", 100))
    if not 0 <= strength <= 100:
        raise ToolError("OUT_OF_RANGE", f"strength_pct must be 0..100, got {strength}")

    part = load_part(args.get("path"))
    grid = float(grid) if grid is not None else infer_grid(part.notes)
    if fix == "snap_to_scale":
        if args.get("key"):
            tonic, mode = parse_key(args["key"])
        else:
            tonic, mode, _ = detect_key(part.notes)
        fixed = fixers.snap_to_scale(part.notes, tonic, mode)
    elif fix == "quantize":
        fixed = fixers.quantize(part.notes, grid, strength)
    else:
        fixed = fixers.humanize(part.notes, part.ms_per_beat)

    fixed = snap_to_resolution(fixed, written_resolution(part))
    before = snap_to_resolution(part.notes, written_resolution(part))
    changed = [
        {"index": i, "before": _note_view(b), "after": _note_view(a)}
        for i, (b, a) in enumerate(zip(before, fixed))
        if (b.pitch, b.start, b.velocity) != (a.pitch, a.start, a.velocity)
    ]

    stem, _ = os.path.splitext(part.path)
    fixed_path = save_part(Part(fixed, part.ppq, part.tempo_us), f"{stem}_{fix}.mid")
    script_path = pyscript.write(fixed, source=fixed_path, fallback_dir=os.path.dirname(fixed_path))
    reanalysis = analyze_part(load_part(fixed_path), grid_beats=grid)
    return {
        "fixed_mid_path": fixed_path,
        "pyscript_path": script_path,
        "changed": changed,
        "user_action": pyscript.USER_ACTION,
        "reanalysis": reanalysis,
    }


def _notes_from_args(args: dict) -> list[PlayNote]:
    has_path, has_notes = args.get("path") is not None, args.get("notes") is not None
    if has_path == has_notes:
        raise ToolError("OUT_OF_RANGE", "fl_play_midi needs exactly one of 'path' (a .mid file) or 'notes' (a list), not both or neither")
    if has_path:
        return [PlayNote(n.pitch, n.start, n.length, n.velocity) for n in load_part(args["path"]).notes]
    raw = args["notes"]
    if not isinstance(raw, list) or not raw:
        raise ToolError("OUT_OF_RANGE", "notes must be a non-empty list")
    if len(raw) > 5000:
        raise ToolError("OUT_OF_RANGE", f"at most 5000 notes, got {len(raw)}")
    out = []
    for i, n in enumerate(raw):
        try:
            pitch, start, length = int(n["pitch"]), float(n["start_beat"]), float(n["length_beats"])
            vel = int(n.get("velocity", 100))
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolError("OUT_OF_RANGE", f"notes[{i}] needs pitch, start_beat, length_beats: {exc}") from exc
        if not (0 <= pitch <= 127 and start >= 0 and length > 0 and 1 <= vel <= 127):
            raise ToolError("OUT_OF_RANGE", f"notes[{i}] out of range: {n}")
        out.append(PlayNote(pitch, start, length, vel))
    return out


def fl_play_midi(args: dict) -> dict:
    notes = _notes_from_args(args)
    channel = args.get("channel")
    if channel is not None and int(channel) < 0:
        raise ToolError("OUT_OF_RANGE", f"channel must be >= 0, got {channel}")
    start_bar = int(args.get("start_bar", 2))
    if start_bar < 2:
        raise ToolError("OUT_OF_RANGE", f"start_bar must be >= 2, got {start_bar}")
    from mixassist.fl import get_fl

    return play(get_fl(), notes, channel=None if channel is None else int(channel),
                record=bool(args.get("record", False)), start_bar=start_bar)
