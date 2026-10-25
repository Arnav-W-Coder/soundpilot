"""F1 acceptance (spec §6)."""
import logging
import time

import pytest

from mixassist.fl import curves
from mixassist.tools import call_tool

from conftest import install_fake


def test_overview_returns_master_plus_12(fake_fl):
    t0 = time.perf_counter()
    out = call_tool("fl_get_mix_overview", {"include_empty": False})
    elapsed = time.perf_counter() - t0
    assert "error" not in out
    assert elapsed < 0.5
    assert len(out["tracks"]) == 13
    assert [t["index"] for t in out["tracks"]] == list(range(13))
    for t in out["tracks"]:
        expected = curves.interp(fake_fl.vol_curve, fake_fl.tracks[t["index"]]["vol"])
        assert t["volume_db"] == pytest.approx(expected, abs=0.1)
    assert out["fl_api"] == 36 and out["bpm"] == 120.0


def test_overview_track_shape_and_units():
    out = call_tool("fl_get_mix_overview", {})
    kick = next(t for t in out["tracks"] if t["name"] == "Kick")
    assert set(kick) == {"index", "name", "volume_db", "pan_pct", "stereo_width_pct", "muted", "solo", "eq", "plugins"}
    assert kick["volume_db"] == pytest.approx(0.0, abs=0.1)
    assert [b["band"] for b in kick["eq"]] == ["low", "mid", "high"]
    assert kick["plugins"][0] == {"slot": 0, "name": "Fruity Parametric EQ 2"}
    hat = next(t for t in out["tracks"] if t["name"] == "Hat Open")
    assert hat["pan_pct"] == pytest.approx(30.0)
    bass = next(t for t in out["tracks"] if t["name"] == "Bass")
    assert bass["eq"][0]["gain_db"] == pytest.approx(2.0, abs=0.05) and bass["eq"][0]["freq_hz"] == pytest.approx(80, abs=1)


def test_include_empty_lists_every_track_but_current():
    out = call_tool("fl_get_mix_overview", {"include_empty": True})
    assert len(out["tracks"]) == 128
    assert out["tracks"][-1]["name"] == "Insert 127"


def test_default_named_track_with_plugin_is_not_empty():
    tracks = [{"name": "Master"}, {"name": "Insert 1", "plugins": ["Fruity Limiter"]}, {"name": "Insert 2"}]
    install_fake(tracks=tracks)
    out = call_tool("fl_get_mix_overview", {})
    assert [t["name"] for t in out["tracks"]] == ["Master", "Insert 1"]


def test_overview_normalizes_tempo_x1000_from_outdated_device_script(caplog):
    install_fake(bpm=130.0, tempo_x1000=True)
    with caplog.at_level(logging.WARNING, logger="mixassist.fl.client"):
        first = call_tool("fl_get_mix_overview", {})
        second = call_tool("fl_get_mix_overview", {})
    assert first["bpm"] == 130.0 and second["bpm"] == 130.0
    warnings = [r for r in caplog.records if "x1000" in r.getMessage()]
    assert len(warnings) == 1 and "bpm=130000" in warnings[0].getMessage()


def test_true_bpm_is_left_alone(caplog):
    install_fake(bpm=999.0)
    with caplog.at_level(logging.WARNING, logger="mixassist.fl.client"):
        assert call_tool("fl_get_mix_overview", {})["bpm"] == 999.0
    assert not [r for r in caplog.records if r.name == "mixassist.fl.client"]


def test_old_api_has_no_eq():
    install_fake(api=34)
    out = call_tool("fl_get_mix_overview", {})
    assert all(t["eq"] == [] for t in out["tracks"])


@pytest.mark.realfl
@pytest.mark.skip(reason="PROBE: needs real FL (P3); see internal/probes.md")
def test_real_fl_volume_matches_mixer_hint():
    pass
