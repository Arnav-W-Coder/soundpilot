"""Spec §3.2 input schemas (verbatim, frozen) and tool descriptions for MCP and the LLM."""
from __future__ import annotations

import copy

T = {"oneOf": [{"type": "integer", "minimum": 0}, {"type": "string", "minLength": 1}]}

SCHEMAS: dict[str, dict] = {
    "fl_get_mix_overview": {
        "type": "object",
        "properties": {"include_empty": {"type": "boolean", "default": False}},
    },
    "fl_set_track": {
        "type": "object",
        "required": ["track"],
        "properties": {
            "track": T,
            "volume_db": {"type": "number", "minimum": -60, "maximum": 5},
            "volume_change_db": {"type": "number", "minimum": -24, "maximum": 12},
            "pan_pct": {"type": "number", "minimum": -100, "maximum": 100},
            "stereo_width_pct": {"type": "number", "minimum": -100, "maximum": 100},
            "eq_band": {"enum": ["low", "mid", "high"]},
            "eq_gain_db": {"type": "number", "minimum": -12, "maximum": 12},
            "eq_freq_hz": {"type": "number", "minimum": 20, "maximum": 20000},
        },
    },
    "fl_undo": {
        "type": "object",
        "properties": {"change_id": {"type": "string"}},
    },
    "fl_watch_peaks": {
        "type": "object",
        "properties": {
            "seconds": {"type": "number", "minimum": 2, "maximum": 300, "default": 30},
            "start_playback": {"type": "boolean", "default": True},
            "from_bar": {"type": "integer", "minimum": 1, "default": 1},
        },
    },
    "analyze_audio": {
        "type": "object",
        "required": ["path"],
        "properties": {"path": {"type": "string"}, "reference_path": {"type": "string"}},
    },
    "diagnose_complaint": {
        "type": "object",
        "required": ["complaint", "analysis_id"],
        "properties": {"complaint": {"type": "string"}, "analysis_id": {"type": "string"}, "track": T},
    },
    "verify_fix": {
        "type": "object",
        "required": ["complaint", "before_analysis_id", "after_path"],
        "properties": {
            "complaint": {"type": "string"},
            "before_analysis_id": {"type": "string"},
            "after_path": {"type": "string"},
        },
    },
    "analyze_midi": {
        "type": "object",
        "required": ["path"],
        "properties": {"path": {"type": "string"}, "track": {"type": "integer", "minimum": 0}},
    },
    "fix_midi": {
        "type": "object",
        "required": ["path", "fix"],
        "properties": {
            "path": {"type": "string"},
            "fix": {"enum": ["snap_to_scale", "quantize", "humanize"]},
            "key": {"type": "string", "pattern": "^[A-G][#b]? (major|minor)$"},
            "grid_beats": {"enum": [0.125, 0.25, 0.5, 1]},
            "strength_pct": {"type": "number", "minimum": 0, "maximum": 100, "default": 100},
        },
    },
    "fl_play_midi": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "notes": {
                "type": "array",
                "maxItems": 5000,
                "items": {
                    "type": "object",
                    "required": ["pitch", "start_beat", "length_beats"],
                    "properties": {
                        "pitch": {"type": "integer", "minimum": 0, "maximum": 127},
                        "start_beat": {"type": "number", "minimum": 0},
                        "length_beats": {"type": "number", "exclusiveMinimum": 0},
                        "velocity": {"type": "integer", "minimum": 1, "maximum": 127, "default": 100},
                    },
                },
            },
            "channel": {"type": "integer", "minimum": 0},
            "record": {"type": "boolean", "default": False},
            "start_bar": {"type": "integer", "minimum": 2, "default": 2},
        },
        "oneOf": [{"required": ["path"]}, {"required": ["notes"]}],
    },
}

TOOL_NAMES: tuple[str, ...] = tuple(SCHEMAS)

