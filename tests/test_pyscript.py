"""F7 acceptance: the generated MCP_Apply.pyscript compiles and writes exactly the fixed notes (ADR-2)."""
from __future__ import annotations

import py_compile
import shutil
import sys
import types
from pathlib import Path

import mido
import pytest

from mixassist.midi import pyscript
from mixassist.midi.io import Note
from mixassist.tools.midi_tools import fix_midi

ROOT = Path(__file__).resolve().parents[1]
MIDI_DIR = ROOT / "fixtures" / "midi"


class FakeNote:
    def __init__(self):
        self.number, self.time, self.length, self.velocity = 0, 0, 0, 0.8


class FakeScore:
    def __init__(self, ppq: int):
        self._ppq = ppq
        self.notes: list[FakeNote] = [FakeNote()]  # pre-existing note that must be cleared
        self.clear_calls: list[tuple] = []

    @property
    def PPQ(self):
        return self._ppq

    def clearNotes(self, *args, **kwargs):
        assert not kwargs, "clearNotes must be called positionally (FL 25.2.5)"
        self.clear_calls.append(args)
        self.notes.clear()

    def addNote(self, note):
        assert isinstance(note, FakeNote)
        self.notes.append(note)


def fake_flpianoroll(ppq: int) -> types.ModuleType:
    mod = types.ModuleType("flpianoroll")
    mod.Note = FakeNote
    mod.score = FakeScore(ppq)
    return mod


def run_script(path: str, ppq: int, monkeypatch) -> FakeScore:
    mod = fake_flpianoroll(ppq)
    monkeypatch.setitem(sys.modules, "flpianoroll", mod)
    src = Path(path).read_text(encoding="ascii")
    exec(compile(src, path, "exec"), {"__name__": "__main__"})
    return mod.score


def mid_notes(path: str, ppq: int) -> list[tuple[int, int, int, int]]:
    """(pitch, time, length, velocity) in `ppq` ticks from a .mid file."""
    mf = mido.MidiFile(path)
    scale = ppq / mf.ticks_per_beat
    out, tick, open_ = [], 0, {}
    for msg in mido.merge_tracks(mf.tracks):
        tick += msg.time
        if msg.type == "note_on" and msg.velocity:
            open_[msg.note] = (tick, msg.velocity)
        elif msg.type in ("note_off", "note_on") and msg.note in open_:
            on, vel = open_.pop(msg.note)
            out.append((msg.note, round(on * scale), max(1, round((tick - on) * scale)), vel))
    return sorted(out)


@pytest.fixture(autouse=True)
def pyscript_dir(tmp_path, monkeypatch):
    d = tmp_path / "Piano roll scripts"
    d.mkdir()
    monkeypatch.setenv(pyscript.PYSCRIPT_DIR_ENV, str(d))
    return d


@pytest.mark.parametrize("fix", ["snap_to_scale", "quantize", "humanize"])
@pytest.mark.parametrize("ppq", [96, 960])
def test_script_compiles_and_writes_fixed_notes(fix, ppq, tmp_path, pyscript_dir, monkeypatch):
    src = tmp_path / "melody_c_major_3wrong.mid"
    shutil.copy(MIDI_DIR / src.name, src)
    out = fix_midi({"path": str(src), "fix": fix})
    assert out["pyscript_path"] == str(pyscript_dir / "MCP_Apply.pyscript")
    py_compile.compile(out["pyscript_path"], cfile=str(tmp_path / "x.pyc"), doraise=True)

    score = run_script(out["pyscript_path"], ppq, monkeypatch)
    assert score.clear_calls == [(True,)]
    written = sorted((n.number, n.time, n.length, round(n.velocity * 127)) for n in score.notes)
    assert written == mid_notes(out["fixed_mid_path"], ppq)
    assert len(written) == 32


def test_script_is_ascii_and_has_fl_header():
    text = pyscript.render([Note(60, 0.0, 1.0, 100)], source="mélodie.mid")
    text.encode("ascii")
    assert text.startswith('# Script.Name = "MCP Apply"')
    assert "score.clearNotes(True)" in text and "score.addNote(n)" in text and "score.PPQ" in text


def test_velocity_maps_to_unit_range(monkeypatch, tmp_path):
    path = tmp_path / "s.pyscript"
    path.write_text(pyscript.render([Note(60, 0, 1, 127), Note(62, 1, 0.5, 1)]), encoding="ascii")
    score = run_script(str(path), 96, monkeypatch)
    assert [(n.number, n.time, n.length) for n in score.notes] == [(60, 0, 96), (62, 96, 48)]
    assert score.notes[0].velocity == 1.0
    assert 0 < score.notes[1].velocity < 0.01


def test_default_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(pyscript.PYSCRIPT_DIR_ENV, str(tmp_path / "custom"))
    assert pyscript.default_script_dir() == str(tmp_path / "custom")
    monkeypatch.delenv(pyscript.PYSCRIPT_DIR_ENV)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert pyscript.default_script_dir() is None
    fl_dir = tmp_path / "Documents" / "Image-Line" / "FL Studio" / "Settings" / "Piano roll scripts"
    fl_dir.mkdir(parents=True)
    assert pyscript.default_script_dir() == str(fl_dir)


def test_falls_back_next_to_fixed_file(monkeypatch, tmp_path):
    monkeypatch.delenv(pyscript.PYSCRIPT_DIR_ENV)
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "nohome"))
    path = pyscript.write([Note(60, 0, 1, 100)], fallback_dir=str(tmp_path))
    assert path == str(tmp_path / "MCP_Apply.pyscript")
