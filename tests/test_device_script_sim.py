"""Small simulated FL for loading fl_script/MixAssist/device_MixAssist.py outside FL.

Not a test module itself; test_device_script.py and test_bridge.py load it by path.
The simulation only records calls and returns plausible values. It is not a
model of real FL behaviour (that is FakeFL's job, and real FL is checked by
internal/probes.md).
"""
from __future__ import annotations

import importlib.util
import itertools
import math
import sys
import types
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "fl_script" / "MixAssist" / "device_MixAssist.py"
FL_MODULES = ("mixer", "transport", "channels", "general", "device", "plugins", "ui", "midi")
_counter = itertools.count()
UNSAFE_MSG = "Operation unsafe at current time"  # FL API 45 raised RuntimeError with this text
# FlMidiMsg default pmeFlags: PME_System | PME_System_Safe | PME_PreviewNote | PME_FromMIDI [ST:fl_classes]
DEFAULT_PME_FLAGS = 0b101110
TRANSPORT_SETTERS = {"transport.record", "transport.start", "transport.stop", "transport.setSongPos"}


class FakeMsg:
    def __init__(self, sysex: bytes, pmeFlags: int = DEFAULT_PME_FLAGS):
        self.sysex = sysex
        self.handled = False
        self.pmeFlags = pmeFlags


