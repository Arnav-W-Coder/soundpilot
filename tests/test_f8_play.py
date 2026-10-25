"""F8 acceptance on FakeFL: record/live scheduling, clock sync, cleanup and warnings (spec §6 F8)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mixassist.errors import ToolError
from mixassist.fl import set_fl
from mixassist.fl.client import FLClient
from mixassist.fl.fake import FakeTransport, VirtualClock, default_fake
from mixassist.midi.scheduler import FOCUS_WARNING, PATTERN_MODE_WARNING, PlayNote, play, precise_sleep_until
from mixassist.tools.midi_tools import fl_play_midi

ROOT = Path(__file__).resolve().parents[1]
SCALE_8 = str(ROOT / "fixtures" / "midi" / "scale_8.mid")
BPM, PPQ = 120.0, 96


def client(**fake_kw) -> tuple[FLClient, object]:
    fake = default_fake(bpm=BPM, ppq=PPQ, **fake_kw)
    return FLClient(FakeTransport(fake), "fake"), fake


@pytest.fixture
def active_fake():
    """Install a virtual-clock FakeFL as the process-wide client for tool-level tests."""
    fl, fake = client(clock=VirtualClock())
    set_fl(fl)
    yield fake
    set_fl(None)


def target_ticks(notes: list[PlayNote], start_bar: int) -> np.ndarray:
    return np.array([(start_bar - 1) * 4 * PPQ + n.start_beat * PPQ for n in notes])


def test_record_64_notes_within_10ms_p95():
    fl, fake = client()  # real time.monotonic clock with the 5 ms reporting lag
    pitches = [60, 62, 64, 65, 67, 69, 71, 72]
    notes = [PlayNote(pitches[i % 8], i * 0.125, 0.1, 90 + i % 20) for i in range(64)]
    out = play(fl, notes, channel=4, record=True, start_bar=3)

    rec = fake.recorded_notes
    assert len(rec) == 64, "no note may be missing"
    assert [r["pitch"] for r in rec] == [n.pitch for n in notes]
    assert [r["velocity"] for r in rec] == [n.velocity for n in notes]
    err_ms = (np.array([r["tick"] for r in rec]) - target_ticks(notes, 3)) / (PPQ * BPM / 60) * 1000
    p95 = float(np.percentile(np.abs(err_ms), 95))
    print(f"F8 record timing vs FL ticks: p95 |err| = {p95:.2f} ms, mean {err_ms.mean():+.2f} ms, max {np.abs(err_ms).max():.2f} ms; "
          f"scheduler lateness {out['timing_lateness_ms']}")
    assert p95 <= 10.0

    assert fake.sounding_notes == set()
    assert not fake.playing and not fake.recording
    assert fake.selected_channel == 4
    assert out["mode"] == "record" and out["notes_sent"] == 64 and out["bpm"] == BPM
    assert out["channel"] == {"index": 4, "name": fake.channels[4]}
    assert set(out["timing_lateness_ms"]) == {"mean", "p95", "max"}
    assert PATTERN_MODE_WARNING not in out["warnings"]


def test_record_sequence_of_transport_ops():
    fl, fake = client(clock=VirtualClock())
    play(fl, [PlayNote(60, 0, 1)], record=True, start_bar=5)
    assert fake.ops[:3] == ["transport"] * 3
    assert fake.recorded_notes[0]["tick"] == pytest.approx(4 * 4 * PPQ, abs=1.5)
    assert len(fake.recorded_offs) == 1
    assert not fake.playing and not fake.recording


def test_virtual_clock_record_is_exact_up_to_report_lag():
    fl, fake = client(clock=VirtualClock())
    notes = [PlayNote(60 + i % 12, i * 0.25, 0.2) for i in range(32)]
    play(fl, notes, record=True)
    ticks = np.array([r["tick"] for r in fake.recorded_notes])
    err_ms = (ticks - target_ticks(notes, 2)) / (PPQ * BPM / 60) * 1000
    assert np.all(np.abs(err_ms) <= 10.0)


def test_pattern_mode_warns():
    fl, fake = client(clock=VirtualClock(), loop_mode=0)
    out = play(fl, [PlayNote(60, 0, 0.5)], record=True)
    assert PATTERN_MODE_WARNING in out["warnings"]
    assert FOCUS_WARNING in out["warnings"]
    out = play(fl, [PlayNote(60, 0, 0.5)])
    assert PATTERN_MODE_WARNING in out["warnings"]


def test_live_mode_sends_on_off_and_releases():
    fl, fake = client()
    notes = [PlayNote(60 + i, i * 0.0625, 0.05) for i in range(8)] + [PlayNote(48, 0, 1.0)]
    sent = []
    real_note = fake.note
    fake.note = lambda s, d1, d2, t: (sent.append((s, d1, d2, t)), real_note(s, d1, d2, t))
    out = play(fl, notes)
    assert out["mode"] == "live" and out["notes_sent"] == 9
    assert fake.recorded_notes == []  # not recording
    assert fake.sounding_notes == set()
    assert sent[-1][:3] == (0xB0, 123, 0)
    assert out["timing_lateness_ms"]["p95"] < 5.0
    assert out["channel"]["index"] is None


def test_overlapping_same_pitch_does_not_leave_notes_on():
    fl, fake = client(clock=VirtualClock())
    play(fl, [PlayNote(60, 0, 1.0), PlayNote(60, 0.5, 1.0), PlayNote(62, 0.25, 4.0)], record=True)
    assert fake.sounding_notes == set()
    assert len(fake.recorded_notes) == 3


def test_precise_sleep_virtual_clock_does_not_hang():
    clock = VirtualClock()
    precise_sleep_until(clock() + 0.5, clock, clock.advance)
    assert clock() == pytest.approx(1000.5, abs=1e-9)


def test_tool_path_and_channel(active_fake):
    out = fl_play_midi({"path": SCALE_8, "channel": 3, "record": True, "start_bar": 2})
    assert out["notes_sent"] == 8 and out["channel"]["name"] == active_fake.channels[3]
    assert [r["pitch"] for r in active_fake.recorded_notes] == [60, 62, 64, 65, 67, 69, 71, 72]
    assert active_fake.sounding_notes == set()


def test_tool_inline_notes_default_velocity(active_fake):
    out = fl_play_midi({"notes": [{"pitch": 64, "start_beat": 0, "length_beats": 0.5}], "record": True})
    assert out["notes_sent"] == 1 and active_fake.recorded_notes[0]["velocity"] == 100


@pytest.mark.parametrize("args", [
    {},
    {"path": SCALE_8, "notes": [{"pitch": 60, "start_beat": 0, "length_beats": 1}]},
    {"notes": []},
    {"notes": [{"pitch": 128, "start_beat": 0, "length_beats": 1}]},
    {"notes": [{"pitch": 60, "start_beat": 0, "length_beats": 0}]},
    {"path": SCALE_8, "start_bar": 1, "record": True},
])
def test_tool_rejects_bad_args(active_fake, args):
    with pytest.raises(ToolError) as e:
        fl_play_midi(args)
    assert e.value.code == "OUT_OF_RANGE"
    assert active_fake.recorded_notes == []


def test_tool_unknown_channel(active_fake):
    with pytest.raises(ToolError) as e:
        fl_play_midi({"path": SCALE_8, "channel": 99})
    assert e.value.code == "NOT_FOUND"


def test_dispatcher_enforces_one_of(active_fake):
    from mixassist.tools import call_tool

    res = call_tool("fl_play_midi", {"path": SCALE_8, "notes": [{"pitch": 60, "start_beat": 0, "length_beats": 1}]})
    assert res["error"]["code"] == "OUT_OF_RANGE"
    ok = call_tool("fl_play_midi", {"path": SCALE_8, "record": True})
    assert ok["notes_sent"] == 8


@pytest.mark.realfl
@pytest.mark.skip(reason="PROBE P8: needs real FL with the MixAssist device script (internal/probes.md)")
def test_p8_real_fl_scale_record_within_20ms():
    """Record scale_8.mid at bar 2 into a real FL channel, export as .mid; onsets within ±20 ms of the grid at p95."""


@pytest.mark.realfl
@pytest.mark.skip(reason="PROBE P8: needs real FL; repeat with FL unfocused and note the result")
def test_p8_real_fl_unfocused():
    """Same as above with another app focused; record whether notes still arrive."""
