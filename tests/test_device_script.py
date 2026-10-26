"""fl_script/MixAssist/device_MixAssist.py against a simulated FL (spec section 3.3)."""
from __future__ import annotations

import ast
import importlib.util
import math
import py_compile
from pathlib import Path

import pytest

_SIM_PATH = Path(__file__).with_name("test_device_script_sim.py")
_spec = importlib.util.spec_from_file_location("ma_device_sim", _SIM_PATH)
sim_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sim_mod)
SimFL, FakeMsg, load_device_script, SCRIPT_PATH = sim_mod.SimFL, sim_mod.FakeMsg, sim_mod.load_device_script, sim_mod.SCRIPT_PATH

ALLOWED_IMPORTS = {"mixer", "transport", "channels", "general", "device", "plugins", "ui", "midi"}


@pytest.fixture
def sim():
    return SimFL()


@pytest.fixture
def dev(sim):
    return load_device_script(sim)


def call(dev, sim, op, args=None, msg_id=42):
    """Send one request through OnSysEx; return (reply_op, reply_args)."""
    before = len(sim.sysex_out)
    msg = FakeMsg(dev.encode(msg_id, op, args or {}))
    dev.OnSysEx(msg)
    assert msg.handled is True
    assert len(sim.sysex_out) == before + 1, "expected exactly one reply"
    frame = sim.sysex_out[-1]
    assert frame[:4] == bytes([0xF0, 0x7D, 0x4D, 0x41]) and frame[-1] == 0xF7
    assert len(frame) <= 1024
    rid, rop, rargs = dev.decode(frame)
    assert rid == msg_id
    return rop, rargs


def ok(dev, sim, op, args=None):
    rop, rargs = call(dev, sim, op, args)
    assert rop == "ok", rargs
    return rargs


def err(dev, sim, op, args=None):
    rop, rargs = call(dev, sim, op, args)
    assert rop == "err", rargs
    assert set(rargs) == {"msg"} and isinstance(rargs["msg"], str) and rargs["msg"]
    return rargs["msg"]


# ------------------------------------------------------------ static checks --

def test_file_is_ascii_only():
    data = SCRIPT_PATH.read_bytes()
    bad = [i for i, b in enumerate(data) if b > 0x7F]
    assert not bad, f"non-ASCII bytes at offsets {bad[:10]}"


def test_py_compile():
    py_compile.compile(str(SCRIPT_PATH), doraise=True)


def test_header_names_script():
    first = SCRIPT_PATH.read_text().splitlines()[0]
    assert first == "# name=MixAssist"


