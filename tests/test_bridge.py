"""mixassist/fl/bridge.py with a fake rtmidi module (no real MIDI ports needed)."""
from __future__ import annotations

import importlib.util
import os
import types
from pathlib import Path

import pytest

from mixassist.errors import ToolError
from mixassist.fl import bridge
from mixassist.fl.bridge import BridgeTransport, find_port, list_ports
from mixassist.fl.client import FLClient

_SIM_PATH = Path(__file__).with_name("test_device_script_sim.py")
_spec = importlib.util.spec_from_file_location("ma_device_sim_bridge", _SIM_PATH)
sim_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sim_mod)

MA = bytes([0xF0, 0x7D, 0x4D, 0x41])


class FakeRtMidi:
    """Stands in for the python-rtmidi module. ``responder(message) -> list[messages]`` models FL."""

    def __init__(self, inputs=("MA from FL",), outputs=("MA to FL",), responder=None, fail_init=False):
        self.inputs = list(inputs)
        self.outputs = list(outputs)
        self.responder = responder
        self.fail_init = fail_init
        self.sent: list[list[int]] = []
        self.instances: list = []
        self.MidiIn = self._make_in
        self.MidiOut = self._make_out

    def _make_in(self, *a, **kw):
        if self.fail_init:
            raise RuntimeError("MidiInCore::initialize: error creating OS-X MIDI client object")
        port = _FakeIn(self)
        self.instances.append(port)
        return port

    def _make_out(self, *a, **kw):
        if self.fail_init:
            raise RuntimeError("MidiOutCore::initialize: error")
        port = _FakeOut(self)
        self.instances.append(port)
        return port

    @property
    def midi_in(self):
        return next(p for p in self.instances if isinstance(p, _FakeIn))

    def deliver(self, message, delta=0.0):
        """Push a message into the open input port, as the rtmidi callback thread would."""
        port = self.midi_in
        assert port.callback is not None and port.opened is not None
        port.callback((list(message), delta), port.cb_data)


class _FakeIn:
    def __init__(self, rt):
        self.rt = rt
        self.opened = None
        self.callback = None
        self.cb_data = None
        self.ignore = None
        self.closed = False
        self.deleted = False

    def get_ports(self):
        return list(self.rt.inputs)

    def open_port(self, i, name=None):
        self.opened = i

    def ignore_types(self, sysex=True, timing=True, active_sense=True):
        self.ignore = {"sysex": sysex, "timing": timing, "active_sense": active_sense}

    def set_callback(self, cb, data=None):
        self.callback, self.cb_data = cb, data

    def cancel_callback(self):
        self.callback = None

    def close_port(self):
        self.closed = True

    def delete(self):
        self.deleted = True


class _FakeOut:
    def __init__(self, rt):
        self.rt = rt
        self.opened = None
        self.closed = False
        self.deleted = False

    def get_ports(self):
        return list(self.rt.outputs)

    def open_port(self, i, name=None):
        self.opened = i

    def send_message(self, message):
        assert self.opened is not None and not self.closed
        self.rt.sent.append(list(message))
        if self.rt.responder is not None:
            for reply in self.rt.responder(list(message)) or []:
                self.rt.deliver(reply)

    def close_port(self):
        self.closed = True

    def delete(self):
        self.deleted = True


# -------------------------------------------------------------- port matching

@pytest.mark.parametrize("names,wanted,expected", [
    (["IAC Driver Bus 1", "IAC Driver MA to FL", "IAC Driver MA from FL"], "MA to FL", 1),
    (["IAC Driver Bus 1", "IAC Driver MA to FL", "IAC Driver MA from FL"], "MA from FL", 2),
    (["Microsoft GS Wavetable Synth 0", "MA to FL 1", "MA from FL 2"], "MA to FL", 1),
    (["ma from fl 3"], "MA from FL", 0),
    (["MA to FL extra", "MA to FL"], "MA to FL", 1),
    (["IAC Driver Bus 1"], "MA to FL", None),
    ([], "MA to FL", None),
])
def test_find_port(names, wanted, expected):
    assert find_port(names, wanted) == expected


