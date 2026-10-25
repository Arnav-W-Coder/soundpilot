"""F2 acceptance (spec §6): change by name, readback, undo."""
import pytest

from mixassist.fl import curves
from mixassist.tools import call_tool

from conftest import install_fake


def _kick(fake):
    return curves.interp(fake.vol_curve, fake.tracks[1]["vol"])


def test_kick_down_2db_and_undo(fake_fl):
    out = call_tool("fl_set_track", {"track": "Kick", "volume_change_db": -2})
    assert "error" not in out, out
    assert out["track"] == {"index": 1, "name": "Kick"}
    assert out["before"]["volume_db"] == pytest.approx(0.0, abs=0.1)
    assert out["after"]["volume_db"] == pytest.approx(-2.0, abs=0.25)
    assert out["verified"] is True
    assert out["change_id"]
    undo = call_tool("fl_undo", {})
    assert undo["change_id"] == out["change_id"]
    assert undo["restored"]["volume_db"] == pytest.approx(0.0, abs=0.1)
    assert undo["verified"] is True
    assert _kick(fake_fl) == pytest.approx(0.0, abs=0.1)


def test_name_matching_is_case_insensitive_and_prefix():
    assert call_tool("fl_set_track", {"track": "kIcK", "pan_pct": 10})["track"]["name"] == "Kick"
    assert call_tool("fl_set_track", {"track": "lead", "pan_pct": 10})["track"]["name"] == "Lead Synth"
    assert call_tool("fl_set_track", {"track": "1", "pan_pct": 5})["track"]["index"] == 1  # numeric string coerced


def test_ambiguous_hat():
    out = call_tool("fl_set_track", {"track": "hat", "pan_pct": -30})
    assert out["error"]["code"] == "AMBIGUOUS_TARGET"
    assert set(out["error"]["candidates"]) == {"Hat Open", "Hat Closed"}


def test_pan_readback():
    out = call_tool("fl_set_track", {"track": "Snare", "pan_pct": -30})
    assert out["after"]["pan_pct"] == pytest.approx(-30, abs=1)
    assert out["verified"]


def test_volume_db_out_of_range():
    out = call_tool("fl_set_track", {"track": "Kick", "volume_db": 9})
    assert out["error"]["code"] == "OUT_OF_RANGE"
    out = call_tool("fl_set_track", {"track": "Kick", "volume_change_db": 7})  # 0 + 7 > fader max (P3 placeholder)
    assert out["error"]["code"] == "OUT_OF_RANGE"


def test_readback_search_handles_curve_mismatch():
    """The fake's real fader curve differs from the server's table; readback search still converges."""
    from mixassist.fl.fake import demo_tracks

    skewed = [(x, y * 1.15 if y < 0 else y * 0.9) for x, y in curves.VOLUME_TABLE]
    fake, _ = install_fake(tracks=demo_tracks(), vol_curve=skewed)
    out = call_tool("fl_set_track", {"track": "Kick", "volume_change_db": -2})
    assert out["after"]["volume_db"] == pytest.approx(-2.0, abs=0.25)
    assert out["verified"] and 1 < out["round_trips"] <= 8
    assert curves.interp(skewed, fake.tracks[1]["vol"]) == pytest.approx(-2.0, abs=0.25)


def test_absolute_volume_and_width():
    out = call_tool("fl_set_track", {"track": "Pad", "volume_db": -6, "stereo_width_pct": 20})
    assert out["after"]["volume_db"] == pytest.approx(-6, abs=0.25)
    assert out["after"]["stereo_width_pct"] == pytest.approx(20, abs=1)
    assert out["verified"]


def test_eq_change_and_undo():
    out = call_tool("fl_set_track", {"track": 0, "eq_band": "high", "eq_gain_db": -5, "eq_freq_hz": 8000})
    assert "error" not in out, out
    assert out["after"]["eq_gain_db"] == pytest.approx(-5, abs=0.25)
    assert out["after"]["eq_freq_hz"] == pytest.approx(8000, rel=0.02)
    assert out["before"]["eq_band"] == "high"
    undo = call_tool("fl_undo", {"change_id": out["change_id"]})
    assert undo["restored"]["eq_gain_db"] == pytest.approx(out["before"]["eq_gain_db"], abs=0.1)
    assert undo["verified"]


def test_undo_errors():
    assert call_tool("fl_undo", {})["error"]["code"] == "NOT_FOUND"
    call_tool("fl_set_track", {"track": "Kick", "volume_change_db": -1})
    assert call_tool("fl_undo", {"change_id": "c_missing"})["error"]["code"] == "NOT_FOUND"
    assert "error" not in call_tool("fl_undo", {})
    assert call_tool("fl_undo", {})["error"]["code"] == "NOT_FOUND"


def test_undo_restores_stored_value_after_intervening_edit(fake_fl):
    first = call_tool("fl_set_track", {"track": "Bass", "volume_change_db": -3})
    call_tool("fl_set_track", {"track": "Bass", "volume_change_db": -3})
    undo = call_tool("fl_undo", {"change_id": first["change_id"]})
    assert undo["restored"]["volume_db"] == pytest.approx(first["before"]["volume_db"], abs=0.1)


@pytest.mark.parametrize(
    "args",
    [
        {"track": "Kick"},
        {"track": "Kick", "volume_db": -3, "volume_change_db": -1},
        {"track": "Kick", "eq_gain_db": -3},
        {"track": "Kick", "eq_band": "mid"},
        {"track": "Nope", "pan_pct": 0},
        {"track": 999, "pan_pct": 0},
        {"track": "Kick", "volume": "-2"},
    ],
)
def test_bad_requests_return_errors(args):
    out = call_tool("fl_set_track", args)
    assert set(out) == {"error"} and out["error"]["code"] in {"OUT_OF_RANGE", "NOT_FOUND"}


def test_writes_use_pickup_mode_zero(fake_fl):
    """With a non-zero pickup the fake would ignore a 0.8 -> 0.7 jump; the tool always sends pk=0."""
    out = call_tool("fl_set_track", {"track": "Kick", "volume_change_db": -6})
    assert out["after"]["volume_db"] == pytest.approx(-6, abs=0.25)


@pytest.mark.realfl
@pytest.mark.skip(reason="PROBE: needs real FL (P3/P5); see internal/probes.md")
def test_real_fl_readback():
    pass
