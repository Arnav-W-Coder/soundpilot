"""In-memory FL Studio stand-in (spec §3.4) implementing the §3.3 op table.

Quirks reproduced on purpose (code that relies on their absence must fail tests):

- Pickup: a setter given pickup mode ``pk != 0`` is ignored when the change exceeds 0.05 normalized.
  The wire arg ``pk`` is optional (default 0, which is what the device script always uses).
- Delayed readback: ``set``/``eq_set`` writes are committed only after the NEXT op has been handled,
  so a read in that next op still returns the old value. Read at least two ops after a write.
- Built-in EQ is exactly 3 bands; band >= 3 gives ``err``. Norm <-> dB/Hz mapping is a table.
- Volume curve is a monotonic table with 0.8 -> 0.0 dB.
- Peaks are instantaneous, sampled at poll time from ``peak_script`` (seconds since transport play).
- Transport clock: pos_ticks = start + (now - t_start) * ppq * bpm / 60, reported 5 ms late;
  in pattern mode (loop_mode 0) the position wraps at the pattern length.
- ``tempo_x1000=True`` reports ``bpm`` as BPM * 1000, the raw ``mixer.getCurrentTempo()`` value
  that real FL returns (FL API 45) and that device scripts older than the fix pass through.
- ``transport_unsafe=N`` makes the next N transport changes (anything but a pure ``pos`` read) reply
  ``err`` "transport failed: RuntimeError: Operation unsafe at current time", what real FL API 45 did
  for ``transport.record()`` inside OnSysEx with device scripts that called it directly.
- ``transport_defer_ops=N`` models the current device script queueing transport changes for OnIdle:
  a change applies only after N more ops, and replies carry ``queued`` (pending count) meanwhile.
  A huge N models a build where OnIdle never fires.
"""
from __future__ import annotations

import queue
import re
import time
from typing import Callable

from mixassist.fl import curves
from mixassist.fl.protocol import decode, encode

PICKUP_THRESHOLD = 0.05
REPORT_LAG_S = 0.005
DEFAULT_NAME = re.compile(r"^Insert (\d+)$")


class VirtualClock:
    """Manually advanced clock for deterministic tests (``FLClient.sleep`` advances it)."""

    def __init__(self, start: float = 1000.0):
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += max(0.0, float(seconds))


class SkipClock:
    """Real-time clock whose ``advance`` skips ahead instead of blocking (demo server: a 30 s
    peak watch on FakeFL returns at once, while code that spins on the clock still works)."""

    def __init__(self):
        self.offset = 0.0

    def __call__(self) -> float:
        return time.monotonic() + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += max(0.0, float(seconds))