def test_opens_iac_ports_by_substring_and_enables_sysex():
    rt = FakeRtMidi(inputs=["IAC Driver Bus 1", "IAC Driver MA from FL"], outputs=["IAC Driver MA to FL", "IAC Driver Bus 1"])
    t = BridgeTransport(rtmidi_module=rt)
    assert t.out_name == "IAC Driver MA to FL" and t.in_name == "IAC Driver MA from FL"
    midi_in = rt.midi_in
    assert midi_in.opened == 1 and midi_in.ignore["sysex"] is False
    assert midi_in.callback is not None
    out = next(p for p in rt.instances if isinstance(p, _FakeOut))
    assert out.opened == 0
    t.close()


def test_opens_loopmidi_names():
    rt = FakeRtMidi(inputs=["MA to FL 0", "MA from FL 1"], outputs=["MA to FL 2", "MA from FL 3"])
    t = BridgeTransport(rtmidi_module=rt)
    assert (t.out_name, t.in_name) == ("MA to FL 2", "MA from FL 1")
    t.close()


@pytest.mark.parametrize("inputs,outputs,missing", [
    ([], ["MA to FL"], "MA from FL"),
    (["MA from FL"], ["Bus 1"], "MA to FL"),
    ([], [], "MA to FL"),
])
def test_missing_ports_raise_fl_not_connected_with_setup_hint(inputs, outputs, missing):
    rt = FakeRtMidi(inputs=inputs, outputs=outputs)
    with pytest.raises(ToolError) as info:
        BridgeTransport(rtmidi_module=rt)
    exc = info.value
    assert exc.code == "FL_NOT_CONNECTED"
    assert missing in exc.message
    for needle in ("IAC Driver", "Device is online", "loopMIDI", "MA to FL", "MA from FL", "MixAssist", "Hardware"):
        assert needle in exc.hint
    assert all(p.deleted for p in rt.instances)
    assert exc.to_dict()["error"]["code"] == "FL_NOT_CONNECTED"


def test_midi_system_failure_is_fl_not_connected():
    with pytest.raises(ToolError) as info:
        BridgeTransport(rtmidi_module=FakeRtMidi(fail_init=True))
    assert info.value.code == "FL_NOT_CONNECTED" and info.value.hint


