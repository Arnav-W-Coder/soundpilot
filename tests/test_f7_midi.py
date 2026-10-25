"""F7 acceptance: key and wrong notes, snap_to_scale, robotic flag, humanize, quantize (spec §6 F7, §7)."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import mido
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fixtures import make_midi  # noqa: E402
from mixassist.errors import ToolError  # noqa: E402
from mixassist.midi.analysis import detect_key, parse_key  # noqa: E402
from mixassist.midi.fixers import snap_to_scale  # noqa: E402
from mixassist.midi.io import Note, load_part  # noqa: E402
from mixassist.tools.midi_tools import analyze_midi, fix_midi  # noqa: E402

MIDI_DIR = ROOT / "fixtures" / "midi"
FIXTURES = ("melody_c_major_3wrong.mid", "robotic.mid", "sloppy.mid", "scale_8.mid", "melody.mid")


@pytest.fixture(autouse=True)
def pyscript_dir(tmp_path, monkeypatch):
    d = tmp_path / "pyscripts"
    d.mkdir()
    monkeypatch.setenv("SOUNDPILOT_PYSCRIPT_DIR", str(d))
    return d


@pytest.fixture
def mid(tmp_path):
    """Copy a committed fixture into tmp so fix_midi's outputs stay out of the repo."""
    def _copy(name: str) -> str:
        dst = tmp_path / name
        shutil.copy(MIDI_DIR / name, dst)
        return str(dst)
    return _copy


def test_committed_fixtures_match_generator(tmp_path):
    make_midi.make_all(tmp_path)
    for name in FIXTURES:
        assert (MIDI_DIR / name).read_bytes() == (tmp_path / name).read_bytes(), name


def test_melody_has_32_notes_and_three_planted_wrong_notes():
    assert len(make_midi.MELODY_C_MAJOR_3WRONG) == 32
    pcs = sorted(make_midi.MELODY_C_MAJOR_3WRONG[i][0] % 12 for i in make_midi.PLANTED_WRONG)
    assert pcs == [1, 6, 8]  # C#, F#, G#


def test_key_and_out_of_key(mid):
    out = analyze_midi({"path": mid("melody_c_major_3wrong.mid")})
    assert (out["key"]["tonic"], out["key"]["mode"]) in {("C", "major"), ("A", "minor")}
    assert 0 < out["key"]["confidence"] <= 1
    assert out["note_count"] == 32
    assert [o["index"] for o in out["out_of_key"]] == list(make_midi.PLANTED_WRONG)
    assert {o["pitch"] % 12 for o in out["out_of_key"]} == {1, 6, 8}
    assert "out_of_key" in out["flags"]
    for field in ("timing", "velocity", "density_notes_per_beat", "flags"):
        assert field in out


def test_snap_to_scale_fixes_exactly_the_planted_notes(mid, pyscript_dir):
    out = fix_midi({"path": mid("melody_c_major_3wrong.mid"), "fix": "snap_to_scale"})
    assert out["reanalysis"]["out_of_key"] == []
    assert [c["index"] for c in out["changed"]] == list(make_midi.PLANTED_WRONG)
    for c in out["changed"]:
        assert abs(c["after"]["pitch"] - c["before"]["pitch"]) == 1
        assert c["after"]["beat"] == c["before"]["beat"]
        assert c["after"]["velocity"] == c["before"]["velocity"]
    assert Path(out["fixed_mid_path"]).is_file()
    assert Path(out["pyscript_path"]).parent == pyscript_dir
    assert out["user_action"] == (
        "In FL: open this channel's piano roll → Scripting → MCP Apply (first time this session), "
        "afterwards press Ctrl+Alt+Y."
    )


def test_snap_tie_follows_melodic_direction():
    c_major = (0, "major")
    up = snap_to_scale([Note(60, 0, 1, 100), Note(66, 1, 1, 100)], *c_major)
    down = snap_to_scale([Note(72, 0, 1, 100), Note(66, 1, 1, 100)], *c_major)
    assert up[1].pitch == 67 and down[1].pitch == 65
    assert snap_to_scale([Note(61, 0, 1, 100)], *c_major)[0].pitch == 60  # first note ties down


def test_snap_with_explicit_key(mid):
    out = fix_midi({"path": mid("melody_c_major_3wrong.mid"), "fix": "snap_to_scale", "key": "A minor"})
    assert out["reanalysis"]["out_of_key"] == []
    assert len(out["changed"]) == 3