class FakeFL:
    def __init__(
        self,
        tracks: list[dict],
        eq_curves: dict | None = None,
        vol_curve: curves.Table | None = None,
        peak_script: dict[int, list[tuple[float, float]]] | None = None,
        bpm: float = 120.0,
        ppq: int = 96,
        loop_mode: int = 1,
        channels: list[str] | None = None,
        api: int = 36,
        clock: Callable[[], float] = time.monotonic,
        pattern_beats: float = 16.0,
        port: int = 0,
        tempo_x1000: bool = False,
        transport_unsafe: int = 0,
        transport_defer_ops: int | None = None,
    ):
        self.vol_curve = vol_curve or curves.VOLUME_TABLE
        eq_curves = eq_curves or {}
        self.eq_gain_curve = eq_curves.get("gain", curves.EQ_GAIN_TABLE)
        self.eq_freq_curve = eq_curves.get("freq", curves.EQ_FREQ_TABLE)
        self.peak_script = {int(k): sorted(v) for k, v in (peak_script or {}).items()}
        self.bpm = float(bpm)
        self.ppq = int(ppq)
        self.loop_mode = int(loop_mode)
        self.channels = list(channels if channels is not None else DEFAULT_CHANNELS)
        self.api = int(api)
        self.clock = clock
        self.pattern_beats = float(pattern_beats)
        self.port = int(port)
        self.tempo_x1000 = bool(tempo_x1000)
        self.transport_unsafe = int(transport_unsafe)
        self.transport_defer_ops = transport_defer_ops

        self.tracks = [self._make_track(t, i) for i, t in enumerate(tracks)]
        self.tracks.append(self._make_track({"name": "Current"}, len(self.tracks)))  # "current" track, excluded in v1

        self.playing = False
        self.recording = False
        self.start_ticks = 0.0
        self.t_start = 0.0
        self.selected_channel = 0
        self.recorded_notes: list[dict] = []
        self.recorded_offs: list[dict] = []
        self.sounding_notes: set[tuple[int, int]] = set()
        self.ops: list[str] = []
        self._pending: list[Callable[[], None]] = []
        self._transport_queue: list[list] = []  # [ops_left, cmd, pos_ticks]

    # ------------------------------------------------------------------ setup
    def _make_track(self, spec: dict, index: int) -> dict:
        if "volume_db" in spec:
            vol = curves.invert(self.vol_curve, float(spec["volume_db"]))
        else:
            vol = float(spec.get("volume", curves.DEFAULT_VOLUME_NORM))
        plugins = spec.get("plugins", [])
        if isinstance(plugins, dict):
            slots = {int(k): str(v) for k, v in plugins.items()}
        else:
            slots = {i: str(name) for i, name in enumerate(plugins)}
        eq = []
        eq_spec = spec.get("eq") or []
        for band in range(3):
            b = eq_spec[band] if band < len(eq_spec) else {}
            g = b.get("g")
            if g is None:
                g = curves.invert(self.eq_gain_curve, float(b["gain_db"])) if "gain_db" in b else curves.EQ_DEFAULT_GAIN_NORM
            f = b.get("f")
            if f is None:
                f = curves.invert(self.eq_freq_curve, float(b["freq_hz"])) if "freq_hz" in b else curves.EQ_DEFAULT_FREQ_NORM[band]
            eq.append({"g": float(g), "f": float(f), "w": float(b.get("w", curves.EQ_DEFAULT_WIDTH_NORM))})
        return {
            "name": str(spec.get("name", f"Insert {index}")),
            "vol": vol,
            "pan": float(spec.get("pan", 0.0)),
            "sep": float(spec.get("sep", 0.0)),
            "muted": bool(spec.get("muted", False)),
            "solo": bool(spec.get("solo", False)),
            "plugins": slots,
            "eq": eq,
        }

    # -------------------------------------------------------------- transport
    @property
    def ticks_per_second(self) -> float:
        return self.ppq * self.bpm / 60.0

    def _wrap(self, ticks: float) -> float:
        if self.loop_mode == 0:
            length = self.pattern_beats * self.ppq
            if length > 0:
                return ticks % length
        return ticks

    def true_ticks(self, t: float | None = None) -> float:
        """Actual song position at time t (no reporting lag)."""
        if not self.playing:
            return self.start_ticks
        t = self.clock() if t is None else t
        return self._wrap(self.start_ticks + max(0.0, t - self.t_start) * self.ticks_per_second)

    def reported_ticks(self) -> float:
        if not self.playing:
            return self.start_ticks
        elapsed = max(0.0, self.clock() - self.t_start - REPORT_LAG_S)
        return self._wrap(self.start_ticks + elapsed * self.ticks_per_second)

    def peak_at(self, track: int, t_since_play: float) -> float:
        script = self.peak_script.get(track)
        if not script or not self.playing:
            return 0.0
        value = 0.0
        for t_s, linear in script:
            if t_s <= t_since_play:
                value = float(linear)
            else:
                break
        return value

    # ------------------------------------------------------------------ notes
    def note(self, status: int, d1: int, d2: int, t: float) -> None:
        kind, ch = status & 0xF0, status & 0x0F
        if kind == 0x90 and d2 > 0:
            self.sounding_notes.add((ch, d1))
            if self.recording and self.playing:
                self.recorded_notes.append(
                    {"pitch": d1, "velocity": d2, "tick": self.true_ticks(t), "t": t, "channel": self.selected_channel}
                )
        elif kind == 0x80 or (kind == 0x90 and d2 == 0):
            self.sounding_notes.discard((ch, d1))
            if self.recording and self.playing:
                self.recorded_offs.append({"pitch": d1, "tick": self.true_ticks(t), "t": t})
        elif kind == 0xB0 and d1 == 123:
            self.sounding_notes = {n for n in self.sounding_notes if n[0] != ch}

    # ------------------------------------------------------------------- ops
    def handle(self, frame: bytes) -> bytes | None:
        try:
            msg_id, op, args = decode(frame)
        except ValueError:
            return None  # FL drops malformed SysEx
        committed, self._pending = self._pending, []
        self._run_transport_queue()
        self.ops.append(op)
        try:
            handler = getattr(self, f"_op_{op}", None)
            if handler is None:
                reply_op, reply = "err", {"msg": f"unknown op {op}"}
            else:
                reply_op, reply = "ok", handler(args)
        except FakeError as exc:
            reply_op, reply = "err", {"msg": str(exc)}
        finally:
            for apply in committed:
                apply()
        return encode(msg_id, reply_op, reply)

    def _track(self, args: dict, key: str = "track") -> int:
        if key not in args or args[key] is None:
            raise FakeError(f"missing {key}")
        i = int(args[key])
        if not 0 <= i < len(self.tracks):
            raise FakeError(f"track {i} out of range")
        return i

    def _op_hello(self, args: dict) -> dict:
        return {"api": self.api, "port": self.port}

    def _op_tracks(self, args: dict) -> dict:
        start, count = int(args.get("start", 0)), int(args.get("count", 8))
        if count > 8:
            raise FakeError("count > 8")
        out: dict = {"total": len(self.tracks)}
        for i in range(max(0, start), min(len(self.tracks), start + count)):
            t = self.tracks[i]
            out[f"n{i}"] = t["name"]
            out[f"v{i}"] = round(t["vol"], 6)
            out[f"vd{i}"] = round(curves.interp(self.vol_curve, t["vol"]), 4)
            out[f"p{i}"] = round(t["pan"], 6)
            out[f"s{i}"] = round(t["sep"], 6)
            out[f"m{i}"] = t["muted"]
            out[f"so{i}"] = t["solo"]
        return out

    def _op_fx(self, args: dict) -> dict:
        i = self._track(args)
        out: dict = {}
        for slot in range(10):
            name = self.tracks[i]["plugins"].get(slot)
            if name is not None:
                out[f"s{slot}"] = True
                out[f"n{slot}"] = name
        return out

    def _op_eq_get(self, args: dict) -> dict:
        if self.api < 35:
            raise FakeError("EQ API needs api >= 35")
        i = self._track(args)
        out: dict = {}
        for b, band in enumerate(self.tracks[i]["eq"]):
            out[f"g{b}"] = round(band["g"], 6)
            out[f"gd{b}"] = round(curves.interp(self.eq_gain_curve, band["g"]), 4)
            out[f"f{b}"] = round(band["f"], 6)
            out[f"fh{b}"] = round(curves.interp(self.eq_freq_curve, band["f"]), 2)
            out[f"w{b}"] = round(band["w"], 6)
        return out

    def _pickup_ok(self, current: float, new: float, pk: int) -> bool:
        return pk == 0 or abs(new - current) <= PICKUP_THRESHOLD

    def _op_set(self, args: dict) -> dict:
        i = self._track(args)
        pk = int(args.get("pk") or 0)
        track = self.tracks[i]
        for key, field, lo, hi in (("v", "vol", 0.0, 1.0), ("p", "pan", -1.0, 1.0), ("s", "sep", -1.0, 1.0)):
            if args.get(key) is None:
                continue
            new = min(hi, max(lo, float(args[key])))
            if self._pickup_ok(track[field], new, pk):
                self._pending.append(lambda t=track, f=field, v=new: t.__setitem__(f, v))
        return {}

    def _op_eq_set(self, args: dict) -> dict:
        if self.api < 35:
            raise FakeError("EQ API needs api >= 35")
        i = self._track(args)
        band = int(args.get("band", -1))
        if not 0 <= band < 3:
            raise FakeError(f"eq band {band} out of range (3 bands)")
        pk = int(args.get("pk") or 0)
        state = self.tracks[i]["eq"][band]
        for key in ("g", "f", "w"):
            if args.get(key) is None:
                continue
            new = min(1.0, max(0.0, float(args[key])))
            if self._pickup_ok(state[key], new, pk):
                self._pending.append(lambda s=state, k=key, v=new: s.__setitem__(k, v))
        return {}

    def _op_peaks(self, args: dict) -> dict:
        start, count = int(args.get("start", 0)), int(args.get("count", 32))
        if count > 32:
            raise FakeError("count > 32")
        since_play = self.clock() - self.t_start
        out: dict = {}
        for i in range(max(0, start), min(len(self.tracks), start + count)):
            out[f"p{i}"] = float(self.peak_at(i, since_play))
        return out

    def _run_transport_queue(self) -> None:
        """OnIdle between ops: apply queued transport changes that are due, in order."""
        for item in self._transport_queue:
            item[0] -= 1
        while self._transport_queue and self._transport_queue[0][0] <= 0:
            _, cmd, pos = self._transport_queue.pop(0)
            self._apply_transport(cmd, pos)

    def _op_transport(self, args: dict) -> dict:
        cmd = args.get("cmd")
        pos = args.get("pos_ticks")
        if cmd not in ("play", "stop", "rec_on", "rec_off", "pos"):
            raise FakeError(f"unknown transport cmd {cmd!r}")
        changes = cmd != "pos" or pos is not None
        if changes and self.transport_unsafe > 0:
            self.transport_unsafe -= 1
            raise FakeError("transport failed: RuntimeError: Operation unsafe at current time")
        if changes and (self.transport_defer_ops is not None or self._transport_queue):
            self._transport_queue.append([int(self.transport_defer_ops or 0), cmd, pos])
        elif changes:
            self._apply_transport(cmd, pos)
        out = {
            "playing": self.playing,
            "recording": self.recording,
            "pos_ticks": int(round(self.reported_ticks())),
            "loop_mode": self.loop_mode,
            "bpm": self.bpm * 1000.0 if self.tempo_x1000 else self.bpm,
            "ppq": self.ppq,
        }
        if self._transport_queue:
            out["queued"] = len(self._transport_queue)
        return out

    def _apply_transport(self, cmd: str, pos) -> None:
        now = self.clock()
        if cmd == "play":
            if pos is not None:
                self.start_ticks = float(pos)
            elif self.playing:
                self.start_ticks = self.true_ticks(now)
            self.t_start = now
            self.playing = True
        elif cmd == "stop":
            self.playing = False
        elif cmd == "rec_on":
            self.recording = True
        elif cmd == "rec_off":
            self.recording = False
        elif cmd == "pos":
            if pos is not None:
                self.start_ticks = float(pos)
                self.t_start = now

    def _op_channels(self, args: dict) -> dict:
        start, count = int(args.get("start", 0)), int(args.get("count", 16))
        if count > 16:
            raise FakeError("count > 16")
        out: dict = {"total": len(self.channels)}
        for i in range(max(0, start), min(len(self.channels), start + count)):
            out[f"n{i}"] = self.channels[i]
        return out

    def _op_select(self, args: dict) -> dict:
        ch = int(args.get("channel", -1))
        if not 0 <= ch < len(self.channels):
            raise FakeError(f"channel {ch} out of range")
        self.selected_channel = ch
        return {}