def test_missing_rtmidi_package(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "rtmidi":
            raise ImportError("No module named 'rtmidi'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ToolError) as info:
        BridgeTransport()
    assert info.value.code == "FL_NOT_CONNECTED" and "python-rtmidi" in info.value.hint


def test_list_ports():
    rt = FakeRtMidi(inputs=["A", "MA from FL"], outputs=["MA to FL"])
    assert list_ports(rt) == {"inputs": ["A", "MA from FL"], "outputs": ["MA to FL"]}
    assert all(p.deleted for p in rt.instances)
    with pytest.raises(ToolError):
        list_ports(FakeRtMidi(fail_init=True))


# ------------------------------------------------------------ send / receive

@pytest.fixture
def rt():
    return FakeRtMidi()


@pytest.fixture
def transport(rt):
    t = BridgeTransport(rtmidi_module=rt)
    yield t
    t.close()


def test_send_sysex_and_short(transport, rt):
    frame = MA + b"1|hello|" + b"\xf7"
    transport.send_sysex(frame)
    transport.send_short(0x90, 60, 100)
    transport.send_short(0xB0, 123, 0)
    assert rt.sent == [list(frame), [0x90, 60, 100], [0xB0, 123, 0]]


@pytest.mark.parametrize("bad", [b"", b"\xf0", MA + b"x", b"\x90\x3c\x40\xf7", MA + b"\x80\xf7"])
def test_send_sysex_rejects_bad_frames(transport, bad):
    with pytest.raises(ValueError):
        transport.send_sysex(bad)


def test_send_short_rejects_non_channel_status(transport):
    with pytest.raises(ValueError):
        transport.send_short(0xF0, 0, 0)


def test_recv_filters_by_prefix(transport, rt):
    rt.deliver([0x90, 60, 100])                    # note echo
    rt.deliver([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7])  # someone else's SysEx
    rt.deliver([0xF8])                             # clock
    good = MA + b"3|ok|api=i36" + b"\xf7"
    rt.deliver(list(good))
    assert transport.recv_sysex(0.1) == good
    assert transport.recv_sysex(0.01) is None
    assert transport.recv_sysex(0) is None


def test_recv_reassembles_split_sysex(transport, rt):
    frame = MA + b"4|ok|n1=sKick" + b"\xf7"
    rt.deliver(list(frame[:5]))
    rt.deliver(list(frame[5:9]))
    rt.deliver(list(frame[9:]))
    assert transport.recv_sysex(0.1) == frame


def test_recv_preserves_order(transport, rt):
    frames = [MA + f"{i}|ok|".encode() + b"\xf7" for i in range(1, 6)]
    for f in frames:
        rt.deliver(list(f))
    assert [transport.recv_sysex(0.1) for _ in frames] == frames


def test_close_is_idempotent_and_releases_ports(rt):
    t = BridgeTransport(rtmidi_module=rt)
    t.close()
    t.close()
    assert all(p.closed and p.deleted for p in rt.instances)
    assert rt.midi_in.callback is None
    with pytest.raises(ToolError) as info:
        t.send_sysex(MA + b"1|hello|" + b"\xf7")
    assert info.value.code == "FL_NOT_CONNECTED"


def test_context_manager(rt):
    with BridgeTransport(rtmidi_module=rt) as t:
        assert t.in_name == "MA from FL"
    assert all(p.deleted for p in rt.instances)


# ------------------------------------- end to end through the device script

def _fl_responder(sim, dev):
    """Model FL: SysEx goes to OnSysEx (replies via device.midiOutSysex); notes are recorded."""
    notes = []

    def respond(message):
        if message[0] == 0xF0:
            before = len(sim.sysex_out)
            dev.OnSysEx(sim_mod.FakeMsg(bytes(message)))
            return [list(f) for f in sim.sysex_out[before:]]
        notes.append(tuple(message))
        return []

    return respond, notes


@pytest.fixture
def loop():
    sim = sim_mod.SimFL(n_tracks=20)
    sim.tracks[1]["name"] = "Kick"
    dev = sim_mod.load_device_script(sim)
    respond, notes = _fl_responder(sim, dev)
    rt = FakeRtMidi(inputs=["IAC Driver MA from FL"], outputs=["IAC Driver MA to FL"], responder=respond)
    t = BridgeTransport(rtmidi_module=rt)
    fl = FLClient(t, "bridge")
    yield fl, sim, t, notes
    t.close()


def test_flclient_round_trips_over_bridge(loop):
    fl, sim, t, _ = loop
    assert fl.request("hello") == {"api": 36, "port": 7}
    r = fl.request("tracks", {"start": 0, "count": 8})
    assert r["n1"] == "Kick" and r["total"] == 21
    fl.request("set", {"track": 1, "v": 0.5, "pk": 0})
    assert sim.calls_named("mixer.setTrackVolume") == [("mixer.setTrackVolume", 1, 0.5, 0)]
    with pytest.raises(ToolError) as info:
        fl.request("eq_set", {"track": 1, "band": 3, "g": 0.5})
    assert info.value.code == "FL_ERROR" and "band" in info.value.message


def test_flclient_times_out_when_fl_is_silent():
    rt = FakeRtMidi(responder=lambda m: [])
    t = BridgeTransport(rtmidi_module=rt)
    with pytest.raises(ToolError) as info:
        FLClient(t, "bridge").request("hello", timeout=0.05)
    assert info.value.code == "FL_TIMEOUT"
    t.close()


def test_notes_pass_through_unhandled(loop):
    fl, sim, t, notes = loop
    fl.note(0x90, 60, 100)
    fl.note(0x80, 60, 0)
    assert notes == [(0x90, 60, 100), (0x80, 60, 0)]
    assert sim.sysex_out == []


def test_stale_replies_are_skipped(loop):
    fl, sim, t, _ = loop
    t._frames.put(MA + b"999|ok|junk=i1" + b"\xf7")
    assert fl.request("hello")["api"] == 36


def test_probe_helpers_over_loop(loop, capsys):
    fl, sim, t, notes = loop
    out = []
    p1 = bridge.probe_hello(fl, count=3, out=out.append)
    assert p1["api"] == 36 and len(p1["rtt_ms"]) == 3
    p2 = bridge.probe_sizes(fl, out=out.append)
    assert p2[1024] == "intact" and p2[256] == "intact"
    table = bridge.probe_fader(fl, 1, step=0.25, out=out.append)
    assert [v for v, _ in table] == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert sim.tracks[1]["vol"] == 0.8  # restored
    eq = bridge.probe_eq(fl, 1, out=out.append)
    assert eq["bands"] == 3 and sim.tracks[1]["eq"][0]["g"] == 0.5  # restored
    p5 = bridge.probe_readback(fl, 1, out=out.append)
    assert len(p5["reads"]) == 3 and sim.tracks[1]["vol"] == 0.8
    sim.tracks[3]["peak"] = 0.7
    p6 = bridge.probe_peaks(fl, seconds=0.05, out=out.append)
    assert p6["polls"] >= 1 and p6["max"]["p3"] == 0.7 and sim.playing is False
    assert any("P1 pass criterion" in line for line in out)


def test_probe_notes_records_scale(loop, monkeypatch):
    fl, sim, t, notes = loop
    monkeypatch.setattr(bridge.time, "sleep", lambda s: None)
    r = bridge.probe_notes(fl, t, channel=1, out=lambda s: None)
    assert r["notes"] == [60, 62, 64, 65, 67, 69, 71, 72]
    assert [n[1] for n in notes if n[0] == 0x90] == r["notes"]
    assert notes[-1] == (0xB0, 123, 0)
    assert sim.selected == 1 and sim.window == 3
    assert sim.playing is False and sim.recording is False
    assert len(sim.calls_named("transport.record")) == 2


def test_cli_check_reports_missing_ports(monkeypatch, capsys):
    monkeypatch.setattr(bridge, "_load_rtmidi", lambda module=None: module or FakeRtMidi(inputs=[], outputs=[]))
    assert bridge.main(["--check"]) == 2
    err = capsys.readouterr().err
    assert "FL_NOT_CONNECTED" in err and "hint:" in err


def test_cli_check_prints_round_trip(monkeypatch, capsys):
    sim = sim_mod.SimFL()
    dev = sim_mod.load_device_script(sim)
    respond, _ = _fl_responder(sim, dev)
    monkeypatch.setattr(bridge, "_load_rtmidi", lambda module=None: module or FakeRtMidi(responder=respond))
    assert bridge.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "hello round trip:" in out and "ms" in out and "FL API 36" in out


def test_cli_list(monkeypatch, capsys):
    monkeypatch.setattr(bridge, "_load_rtmidi", lambda module=None: module or FakeRtMidi())
    assert bridge.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "MA from FL" in out and "MA to FL" in out


# ------------------------------------------------------------- real FL only

realfl = pytest.mark.skipif(os.environ.get("MIXASSIST_REALFL") != "1",
                            reason="needs FL Studio with the MixAssist script and IAC/loopMIDI ports (internal/probes.md)")


@pytest.mark.realfl
@realfl
def test_real_fl_hello_round_trip():
    t = BridgeTransport()
    try:
        out = []
        r = bridge.probe_hello(FLClient(t, "bridge"), count=10, out=out.append)
        print("\n".join(out))
        assert r["api"] > 0
    finally:
        t.close()


@pytest.mark.realfl
@realfl
def test_real_fl_1024_byte_reply():
    t = BridgeTransport()
    try:
        assert bridge.probe_sizes(FLClient(t, "bridge"), sizes=(1024,), out=print)[1024] == "intact"
    finally:
        t.close()