def test_robotic_flag(mid):
    out = analyze_midi({"path": mid("robotic.mid")})
    assert "robotic" in out["flags"]
    assert out["velocity"]["std"] < 3 and out["timing"]["median_offset_ms"] < 1
    assert "robotic" not in analyze_midi({"path": mid("melody_c_major_3wrong.mid")})["flags"]


def test_humanize(mid):
    out = fix_midi({"path": mid("robotic.mid"), "fix": "humanize"})
    re = out["reanalysis"]
    assert re["velocity"]["std"] >= 6
    assert 2 <= re["timing"]["median_offset_ms"] <= 10
    assert re["timing"]["max_offset_ms"] <= 10.6  # 10 ms plus one output tick of rounding
    assert "robotic" not in re["flags"]
    again = fix_midi({"path": mid("robotic.mid"), "fix": "humanize"})
    assert again["changed"] == out["changed"]  # seed 7 is fixed


def test_sloppy_quantize_100(mid):
    before = analyze_midi({"path": mid("sloppy.mid")})
    assert before["timing"]["grid_beats"] == 0.5
    assert 10 < before["timing"]["median_offset_ms"] <= 42
    out = fix_midi({"path": mid("sloppy.mid"), "fix": "quantize", "strength_pct": 100})
    fixed = mido.MidiFile(out["fixed_mid_path"])
    ms_per_tick = 500.0 / fixed.ticks_per_beat
    assert out["reanalysis"]["timing"]["median_offset_ms"] <= ms_per_tick
    source_tick_ms = 500.0 / mido.MidiFile(mid("sloppy.mid")).ticks_per_beat
    assert out["reanalysis"]["timing"]["median_offset_ms"] <= source_tick_ms


def test_quantize_strength_50_halves_offsets(mid):
    path = mid("sloppy.mid")
    before = np.array([n.start for n in load_part(path).notes])
    out = fix_midi({"path": path, "fix": "quantize", "strength_pct": 50, "grid_beats": 0.5})
    after = np.array([n.start for n in load_part(out["fixed_mid_path"]).notes])
    grid = np.round(before / 0.5) * 0.5
    np.testing.assert_allclose(np.abs(after - grid), np.abs(before - grid) / 2, atol=1 / 960)


def test_parse_key():
    assert parse_key("C major") == (0, "major")
    assert parse_key("F# minor") == (6, "minor")
    assert parse_key("Bb major") == (10, "major")
    with pytest.raises(ToolError) as e:
        parse_key("H major")
    assert e.value.code == "OUT_OF_RANGE"


def test_detect_key_transposes():
    notes = load_part(str(MIDI_DIR / "robotic.mid")).notes
    up2 = [n.moved(pitch=n.pitch + 2) for n in notes]
    assert detect_key(up2)[:2] == (2, "major")


@pytest.mark.parametrize("args,code", [
    ({"path": "fixtures/midi/robotic.mid"}, "FILE_ERROR"),
    ({"path": "/nonexistent/x.mid"}, "FILE_ERROR"),
    ({"path": str(MIDI_DIR / "robotic.mid"), "track": 9}, "NOT_FOUND"),
])
def test_analyze_errors(args, code):
    with pytest.raises(ToolError) as e:
        analyze_midi(args)
    assert e.value.code == code


def test_analyze_track_selection():
    assert analyze_midi({"path": str(MIDI_DIR / "robotic.mid"), "track": 1})["note_count"] == 32
    with pytest.raises(ToolError) as e:
        analyze_midi({"path": str(MIDI_DIR / "robotic.mid"), "track": 0})  # tempo track only
    assert e.value.code == "NOT_FOUND"


def test_garbage_file_is_file_error(tmp_path):
    bad = tmp_path / "bad.mid"
    bad.write_bytes(b"not a midi file")
    with pytest.raises(ToolError) as e:
        analyze_midi({"path": str(bad)})
    assert e.value.code == "FILE_ERROR"


def test_fix_midi_bad_args(mid):
    with pytest.raises(ToolError) as e:
        fix_midi({"path": mid("robotic.mid"), "fix": "quantize", "grid_beats": 0.3})
    assert e.value.code == "OUT_OF_RANGE"
    with pytest.raises(ToolError) as e:
        fix_midi({"path": mid("robotic.mid"), "fix": "snap_to_scale", "key": "C dorian"})
    assert e.value.code == "OUT_OF_RANGE"