class FakeError(Exception):
    pass


class FakeTransport:
    """Transport that delivers frames straight to a FakeFL (see docs/interfaces.md)."""

    def __init__(self, fake: FakeFL):
        self.fake = fake
        self._replies: queue.Queue[bytes] = queue.Queue()

    def send_sysex(self, frame: bytes) -> None:
        reply = self.fake.handle(frame)
        if reply is not None:
            self._replies.put(reply)

    def recv_sysex(self, timeout: float) -> bytes | None:
        try:
            return self._replies.get(timeout=max(0.0, timeout)) if timeout > 0 else self._replies.get_nowait()
        except queue.Empty:
            return None

    def send_short(self, status: int, d1: int, d2: int) -> None:
        self.fake.note(status, d1, d2, self.fake.clock())

    def clock(self) -> float:
        return self.fake.clock()

    def sleep(self, seconds: float) -> None:
        if hasattr(self.fake.clock, "advance"):
            self.fake.clock.advance(seconds)
        elif seconds > 0:
            time.sleep(seconds)

    def close(self) -> None:
        pass


DEFAULT_CHANNELS = ["Kick", "Snare", "Hat", "Bass", "Lead Synth", "Pad", "Vox Chop", "FX Riser"]

DEMO_TRACKS = [
    {"name": "Master", "volume_db": 0.0, "plugins": ["Fruity Limiter"]},
    {"name": "Kick", "volume_db": 0.0, "plugins": ["Fruity Parametric EQ 2", "Fruity Compressor"]},
    {"name": "Snare", "volume_db": -2.5, "pan": 0.0, "plugins": ["Fruity Reeverb 2"]},
    {"name": "Clap", "volume_db": -4.0, "pan": 0.1, "plugins": []},
    {"name": "Hat Open", "volume_db": -8.0, "pan": 0.3, "plugins": ["Fruity Filter"]},
    {"name": "Hat Closed", "volume_db": -7.0, "pan": -0.25, "plugins": []},
    {"name": "Bass", "volume_db": -3.0, "plugins": ["Fruity Parametric EQ 2", "Fruity Compressor", "Fruity Blood Overdrive"],
     "eq": [{"gain_db": 2.0, "freq_hz": 80.0}, {}, {}]},
    {"name": "Sub", "volume_db": -6.0, "sep": 0.0, "plugins": []},
    {"name": "Lead Synth", "volume_db": -5.0, "pan": -0.1, "sep": 0.2, "plugins": ["Fruity Delay 3", "Fruity Reeverb 2"]},
    {"name": "Pad", "volume_db": -10.0, "sep": 0.5, "plugins": ["Fruity Chorus"],
     "eq": [{"gain_db": -3.0}, {}, {"gain_db": 1.5}]},
    {"name": "Vocal", "volume_db": -1.5, "plugins": ["Fruity Parametric EQ 2", "Fruity Compressor", "Fruity Reeverb 2"]},
    {"name": "FX", "volume_db": -12.0, "pan": 0.5, "plugins": ["Fruity Flanger"]},
    {"name": "Reverb Bus", "volume_db": -9.0, "sep": 0.4, "plugins": ["Fruity Reeverb 2"]},
]