def test_imports_only_fl_modules_and_no_forbidden_features():
    tree = ast.parse(SCRIPT_PATH.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            pytest.fail(f"from-import not allowed: {node.module}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in ALLOWED_IMPORTS, alias.name
        if isinstance(node, ast.Name):
            assert node.id not in {"open", "__import__", "exec", "eval"}, node.id
    funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "OnSysEx" in funcs
    # Notes and CC must reach FL unhandled; no idle polling.
    for name in ("OnIdle", "OnMidiIn", "OnMidiMsg", "OnNoteOn", "OnNoteOff", "OnControlChange"):
        assert name not in funcs, name


# ------------------------------------------------------------------ codec ----

CODEC_CASES = [
    (1, "hello", {}),
    (999999, "ok", {"api": 36, "port": 7}),
    (17, "tracks", {"start": 8, "count": 8}),
    (5, "ok", {"n0": "Master", "v0": 0.8, "vd0": 0.0, "p0": -0.25, "m0": False, "so0": True}),
    (6, "ok", {"n3": "Kick | Snare; a=b 100% \u00e9\u00e8 \u6f22 \U0001f941", "x": None}),
    (7, "set", {"track": 3, "v": 0.123456789012345, "p": -1.0, "s": 1e-9}),
    (8, "err", {"msg": "unknown op foo"}),
    (9, "ok", {"pad": ""}),
]


@pytest.mark.parametrize("msg_id,op,args", CODEC_CASES)
def test_device_codec_round_trip(dev, msg_id, op, args):
    frame = dev.encode(msg_id, op, args)
    assert all(b < 0x80 for b in frame[1:-1])
    assert dev.decode(frame) == (msg_id, op, args)


def test_device_codec_frame_layout(dev):
    assert dev.encode(3, "hello", {}) == b"\xf0\x7d\x4d\x41" + b"3|hello|" + b"\xf7"
    assert dev.encode(4, "x", {"a": 1, "b": True, "c": None, "d": "a b", "e": 0.5}) == (
        b"\xf0\x7d\x4d\x41" + b"4|x|a=i1;b=b1;c=n;d=sa%20b;e=f0.5" + b"\xf7")


def test_device_decoder_is_lenient_about_missing_trailing_bar(dev):
    assert dev.decode(b"\xf0\x7d\x4d\x41" + b"12|hello" + b"\xf7") == (12, "hello", {})
    assert dev.decode(b"\xf0\x7d\x4d\x41" + b"12|x|a=i1;" + b"\xf7") == (12, "x", {"a": 1})


@pytest.mark.parametrize("payload", [b"", b"abc|hello|", b"0|hello|", b"1000000|x|", b"1||", b"1|x|a", b"1|x|a=q1",
                                     b"1|x|a=b2", b"1|x|a=s%G1", b"1|x|a=s%4"])
def test_device_decoder_rejects_malformed(dev, payload):
    with pytest.raises(ValueError):
        dev.decode(b"\xf0\x7d\x4d\x41" + payload + b"\xf7")


# --------------------------------------------------------------------- ops ---

def test_hello(dev, sim):
    assert ok(dev, sim, "hello") == {"api": 36, "port": 7}


def test_tracks_keys_and_values(dev, sim):
    sim.tracks[2].update(name="Kick", vol=0.5, pan=-0.3, sep=0.2, muted=True, solo=True)
    r = ok(dev, sim, "tracks", {"start": 0, "count": 8})
    assert r["total"] == len(sim.tracks)  # raw trackCount(), incl. master and "current"
    for i in range(8):
        assert {f"n{i}", f"v{i}", f"vd{i}", f"p{i}", f"s{i}", f"m{i}", f"so{i}"} <= set(r)
    assert len(r) == 1 + 8 * 7
    assert r["n2"] == "Kick" and r["v2"] == 0.5 and r["p2"] == -0.3 and r["s2"] == 0.2
    assert r["m2"] is True and r["so2"] is True
    assert isinstance(r["vd0"], float) and r["vd0"] == pytest.approx(0.0)
    assert ("mixer.getTrackVolume", 2, 1) in sim.calls


def test_tracks_excludes_current_track(dev, sim):
    last_usable = len(sim.tracks) - 2
    r = ok(dev, sim, "tracks", {"start": last_usable - 2, "count": 8})
    names = {k for k in r if k.startswith("n")}
    assert names == {f"n{i}" for i in range(last_usable - 2, last_usable + 1)}
    assert "Current" not in r.values()
    assert ok(dev, sim, "tracks", {"start": 500, "count": 8}) == {"total": len(sim.tracks)}


@pytest.mark.parametrize("args", [{"start": 0, "count": 9}, {"start": 0, "count": 0}, {"start": -1}, {"start": "a"}])
def test_tracks_bad_ranges(dev, sim, args):
    err(dev, sim, "tracks", args)


def test_tracks_default_count_is_8(dev, sim):
    r = ok(dev, sim, "tracks", {})
    assert {k for k in r if k.startswith("n")} == {f"n{i}" for i in range(8)}


def test_fx_lists_loaded_slots(dev, sim):
    sim.tracks[3]["plugins"] = {0: "Fruity Limiter", 4: "Fruity Reeverb 2"}
    r = ok(dev, sim, "fx", {"track": 3})
    assert r == {"s0": True, "n0": "Fruity Limiter", "s4": True, "n4": "Fruity Reeverb 2"}
    assert ok(dev, sim, "fx", {"track": 1}) == {}
    slots = [c[2] for c in sim.calls_named("mixer.isTrackPluginValid") if c[1] == 3]
    assert slots == list(range(10))


def test_eq_get_keys(dev, sim):
    r = ok(dev, sim, "eq_get", {"track": 1})
    assert r["bands"] == 3
    for b in range(3):
        assert {f"g{b}", f"gd{b}", f"f{b}", f"fh{b}", f"w{b}"} <= set(r)
    assert len(r) == 1 + 3 * 5
    assert r["f0"] < r["f1"] < r["f2"]
    assert ("mixer.getEqGain", 1, 2, 1) in sim.calls and ("mixer.getEqFrequency", 1, 0, 1) in sim.calls


def test_eq_ops_need_api_35():
    sim = SimFL(api=34)
    dev = load_device_script(sim)
    assert "API" in err(dev, sim, "eq_get", {"track": 1})
    assert "API" in err(dev, sim, "eq_set", {"track": 1, "band": 0, "g": 0.4})
    assert not sim.calls_named("mixer.setEqGain")
    assert ok(dev, sim, "hello")["api"] == 34


def test_set_uses_pickup_0_and_save_undo_first(dev, sim):
    assert ok(dev, sim, "set", {"track": 4, "v": 0.6, "p": -0.5, "s": 0.25}) == {}
    names = [c[0] for c in sim.calls]
    undo = names.index("general.saveUndo")
    assert undo < names.index("mixer.setTrackVolume")
    assert sim.calls_named("general.saveUndo") == [("general.saveUndo", "MixAssist", 0)]
    assert sim.calls_named("mixer.setTrackVolume") == [("mixer.setTrackVolume", 4, 0.6, 0)]
    assert sim.calls_named("mixer.setTrackPan") == [("mixer.setTrackPan", 4, -0.5, 0)]
    assert sim.calls_named("mixer.setTrackStereoSep") == [("mixer.setTrackStereoSep", 4, 0.25, 0)]


def test_set_partial_only_touches_given_fields(dev, sim):
    ok(dev, sim, "set", {"track": 0, "p": 0.1})
    assert not sim.calls_named("mixer.setTrackVolume") and not sim.calls_named("mixer.setTrackStereoSep")
    assert sim.calls_named("mixer.setTrackPan") == [("mixer.setTrackPan", 0, 0.1, 0)]


def test_set_accepts_int_values(dev, sim):
    ok(dev, sim, "set", {"track": 1, "v": 1, "p": 0})
    assert sim.calls_named("mixer.setTrackVolume") == [("mixer.setTrackVolume", 1, 1.0, 0)]


@pytest.mark.parametrize("args", [
    {"track": 1}, {"track": 1, "v": 1.5}, {"track": 1, "p": -1.01}, {"track": 1, "s": 2.0},
    {"track": 1, "v": "0.5"}, {"track": 1, "v": True}, {"v": 0.5}, {"track": -1, "v": 0.5},
    {"track": 20, "v": 0.5},  # index 20 is the "current" track in SimFL(n_tracks=20)
    {"track": 1.5, "v": 0.5},
])
def test_set_rejects_bad_args_without_writing(dev, sim, args):
    err(dev, sim, "set", args)
    assert not any(c[0].startswith("mixer.set") or c[0] == "general.saveUndo" for c in sim.calls)


def test_eq_set(dev, sim):
    assert ok(dev, sim, "eq_set", {"track": 2, "band": 2, "g": 0.3, "f": 0.9, "w": 0.1}) == {}
    assert sim.calls_named("mixer.setEqGain") == [("mixer.setEqGain", 2, 2, 0.3)]
    assert sim.calls_named("mixer.setEqFrequency") == [("mixer.setEqFrequency", 2, 2, 0.9)]
    assert sim.calls_named("mixer.setEqBandwidth") == [("mixer.setEqBandwidth", 2, 2, 0.1)]


@pytest.mark.parametrize("args", [{"track": 2, "band": 3, "g": 0.3}, {"track": 2, "band": -1, "g": 0.3},
                                  {"track": 2, "band": 0}, {"track": 2, "band": 0, "g": 1.2}, {"track": 2, "g": 0.3}])
def test_eq_set_rejects(dev, sim, args):
    err(dev, sim, "eq_set", args)
    assert not sim.calls_named("mixer.setEqGain")


def test_peaks(dev, sim):
    sim.tracks[5]["peak"] = 1.12
    r = ok(dev, sim, "peaks", {"start": 0, "count": 32})
    assert set(r) == {f"p{i}" for i in range(len(sim.tracks) - 1)}  # 20 usable tracks < 32
    assert r["p5"] == 1.12
    assert all(c[2] == 2 for c in sim.calls_named("mixer.getTrackPeaks"))
    err(dev, sim, "peaks", {"start": 0, "count": 33})


def test_peaks_32_per_op_on_big_mixer():
    sim = SimFL(n_tracks=127)
    dev = load_device_script(sim)
    r = ok(dev, sim, "peaks", {"start": 32, "count": 32})
    assert set(r) == {f"p{i}" for i in range(32, 64)}


TRANSPORT_KEYS = {"playing", "recording", "pos_ticks", "loop_mode", "bpm", "ppq"}


def test_transport_reply_keys(dev, sim):
    r = ok(dev, sim, "transport", {"cmd": "pos"})
    assert set(r) == TRANSPORT_KEYS
    assert r == {"playing": False, "recording": False, "pos_ticks": 0, "loop_mode": 1, "bpm": 120.0, "ppq": 96}
    assert ("transport.getSongPos", 2) in sim.calls


@pytest.mark.parametrize("raw, bpm", [
    (130000.0, 130.0),  # observed on FL API 45 for a 130 BPM project
    (128500.0, 128.5),
    (130.0, 130.0),
])
def test_transport_reports_true_bpm(dev, sim, raw, bpm):
    sim.bpm = raw
    assert ok(dev, sim, "transport", {"cmd": "pos"})["bpm"] == bpm


def test_transport_play_stop_are_idempotent(dev, sim):
    assert ok(dev, sim, "transport", {"cmd": "play"})["playing"] is True
    assert ok(dev, sim, "transport", {"cmd": "play"})["playing"] is True  # start() toggles; must not pause
    assert len(sim.calls_named("transport.start")) == 1
    assert ok(dev, sim, "transport", {"cmd": "stop"})["playing"] is False


def test_transport_rec_on_off_are_idempotent(dev, sim):
    assert ok(dev, sim, "transport", {"cmd": "rec_on"})["recording"] is True
    assert ok(dev, sim, "transport", {"cmd": "rec_on"})["recording"] is True
    assert ok(dev, sim, "transport", {"cmd": "rec_off"})["recording"] is False
    assert ok(dev, sim, "transport", {"cmd": "rec_off"})["recording"] is False
    assert len(sim.calls_named("transport.record")) == 2


def test_transport_pos_ticks_set_before_play(dev, sim):
    r = ok(dev, sim, "transport", {"cmd": "play", "pos_ticks": 384})
    assert r["pos_ticks"] == 384 and r["playing"] is True
    names = [c[0] for c in sim.calls]
    assert names.index("transport.setSongPos") < names.index("transport.start")
    assert sim.calls_named("transport.setSongPos") == [("transport.setSongPos", 384, 2)]


@pytest.mark.parametrize("args", [{}, {"cmd": "rewind"}, {"cmd": "pos", "pos_ticks": -5}, {"cmd": "pos", "pos_ticks": 1.5}])
def test_transport_rejects(dev, sim, args):
    err(dev, sim, "transport", args)


def test_channels(dev, sim):
    r = ok(dev, sim, "channels", {"start": 0, "count": 16})
    assert r == {"total": 3, "n0": "Kick", "n1": "Snare", "n2": "Synth"}
    assert all(c[1] is True for c in sim.calls_named("channels.channelCount"))
    assert all(c[2] is True for c in sim.calls_named("channels.getChannelName"))
    err(dev, sim, "channels", {"count": 17})


def test_select_opens_piano_roll(dev, sim):
    assert ok(dev, sim, "select", {"channel": 2}) == {}
    assert sim.calls_named("channels.selectOneChannel") == [("channels.selectOneChannel", 2, True)]
    assert sim.calls_named("ui.showWindow") == [("ui.showWindow", 3)]
    err(dev, sim, "select", {"channel": 3})
    err(dev, sim, "select", {})


# ------------------------------------------------------------ error paths ---

def test_unknown_op(dev, sim):
    assert "unknown op" in err(dev, sim, "frobnicate")


def test_fl_exception_becomes_err(dev, sim):
    sim.raise_on = "mixer.getTrackName"
    msg = err(dev, sim, "tracks", {"start": 0, "count": 1})
    assert "tracks failed" in msg and "RuntimeError" in msg


def test_malformed_payload_with_id_gets_err(dev, sim):
    msg = FakeMsg(b"\xf0\x7d\x4d\x41" + b"77|set|track=q1" + b"\xf7")
    dev.OnSysEx(msg)
    assert msg.handled is True
    rid, rop, rargs = dev.decode(sim.sysex_out[-1])
    assert (rid, rop) == (77, "err") and "bad frame" in rargs["msg"]


def test_garbage_with_our_prefix_is_swallowed_silently(dev, sim):
    msg = FakeMsg(b"\xf0\x7d\x4d\x41" + b"nonsense" + b"\xf7")
    dev.OnSysEx(msg)
    assert msg.handled is True and sim.sysex_out == []


def test_foreign_sysex_is_left_unhandled(dev, sim):
    msg = FakeMsg(bytes([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7]))
    dev.OnSysEx(msg)
    assert msg.handled is False and sim.sysex_out == []


def test_missing_sysex_is_left_unhandled(dev, sim):
    msg = FakeMsg(None)
    dev.OnSysEx(msg)
    assert msg.handled is False and sim.sysex_out == []


@pytest.mark.parametrize("op", ["ok", "err"])
def test_echoed_reply_does_not_create_feedback(dev, sim, op):
    msg = FakeMsg(dev.encode(42, op, {"msg": "echo"}))
    dev.OnSysEx(msg)
    assert msg.handled is True and sim.sysex_out == []


# --------------------------------------------------------- 1024-byte cap ----

def test_pad_reply_exact_sizes(dev, sim):
    for size in (64, 512, 1000, 1024):
        r = ok(dev, sim, "pad", {"size": size})
        assert len(sim.sysex_out[-1]) == size
        assert set(r["pad"]) <= {"x"}
    assert "size" in err(dev, sim, "pad", {"size": 1025})


def test_oversized_reply_becomes_err():
    sim = SimFL()
    for t in sim.tracks:
        t["name"] = "\u00e9" * 40  # 240 escaped bytes per name
    dev = load_device_script(sim)
    msg = err(dev, sim, "tracks", {"start": 0, "count": 8})
    assert "1024" in msg
    assert len(sim.sysex_out[-1]) <= 1024


def test_oversized_request_is_rejected(dev, sim):
    frame = dev.encode(9, "set", {"track": 1, "junk": "x" * 1100})
    msg = FakeMsg(frame)
    dev.OnSysEx(msg)
    rid, rop, rargs = dev.decode(sim.sysex_out[-1])
    assert (rid, rop) == (9, "err") and "1024" in rargs["msg"]
    assert not sim.calls_named("mixer.setTrackVolume")


def test_long_error_messages_are_truncated(dev, sim):
    msg = err(dev, sim, "x" * 900)
    assert len(msg) <= 200


# ------------------------------------------------------------- callbacks ----

def test_on_init_prints_without_output_port(sim, capsys):
    sim.assigned = False
    dev = load_device_script(sim)
    dev.OnInit()
    out = capsys.readouterr().out
    assert "MixAssist" in out and "MA from FL" in out


def test_non_finite_volume_db_is_sent_as_null(dev, sim):
    sim.tracks[1]["vol"] = 0.0  # SimFL reports -inf dB at silence
    r = ok(dev, sim, "tracks", {"start": 1, "count": 1})
    assert r["vd1"] is None and r["v1"] == 0.0
    assert dev.encode(1, "ok", {"a": math.nan, "b": math.inf}) == dev.encode(1, "ok", {"a": None, "b": None})


def test_set_ignores_pk_and_always_uses_pickup_0(dev, sim):
    ok(dev, sim, "set", {"track": 2, "v": 0.3, "pk": 2})
    ok(dev, sim, "eq_set", {"track": 2, "band": 0, "g": 0.3, "pk": 0})
    assert sim.calls_named("mixer.setTrackVolume") == [("mixer.setTrackVolume", 2, 0.3, 0)]
    assert sim.calls_named("mixer.setEqGain") == [("mixer.setEqGain", 2, 0, 0.3)]


# ---------------------------------------------- cross-codec with Core -------

def _protocol():
    return pytest.importorskip("mixassist.fl.protocol", reason="Core's mixassist/fl/protocol.py not present yet")


@pytest.mark.parametrize("msg_id,op,args", [c for c in CODEC_CASES])
def test_core_decodes_device_frames(dev, msg_id, op, args):
    protocol = _protocol()
    assert protocol.decode(dev.encode(msg_id, op, args)) == (msg_id, op, args)


@pytest.mark.parametrize("msg_id,op,args", [c for c in CODEC_CASES])
def test_device_decodes_core_frames(dev, msg_id, op, args):
    protocol = _protocol()
    assert dev.decode(protocol.encode(msg_id, op, args)) == (msg_id, op, args)


# ------------------------------------- same op table as Core's FakeFL -------

DEVICE_ONLY_KEYS = {"eq_get": {"bands"}}
SAME_REQUESTS = [
    ("hello", {}),
    ("tracks", {"start": 0, "count": 8}),
    ("tracks", {"start": 8, "count": 8}),
    ("tracks", {"start": 3, "count": 1}),
    ("fx", {"track": 2}),
    ("fx", {"track": 1}),
    ("eq_get", {"track": 1}),
    ("set", {"track": 1, "v": 0.5, "p": 0.2, "s": -0.1, "pk": 0}),
    ("eq_set", {"track": 1, "band": 1, "g": 0.4, "f": 0.6, "pk": 0}),
    ("eq_set", {"track": 1, "band": 3, "g": 0.4, "pk": 0}),
    ("peaks", {"start": 0, "count": 32}),
    ("transport", {"cmd": "pos"}),
    ("transport", {"cmd": "rewind"}),
    ("channels", {"start": 0, "count": 16}),
    ("channels", {"start": 2, "count": 1}),
    ("select", {"channel": 1}),
    ("frobnicate", {}),
]


@pytest.mark.parametrize("op,args", SAME_REQUESTS, ids=[f"{op}-{i}" for i, (op, _) in enumerate(SAME_REQUESTS)])
def test_reply_shape_matches_fakefl(op, args):
    fake_mod = pytest.importorskip("mixassist.fl.fake", reason="Core's FakeFL not present yet")
    protocol = _protocol()
    n = 12
    names = ["Master"] + [f"Insert {i}" for i in range(1, n)]
    fake = fake_mod.FakeFL(tracks=[{"name": nm} for nm in names], channels=["Kick", "Snare", "Synth"])
    fake.tracks[2]["plugins"] = {0: "Fruity Limiter", 3: "Fruity Delay 3"}
    sim = SimFL(n_tracks=n)
    sim.tracks[2]["plugins"] = {0: "Fruity Limiter", 3: "Fruity Delay 3"}
    dev = load_device_script(sim)

    fid, fop, fargs = protocol.decode(fake.handle(protocol.encode(5, op, args)))
    dop, dargs = call(dev, sim, op, args, msg_id=5)
    assert fop == dop, (fargs, dargs)
    if dop == "err":
        return
    extra = DEVICE_ONLY_KEYS.get(op, set())
    if op in ("tracks", "peaks"):
        # FakeFL also reports the "current" track (index n); the device omits it (spec section 2)
        # and the server never reads it.
        fargs = {k: v for k, v in fargs.items() if not (k[-len(str(n)):] == str(n) and k[:-len(str(n))].isalpha())}
    assert set(dargs) - extra == set(fargs)
    for k, v in fargs.items():
        assert type(dargs[k]) is type(v), (k, dargs[k], v)
