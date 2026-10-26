"""Every §3.4 FakeFL quirk."""
import pytest

from mixassist.errors import ToolError
from mixassist.fl import curves
from mixassist.fl.fake import FakeFL, VirtualClock, default_fake

from conftest import install_fake


def test_default_fake_layout(fl_client):
    reply = fl_client.request("tracks", {"start": 0, "count": 8})
    assert reply["total"] == 1 + 12 + 115 + 1  # master + named + defaults + current
    assert reply["n0"] == "Master" and reply["n1"] == "Kick"
    assert fl_client.request("tracks", {"start": 13, "count": 1})["n13"] == "Insert 13"


def test_delayed_readback(fl_client):
    fl_client.request("set", {"track": 1, "v": 0.6})
    assert fl_client.request("tracks", {"start": 1, "count": 1})["v1"] == pytest.approx(0.8)  # next op: old value
    assert fl_client.request("tracks", {"start": 1, "count": 1})["v1"] == pytest.approx(0.6)  # later op: new


def test_pickup_mode_ignores_large_jumps(fl_client):
    fl_client.request("set", {"track": 1, "v": 0.5, "pk": 1})
    fl_client.sync()
    assert fl_client.request("tracks", {"start": 1, "count": 1})["v1"] == pytest.approx(0.8)
    fl_client.request("set", {"track": 1, "v": 0.77, "pk": 1})
    fl_client.sync()
    assert fl_client.request("tracks", {"start": 1, "count": 1})["v1"] == pytest.approx(0.77)
    fl_client.request("set", {"track": 1, "v": 0.5, "pk": 0})
    fl_client.sync()
    assert fl_client.request("tracks", {"start": 1, "count": 1})["v1"] == pytest.approx(0.5)


def test_eq_exactly_three_bands_and_tables(fl_client):
    reply = fl_client.request("eq_get", {"track": 1})
    assert {k for k in reply if k.startswith("g") and not k.startswith("gd")} == {"g0", "g1", "g2"}
    assert reply["fh0"] < reply["fh1"] < reply["fh2"]
    with pytest.raises(ToolError) as e:
        fl_client.request("eq_set", {"track": 1, "band": 3, "g": 0.4})
    assert e.value.code == "FL_ERROR"
    fl_client.request("eq_set", {"track": 1, "band": 2, "g": 0.3})
    fl_client.sync()
    reply = fl_client.request("eq_get", {"track": 1})
    assert reply["gd2"] == pytest.approx(curves.interp(curves.EQ_GAIN_TABLE, 0.3), abs=1e-3)


def test_volume_table_default_point():
    assert curves.norm_to_db(0.8) == 0.0
    assert curves.db_to_norm(0.0) == pytest.approx(0.8, abs=1e-6)
    db = [p[1] for p in curves.VOLUME_TABLE]
    assert db == sorted(db)


def test_curve_inversion_is_search_on_table():
    for table in (curves.VOLUME_TABLE, curves.EQ_GAIN_TABLE, curves.EQ_FREQ_TABLE):
        for x in (0.03, 0.31, 0.5, 0.77, 0.99):
            assert curves.invert(table, curves.interp(table, x)) == pytest.approx(x, abs=1e-6)


def test_peaks_are_instantaneous_from_script():
    clock = VirtualClock()
    fake, client = install_fake(clock=clock, peak_script={1: [(0.0, 0.5), (1.0, 1.12), (1.2, 0.5)]})
    assert client.request("peaks", {"start": 1, "count": 1})["p1"] == 0.0  # not playing
    client.request("transport", {"cmd": "play"})
    clock.advance(0.5)
    assert client.request("peaks", {"start": 1, "count": 1})["p1"] == 0.5
    clock.advance(0.6)
    assert client.request("peaks", {"start": 1, "count": 1})["p1"] == 1.12
    clock.advance(0.2)
    assert client.request("peaks", {"start": 1, "count": 1})["p1"] == 0.5  # burst between polls is gone
    with pytest.raises(ToolError):
        client.request("peaks", {"start": 0, "count": 33})