class SimFL:
    def __init__(self, n_tracks: int = 20, api: int = 36, port: int = 7, channel_names=None):
        # index 0 = master, last index = "current" track
        self.tracks = []
        for i in range(n_tracks + 1):
            name = "Master" if i == 0 else ("Current" if i == n_tracks else f"Insert {i}")
            self.tracks.append({
                "name": name, "vol": 0.8, "pan": 0.0, "sep": 0.0, "muted": False, "solo": False,
                "plugins": {}, "peak": 0.0,
                "eq": [{"g": 0.5, "f": f, "w": 0.5} for f in (0.2, 0.5, 0.8)],
            })
        self.api = api
        self.port = port
        self.eq_bands = 3
        self.playing = False
        self.recording = False
        self.pos = 0
        self.loop_mode = 1
        self.bpm = 120.0
        self.ppq = 96
        self.channel_names = list(channel_names or ["Kick", "Snare", "Synth"])
        self.selected = None
        self.window = None
        self.assigned = True
        self.calls: list[tuple] = []
        self.sysex_out: list[bytes] = []
        self.raise_on: str | None = None
        # Calls named here raise RuntimeError(UNSAFE_MSG) while the script runs inside a callback
        # listed in unsafe_in, the way real FL refused transport.record() inside OnSysEx.
        self.unsafe_calls: set[str] = set()
        self.unsafe_in: set[str] = {"OnSysEx"}
        self.context: str | None = None
        self.modules = self._build()

    def _rec(self, name, *args):
        if name in self.unsafe_calls and self.context in self.unsafe_in:
            self.calls.append(("unsafe", name, self.context))
            raise RuntimeError(UNSAFE_MSG)
        self.calls.append((name, *args))
        if self.raise_on == name:
            raise RuntimeError(f"simulated failure in {name}")

    def in_callback(self, name, fn):
        def run(*args):
            outer, self.context = self.context, name
            try:
                return fn(*args)
            finally:
                self.context = outer
        return run

    def calls_named(self, name):
        return [c for c in self.calls if c[0] == name]

    def _build(self) -> dict[str, types.ModuleType]:
        sim = self
        m = {n: types.ModuleType(n) for n in FL_MODULES}

        def vol_db(v):
            return -math.inf if v <= 0 else 20 * math.log10(v / 0.8) * 1.5

        mx = m["mixer"]
        mx.trackCount = lambda: (sim._rec("mixer.trackCount"), len(sim.tracks))[1]
        mx.getTrackName = lambda i: (sim._rec("mixer.getTrackName", i), sim.tracks[i]["name"])[1]
        mx.getTrackVolume = lambda i, mode=0: (sim._rec("mixer.getTrackVolume", i, mode),
                                               vol_db(sim.tracks[i]["vol"]) if mode else sim.tracks[i]["vol"])[1]
        mx.getTrackPan = lambda i: (sim._rec("mixer.getTrackPan", i), sim.tracks[i]["pan"])[1]
        mx.getTrackStereoSep = lambda i: (sim._rec("mixer.getTrackStereoSep", i), sim.tracks[i]["sep"])[1]
        mx.isTrackMuted = lambda i: (sim._rec("mixer.isTrackMuted", i), sim.tracks[i]["muted"])[1]
        mx.isTrackSolo = lambda i: (sim._rec("mixer.isTrackSolo", i), sim.tracks[i]["solo"])[1]
        mx.isTrackPluginValid = lambda i, s: (sim._rec("mixer.isTrackPluginValid", i, s), s in sim.tracks[i]["plugins"])[1]
        mx.getTrackPeaks = lambda i, mode: (sim._rec("mixer.getTrackPeaks", i, mode), sim.tracks[i]["peak"])[1]
        mx.getCurrentTempo = lambda asInt=False: (sim._rec("mixer.getCurrentTempo"), sim.bpm)[1]
        mx.getEqBandCount = lambda: (sim._rec("mixer.getEqBandCount"), sim.eq_bands)[1]
        mx.getEqGain = lambda i, b, mode=0: (sim._rec("mixer.getEqGain", i, b, mode),
                                             (sim.tracks[i]["eq"][b]["g"] - 0.5) * 36 if mode else sim.tracks[i]["eq"][b]["g"])[1]
        mx.getEqFrequency = lambda i, b, mode=0: (sim._rec("mixer.getEqFrequency", i, b, mode),
                                                  20 * 1000 ** sim.tracks[i]["eq"][b]["f"] if mode else sim.tracks[i]["eq"][b]["f"])[1]
        mx.getEqBandwidth = lambda i, b: (sim._rec("mixer.getEqBandwidth", i, b), sim.tracks[i]["eq"][b]["w"])[1]

        def setter(key, name):
            def f(i, v, pickupMode=None):
                sim._rec(name, i, v, pickupMode)
                sim.tracks[i][key] = v
            return f

        mx.setTrackVolume = setter("vol", "mixer.setTrackVolume")
        mx.setTrackPan = setter("pan", "mixer.setTrackPan")
        mx.setTrackStereoSep = setter("sep", "mixer.setTrackStereoSep")

        def eq_setter(key, name):
            def f(i, b, v):
                sim._rec(name, i, b, v)
                sim.tracks[i]["eq"][b][key] = v
            return f

        mx.setEqGain = eq_setter("g", "mixer.setEqGain")
        mx.setEqFrequency = eq_setter("f", "mixer.setEqFrequency")
        mx.setEqBandwidth = eq_setter("w", "mixer.setEqBandwidth")

        tr = m["transport"]

        def start():
            sim._rec("transport.start")
            sim.playing = not sim.playing

        def stop():
            sim._rec("transport.stop")
            sim.playing = False

        def record():
            sim._rec("transport.record")
            sim.recording = not sim.recording

        def set_pos(t, mode=-1):
            sim._rec("transport.setSongPos", t, mode)
            sim.pos = t

        tr.start, tr.stop, tr.record, tr.setSongPos = start, stop, record, set_pos
        tr.isPlaying = lambda: (sim._rec("transport.isPlaying"), sim.playing)[1]
        tr.isRecording = lambda: (sim._rec("transport.isRecording"), sim.recording)[1]
        tr.getSongPos = lambda mode=-1: (sim._rec("transport.getSongPos", mode), sim.pos)[1]
        tr.getLoopMode = lambda: (sim._rec("transport.getLoopMode"), sim.loop_mode)[1]

        ch = m["channels"]
        ch.channelCount = lambda globalCount=False: (sim._rec("channels.channelCount", globalCount), len(sim.channel_names))[1]
        ch.getChannelName = lambda i, useGlobalIndex=False: (sim._rec("channels.getChannelName", i, useGlobalIndex),
                                                             sim.channel_names[i])[1]

        def select_one(i, useGlobalIndex=False):
            sim._rec("channels.selectOneChannel", i, useGlobalIndex)
            sim.selected = i

        ch.selectOneChannel = select_one

        ge = m["general"]
        ge.getVersion = lambda: (sim._rec("general.getVersion"), sim.api)[1]
        ge.getRecPPQ = lambda: (sim._rec("general.getRecPPQ"), sim.ppq)[1]
        ge.saveUndo = lambda name, flags, update=True: sim._rec("general.saveUndo", name, flags)

        dv = m["device"]
        dv.getPortNumber = lambda: (sim._rec("device.getPortNumber"), sim.port)[1]
        dv.isAssigned = lambda: sim.assigned

        def midi_out_sysex(data):
            sim._rec("device.midiOutSysex", bytes(data))
            sim.sysex_out.append(bytes(data))

        dv.midiOutSysex = midi_out_sysex

        pl = m["plugins"]
        pl.getPluginName = lambda i, s=-1, userName=False, useGlobalIndex=False: (
            sim._rec("plugins.getPluginName", i, s), sim.tracks[i]["plugins"][s])[1]

        def show_window(w):
            sim._rec("ui.showWindow", w)
            sim.window = w

        m["ui"].showWindow = show_window
        m["midi"].PIM_None = 0
        return m


def load_device_script(sim: SimFL):
    """Import device_MixAssist.py with sim's modules standing in for FL's."""
    saved = {n: sys.modules.get(n) for n in FL_MODULES}
    sys.modules.update(sim.modules)
    try:
        name = f"device_MixAssist_sim_{next(_counter)}"
        spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for cb in ("OnSysEx", "OnIdle", "OnInit"):
            if hasattr(mod, cb):
                setattr(mod, cb, sim.in_callback(cb, getattr(mod, cb)))
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    return mod