DESCRIPTIONS: dict[str, str] = {
    "fl_get_mix_overview": (
        "Read the FL Studio mixer: tempo (bpm) and every used mixer track (index 0 = master) with "
        "volume_db (dB), pan_pct (-100 left .. 100 right), stereo_width_pct, muted, solo, the built-in "
        "3-band EQ (band low/mid/high, gain_db, freq_hz) and plugin names. Read-only. Set include_empty=true "
        "to also list unused default 'Insert N' tracks."
    ),
    "fl_set_track": (
        "Change ONE mixer track (needs user approval). Arguments: track (track index or name, e.g. 'Kick' or 0 "
        "for master); then any of: volume_change_db (relative change in dB, e.g. -2 for 'down 2 dB'), "
        "volume_db (absolute level in dB, -60..5), pan_pct (-100 = hard left, 100 = hard right, 0 = centre), "
        "stereo_width_pct (-100..100), and for the built-in EQ: eq_band ('low'|'mid'|'high') with eq_gain_db "
        "(-12..12 dB) and/or eq_freq_hz (20..20000 Hz). Use ONLY these argument names; numbers must be numbers. "
        "Returns before/after readback and verified."
    ),
    "fl_undo": (
        "Undo a change made by fl_set_track (needs user approval). Optional change_id; default is the last change. "
        "Restores the stored values and returns the readback."
    ),
    "fl_watch_peaks": (
        "Play the song and watch mixer peak meters to find clipping or tracks running too hot. Arguments: "
        "seconds (2..300, default 30), start_playback (default true), from_bar (default 1). Returns per-track "
        "max_peak_dbfs and clip_count, the clipping track indices, and suggested volume_change_db trims."
    ),
    "analyze_audio": (
        "Measure an exported WAV. Required: path (absolute path of the mix WAV). Optional: reference_path "
        "(absolute path of a reference track to compare against). Returns integrated/short-term loudness (LUFS), "
        "loudness range (LU), true peak (dBTP), sample peak (dBFS), crest (dB), stereo correlation, spectral "
        "centroid (Hz), 7 band levels (dB) and an analysis_id to pass to diagnose_complaint or verify_fix."
    ),
    "diagnose_complaint": (
        "Turn a plain-language complaint about the mix (e.g. 'too bright', 'muddy', 'sounds bad') into measured "
        "findings and proposed fl_set_track changes. Required: complaint (the user's words, e.g. 'too bright') "
        "AND analysis_id (from analyze_audio). Optional: track (track index or name). Prefer this tool for vague "
        "complaints. If it returns needs_clarification=true, ask the user its question and options and propose "
        "no changes."
    ),
    "verify_fix": (
        "Check whether a fix worked. Required: complaint (the original complaint, e.g. 'too bright'), "
        "before_analysis_id (the analysis_id of the mix before the fix) AND after_path (absolute path of the "
        "re-exported WAV). Returns passed and per-check before/after values."
    ),
    "analyze_midi": (
        "Analyze an exported MIDI file. Required: path (absolute .mid path). Optional: track (MIDI track number, "
        "integer). Returns the detected key and confidence, out-of-key notes, timing offsets from the grid (ms), "
        "velocity spread and flags such as 'robotic'."
    ),
    "fix_midi": (
        "Fix a MIDI file (needs user approval). Required: path (absolute .mid path) and fix, one of "
        "'snap_to_scale' (move out-of-key notes into the "
        "key), 'quantize' (snap timing to grid_beats: 0.125, 0.25, 0.5 or 1 beats) or 'humanize' (add small "
        "timing/velocity variation). Optional key like 'C major' or 'A minor', strength_pct 0..100. Writes a new "
        ".mid and a piano-roll script the user applies in FL."
    ),
    "fl_play_midi": (
        "Play a MIDI phrase on the selected FL instrument. Give EITHER path (absolute .mid path) OR notes "
        "(list of {pitch 0..127, start_beat, length_beats, velocity}), never both. Optional channel (global "
        "channel index). record=true records into the piano roll starting at start_bar (>= 2) and needs user "
        "approval; record=false just plays it live."
    ),
}


def schema(name: str) -> dict:
    return copy.deepcopy(SCHEMAS[name])