def test_transport_clock_lag_and_pattern_wrap():
    clock = VirtualClock()
    fake, client = install_fake(clock=clock, bpm=120.0, ppq=96, loop_mode=1)
    client.request("transport", {"cmd": "play", "pos_ticks": 384})
    clock.advance(1.0)
    pos = client.request("transport", {"cmd": "pos"})["pos_ticks"]
    assert pos == round(384 + (1.0 - 0.005) * 192)  # 5 ms reporting lag
    assert fake.true_ticks() == pytest.approx(384 + 192)

    fake2, client2 = install_fake(clock=clock, loop_mode=0, pattern_beats=4)
    client2.request("transport", {"cmd": "play", "pos_ticks": 0})
    clock.advance(2.5)  # 5 beats at 120 BPM -> wraps at 4 beats
    assert fake2.true_ticks() == pytest.approx(96)
    reply = client2.request("transport", {"cmd": "pos"})
    assert reply["loop_mode"] == 0 and reply["bpm"] == 120.0 and reply["ppq"] == 96


def test_tempo_x1000_mimics_raw_get_current_tempo():
    from mixassist.fl.protocol import decode, encode

    clock = VirtualClock()
    fake = default_fake(clock=clock, bpm=130.0, ppq=96, tempo_x1000=True)
    _, _, raw = decode(fake.handle(encode(1, "transport", {"cmd": "play", "pos_ticks": 0})))
    assert raw["bpm"] == 130000.0
    clock.advance(1.0)
    assert fake.true_ticks() == pytest.approx(96 * 130 / 60)  # the clock itself runs at the true tempo


def test_transport_unsafe_replies_err_then_recovers():
    fake, client = install_fake(clock=VirtualClock(), transport_unsafe=1)
    assert client.request("transport", {"cmd": "pos"})["recording"] is False  # pure reads are never refused
    with pytest.raises(ToolError) as e:
        client.request("transport", {"cmd": "rec_on"})
    assert e.value.message == "FL rejected 'transport': transport failed: RuntimeError: Operation unsafe at current time"
    assert client.request("transport", {"cmd": "rec_on"})["recording"] is True


def test_transport_defer_ops_models_onidle_queue():
    fake, client = install_fake(clock=VirtualClock(), transport_defer_ops=1)
    r = client.request("transport", {"cmd": "rec_on"})
    assert r["recording"] is False and r["queued"] == 1
    assert client.request("transport", {"cmd": "play", "pos_ticks": 96})["queued"] == 1  # rec_on ran first
    r = client.request("transport", {"cmd": "pos"})
    assert r["recording"] and r["playing"] and r["pos_ticks"] == 96 and "queued" not in r


def test_notes_record_only_when_recording_and_playing():
    clock = VirtualClock()
    fake, client = install_fake(clock=clock)
    client.note(0x90, 60, 100)
    assert fake.recorded_notes == [] and (0, 60) in fake.sounding_notes
    client.request("transport", {"cmd": "rec_on"})
    client.request("transport", {"cmd": "play", "pos_ticks": 0})
    clock.advance(0.5)
    client.note(0x90, 62, 90)
    assert fake.recorded_notes[-1]["pitch"] == 62 and fake.recorded_notes[-1]["tick"] == pytest.approx(96)
    client.note(0x80, 62, 0)
    client.note(0xB0, 123, 0)
    assert fake.sounding_notes == set()


def test_channels_and_select(fl_client, fake_fl):
    reply = fl_client.request("channels", {"start": 0, "count": 16})
    assert reply["total"] == len(fake_fl.channels) and reply["n0"] == "Kick"
    fl_client.request("select", {"channel": 3})
    assert fake_fl.selected_channel == 3
    with pytest.raises(ToolError):
        fl_client.request("select", {"channel": 99})


def test_unknown_op_and_malformed_frame(fake_fl, fl_client):
    with pytest.raises(ToolError):
        fl_client.request("nope")
    assert fake_fl.handle(b"\xf0garbage\xf7") is None


def test_fake_api_below_35_has_no_eq():
    fake, client = install_fake(api=34)
    with pytest.raises(ToolError):
        client.request("eq_get", {"track": 0})


def test_fake_constructor_accepts_db_and_custom_curves():
    fake = FakeFL(tracks=[{"name": "Master"}, {"name": "A", "volume_db": -6.0}], vol_curve=[(0.0, -80.0), (1.0, 6.0)])
    assert fake.tracks[1]["vol"] == pytest.approx(74 / 86, abs=1e-6)
    assert fake.tracks[-1]["name"] == "Current"
    assert isinstance(default_fake(), FakeFL)