N_DEFAULT_INSERTS = 115

DEMO_PEAK_SCRIPT: dict[int, list[tuple[float, float]]] = {
    0: [(0.0, 0.80)],
    1: [(0.0, 0.5)],
    2: [(0.0, 0.45)],
    3: [(0.0, 0.3)],
    4: [(0.0, 0.2)],
    5: [(0.0, 0.22)],
    6: [(0.0, 0.6)],
    7: [(0.0, 0.4)],
    8: [(0.0, 0.5), (2.0, 1.12), (2.2, 0.5)],  # Lead Synth: 200 ms burst at +0.98 dBFS
    9: [(0.0, 0.25)],
    10: [(0.0, 0.55)],
    11: [(0.0, 0.15)],
    12: [(0.0, 0.2)],
}


def demo_tracks(n_default: int = N_DEFAULT_INSERTS) -> list[dict]:
    tracks = [dict(t) for t in DEMO_TRACKS]
    start = len(tracks)
    tracks += [{"name": f"Insert {i}"} for i in range(start, start + n_default)]
    return tracks


def default_fake(**overrides) -> FakeFL:
    """Demo mix: Master + 12 named tracks + 115 default 'Insert N' tracks (+ the current track)."""
    kwargs = {"tracks": demo_tracks(), "peak_script": DEMO_PEAK_SCRIPT}
    kwargs.update(overrides)
    return FakeFL(**kwargs)


def is_default_name(name: str, index: int) -> bool:
    m = DEFAULT_NAME.match(name.strip())
    return bool(m) and int(m.group(1)) == index
