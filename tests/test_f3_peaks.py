"""F3 acceptance (spec §6) on FakeFL with the clip_track peak script (§7)."""
import pytest

from mixassist.fl.fake import VirtualClock, demo_tracks
from mixassist.tools import call_tool

from conftest import install_fake

CLIP_SCRIPT = {
    0: [(0.0, 0.6)],
    1: [(0.0, 0.3), (4.0, 1.12), (4.2, 0.3)],  # clip_track: 200 ms burst at 1.12 (+0.98 dBFS)
    2: [(0.0, 0.5)],  # -6.02 dBFS: not flagged
}


def _watch(script, seconds=10.0):
    clock = VirtualClock()
    fake, _ = install_fake(tracks=demo_tracks(), peak_script=script, clock=clock)
    return fake, call_tool("fl_watch_peaks", {"seconds": seconds})


def test_clip_burst_detected():
    fake, out = _watch(CLIP_SCRIPT)
    assert "error" not in out, out
    assert out["poll_interval_ms"] == pytest.approx(50, abs=1)
    assert out["polls"] == pytest.approx(200, abs=2)
    assert 1 in out["clipping"]
    kick = next(t for t in out["tracks"] if t["index"] == 1)
    assert kick["max_peak_dbfs"] >= 0.9
    assert kick["clip_count"] >= 1
    assert not fake.playing  # transport stopped afterwards


def test_half_scale_not_flagged():
    _, out = _watch(CLIP_SCRIPT)
    snare = next(t for t in out["tracks"] if t["index"] == 2)
    assert snare["max_peak_dbfs"] == pytest.approx(-6.02, abs=0.01)
    assert 2 not in out["clipping"]
    assert all(s["track"] != 2 for s in out["suggestions"])


def test_suggestion_is_minus_7():
    _, out = _watch(CLIP_SCRIPT)
    s = next(s for s in out["suggestions"] if s["track"] == 1)
    assert s["volume_change_db"] == -7.0
    assert s["reason"]


def test_hot_master_trims_contributors_not_master():
    script = {0: [(0.0, 0.95)], 1: [(0.0, 0.7)], 2: [(0.0, 0.6)], 3: [(0.0, 0.2)]}
    _, out = _watch(script, seconds=2)
    targets = [s["track"] for s in out["suggestions"]]
    assert 0 not in targets
    assert targets[:2] == [1, 2]
    assert all(s["volume_change_db"] < 0 for s in out["suggestions"])


def test_short_transient_between_polls_can_be_missed_and_output_says_so():
    script = {1: [(0.0, 0.3), (1.01, 1.5), (1.02, 0.3)]}  # 10 ms spike between 50 ms polls
    _, out = _watch(script, seconds=2)
    assert 1 not in out["clipping"]
    assert "missed" in out["note"]


def test_from_bar_and_no_playback(fake_fl):
    clock = VirtualClock()
    fake, _ = install_fake(tracks=demo_tracks(), peak_script=CLIP_SCRIPT, clock=clock)
    out = call_tool("fl_watch_peaks", {"seconds": 2, "start_playback": False})
    assert out["warnings"] and out["clipping"] == []
    assert call_tool("fl_watch_peaks", {"seconds": 1})["error"]["code"] == "OUT_OF_RANGE"


@pytest.mark.realfl
@pytest.mark.skip(reason="PROBE: needs real FL (P6); see internal/probes.md")
def test_real_fl_peaks():
    pass
