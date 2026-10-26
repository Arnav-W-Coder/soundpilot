"""Real FL transport: SysEx and short MIDI over two virtual MIDI ports (spec ADR-1, section 3.3).

The server sends on "MA to FL" (FL's input, where device_MixAssist.py listens) and receives on
"MA from FL" (FL's output for the script). Port names are matched by case-insensitive substring,
so macOS IAC ("IAC Driver MA to FL") and Windows loopMIDI ("MA to FL 1") both work.

``python -m mixassist.fl.bridge --check`` sends ``hello`` and prints the round trip (probe P1).
``--test-note`` plays a 1 s middle C on the selected channel and prints FL's tempo.
The other ``--probe`` modes support internal/probes.md; they print measurements and never decide
pass/fail for you.
"""
from __future__ import annotations

import argparse
import math
import queue
import statistics
import sys
import threading
import time

from mixassist.errors import ToolError

MA_PREFIX = bytes([0xF0, 0x7D, 0x4D, 0x41])
DEFAULT_TO_PORT = "MA to FL"
DEFAULT_FROM_PORT = "MA from FL"
MAX_PARTIAL_SYSEX = 64 * 1024

SETUP_HINT = (
    "Create two virtual MIDI ports named 'MA to FL' and 'MA from FL'. "
    "macOS: open Audio MIDI Setup > Window > Show MIDI Studio, double-click 'IAC Driver', tick "
    "'Device is online' and add the ports 'MA to FL' and 'MA from FL'. "
    "Windows: install loopMIDI and create both ports. "
    "Then copy fl_script/MixAssist to <Documents>/Image-Line/FL Studio/Settings/Hardware/MixAssist, "
    "and in FL Options > MIDI Settings enable input 'MA to FL' with controller type 'MixAssist' on "
    "port N and set output 'MA from FL' to the same port N (internal/probes.md)."
)


def _load_rtmidi(module=None):
    if module is not None:
        return module
    try:
        import rtmidi
    except ImportError as exc:
        raise ToolError("FL_NOT_CONNECTED", f"python-rtmidi is not installed ({exc})",
                        hint="pip install python-rtmidi") from exc
    return rtmidi


def find_port(names: list[str], wanted: str) -> int | None:
    """Index of the port whose name contains ``wanted`` (case-insensitive); exact match wins."""
    w = wanted.casefold()
    hits = [i for i, name in enumerate(names) if w in name.casefold()]
    if not hits:
        return None
    for i in hits:
        if names[i].casefold() == w:
            return i
    return hits[0]


def list_ports(rtmidi_module=None) -> dict[str, list[str]]:
    """MIDI port names visible to python-rtmidi: ``{"inputs": [...], "outputs": [...]}``."""
    rt = _load_rtmidi(rtmidi_module)
    try:
        midi_in, midi_out = rt.MidiIn(), rt.MidiOut()
    except Exception as exc:  # rtmidi raises its own SystemError subclasses
        raise ToolError("FL_NOT_CONNECTED", f"cannot open the MIDI system: {exc}", hint=SETUP_HINT) from exc
    try:
        return {"inputs": list(midi_in.get_ports()), "outputs": list(midi_out.get_ports())}
    finally:
        for port in (midi_in, midi_out):
            try:
                port.delete()
            except Exception:
                pass


class BridgeTransport:
    """Transport for FLClient over python-rtmidi (docs/interfaces.md)."""

    def __init__(self, to_port: str = DEFAULT_TO_PORT, from_port: str = DEFAULT_FROM_PORT, rtmidi_module=None):
        rt = _load_rtmidi(rtmidi_module)
        self.to_port = to_port
        self.from_port = from_port
        self._frames: queue.Queue[bytes] = queue.Queue()
        self._partial: bytearray | None = None
        self._send_lock = threading.Lock()
        self._closed = False
        self._out = None
        self._in = None
        try:
            self._out = rt.MidiOut()
            self._in = rt.MidiIn()
            outputs = list(self._out.get_ports())
            inputs = list(self._in.get_ports())
        except ToolError:
            raise
        except Exception as exc:
            self.close()
            raise ToolError("FL_NOT_CONNECTED", f"cannot open the MIDI system: {exc}", hint=SETUP_HINT) from exc

        out_index = find_port(outputs, to_port)
        in_index = find_port(inputs, from_port)
        missing = []
        if out_index is None:
            missing.append(f"output port '{to_port}' (have: {', '.join(outputs) or 'none'})")
        if in_index is None:
            missing.append(f"input port '{from_port}' (have: {', '.join(inputs) or 'none'})")
        if missing:
            self.close()
            raise ToolError("FL_NOT_CONNECTED", "MIDI port not found: " + "; ".join(missing), hint=SETUP_HINT)

        try:
            self._out.open_port(out_index)
            self._in.ignore_types(sysex=False, timing=True, active_sense=True)
            self._in.set_callback(self._on_message)
            self._in.open_port(in_index)
        except Exception as exc:
            self.close()
            raise ToolError("FL_NOT_CONNECTED", f"cannot open MIDI ports: {exc}", hint=SETUP_HINT) from exc
        self.out_name = outputs[out_index]
        self.in_name = inputs[in_index]

    # ------------------------------------------------------------- receiving
    def _on_message(self, event, data=None) -> None:
        message = event[0]
        if not message:
            return
        if self._partial is not None:
            # SysEx split across callbacks: keep appending until F7.
            self._partial.extend(message)
            if len(self._partial) > MAX_PARTIAL_SYSEX:
                self._partial = None
            elif message[-1] == 0xF7:
                frame, self._partial = bytes(self._partial), None
                self._accept(frame)
            return
        if message[0] == 0xF0:
            if message[-1] == 0xF7:
                self._accept(bytes(message))
            else:
                self._partial = bytearray(message)
        # Anything else (notes, clock, other devices) is not for us.

    def _accept(self, frame: bytes) -> None:
        if frame.startswith(MA_PREFIX):
            self._frames.put(frame)

    def recv_sysex(self, timeout: float) -> bytes | None:
        """Next MixAssist frame (F0 7D 4D 41 ... F7) within ``timeout`` seconds, else None."""
        try:
            if timeout <= 0:
                return self._frames.get_nowait()
            return self._frames.get(timeout=timeout)
        except queue.Empty:
            return None

    # --------------------------------------------------------------- sending
    def _send(self, message: list[int]) -> None:
        if self._closed:
            raise ToolError("FL_NOT_CONNECTED", "bridge is closed", hint=SETUP_HINT)
        try:
            with self._send_lock:
                self._out.send_message(message)
        except Exception as exc:
            raise ToolError("FL_NOT_CONNECTED", f"MIDI send failed: {exc}", hint=SETUP_HINT) from exc

    def send_sysex(self, frame: bytes) -> None:
        frame = bytes(frame)
        if len(frame) < 2 or frame[0] != 0xF0 or frame[-1] != 0xF7 or any(b > 0x7F for b in frame[1:-1]):
            raise ValueError("SysEx frame must be F0 <7-bit data> F7")
        self._send(list(frame))

    def send_short(self, status: int, d1: int, d2: int) -> None:
        if not 0x80 <= status <= 0xEF:
            raise ValueError(f"not a channel status byte: {status:#x}")
        self._send([status, d1 & 0x7F, d2 & 0x7F])

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._in is not None:
            for step in (self._in.cancel_callback, self._in.close_port, self._in.delete):
                try:
                    step()
                except Exception:
                    pass
        if self._out is not None:
            for step in (self._out.close_port, self._out.delete):
                try:
                    step()
                except Exception:
                    pass

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ------------------------------------------------------------------ probes CLI
def _ms(seconds: float) -> str:
    return f"{seconds * 1000:.1f} ms"


def _timed(fl, op: str, args: dict | None = None, timeout: float = 1.0) -> tuple[dict, float]:
    t0 = time.perf_counter()
    reply = fl.request(op, args or {}, timeout=timeout)
    return reply, time.perf_counter() - t0


def probe_hello(fl, count: int = 10, out=print) -> dict:
    """P1: hello round trips."""
    rtts = []
    reply = {}
    for _ in range(count):
        reply, rtt = _timed(fl, "hello")
        rtts.append(rtt)
    api = int(reply.get("api") or 0)
    out(f"hello x{count}: median {_ms(statistics.median(rtts))}, max {_ms(max(rtts))}, min {_ms(min(rtts))}")
    out(f"FL API version: {api} (built-in EQ needs >= 35), script port: {reply.get('port')}")
    out("P1 pass criterion: reply < 100 ms and api >= 35 logged")
    return {"api": api, "port": reply.get("port"), "rtt_ms": [round(r * 1000, 2) for r in rtts]}


def probe_sizes(fl, sizes=(256, 512, 768, 1000, 1024), out=print) -> dict:
    """P2: replies padded to exact frame sizes (probe-only device op ``pad``)."""
    results = {}
    for size in sizes:
        try:
            reply, rtt = _timed(fl, "pad", {"size": size})
            pad = reply.get("pad") or ""
            intact = set(pad) <= {"x"}
            results[size] = "intact" if intact else "corrupted"
            out(f"reply {size} B: {results[size]} ({len(pad)} pad chars, {_ms(rtt)})")
        except ToolError as exc:
            results[size] = f"{exc.code}: {exc.message}"
            out(f"reply {size} B: {results[size]}")
    out("P2 pass criterion: intact at 1024 B")
    return results


def _read_track(fl, track: int) -> dict:
    reply = fl.request("tracks", {"start": track, "count": 1})
    if f"n{track}" not in reply:
        raise ToolError("NOT_FOUND", f"no mixer track {track}")
    return {"name": reply[f"n{track}"], "v": reply[f"v{track}"], "vd": reply.get(f"vd{track}"),
            "p": reply[f"p{track}"], "s": reply[f"s{track}"]}


def probe_fader(fl, track: int, step: float = 0.05, out=print) -> list[tuple[float, float | None]]:
    """P3: fader sweep 0..1, reading getTrackVolume(i,1) on a later op. Restores the fader."""
    orig = _read_track(fl, track)
    out(f"track {track} '{orig['name']}': start v={orig['v']:.4f} ({orig['vd']} dB)")
    table = []
    try:
        n = int(round(1.0 / step))
        for k in range(n + 1):
            v = round(min(1.0, k * step), 6)
            fl.request("set", {"track": track, "v": v, "pk": 0})
            fl.request("hello")  # readback on a later op [PF]
            db = _read_track(fl, track)["vd"]
            table.append((v, db))
            out(f"  v={v:.2f} -> {db} dB")
    finally:
        fl.request("set", {"track": track, "v": orig["v"], "pk": 0})
        fl.request("hello")
    finite = [db for _, db in table if db is not None and math.isfinite(db)]
    monotonic = all(a <= b for a, b in zip(finite, finite[1:]))
    out(f"monotonic: {monotonic}")
    out("VOLUME_TABLE = " + repr([(v, db) for v, db in table]))
    out("P3 pass criterion: monotonic, 0.8 -> 0.0 +/-0.1 dB; paste the table into mixassist/fl/curves.py")
    return table


def probe_eq(fl, track: int, out=print) -> dict:
    """P4: dump the built-in EQ, then set/readback band 0 gain. Restores the band."""
    eq = fl.request("eq_get", {"track": track})
    bands = int(eq.get("bands") or 0)
    for b in range(bands):
        out(f"  band {b}: gain {eq.get(f'g{b}')} ({eq.get(f'gd{b}')} dB), freq {eq.get(f'f{b}')} "
            f"({eq.get(f'fh{b}')} Hz), width {eq.get(f'w{b}')}")
    hz = [eq.get(f"fh{b}") for b in range(bands)]
    out(f"band count: {bands}; Hz order low<mid<high: {all(a < b for a, b in zip(hz, hz[1:]))}")
    if bands:
        g0 = eq["g0"]
        try:
            for g in (0.0, 0.25, 0.75, 1.0):
                fl.request("eq_set", {"track": track, "band": 0, "g": g, "pk": 0})
                fl.request("hello")
                rb = fl.request("eq_get", {"track": track})
                out(f"  set band 0 g={g} -> read g={rb.get('g0')} ({rb.get('gd0')} dB)")
        finally:
            fl.request("eq_set", {"track": track, "band": 0, "g": g0, "pk": 0})
            fl.request("hello")
    out("P4 pass criterion: 3 bands ordered low < mid < high in Hz; note the gain range in dB")
    return eq


def probe_readback(fl, track: int, out=print) -> dict:
    """P5: is a write visible on the very next op, or only later? Restores the fader."""
    orig = _read_track(fl, track)
    target = 0.6 if abs(orig["v"] - 0.6) > 0.1 else 0.4
    try:
        fl.request("set", {"track": track, "v": target, "pk": 0})
        reads = [_read_track(fl, track)["v"] for _ in range(3)]
    finally:
        fl.request("set", {"track": track, "v": orig["v"], "pk": 0})
        fl.request("hello")
    seen = [abs(v - target) < 1e-3 for v in reads]
    out(f"wrote v={target}; reads on ops +1, +2, +3 after the set: {reads} (new value seen: {seen})")
    out("P5 pass criterion: next-op returns the new value (otherwise: 1-op delay plus retry x3)")
    return {"target": target, "reads": reads}


def probe_peaks(fl, seconds: float = 5.0, tracks: int = 32, out=print) -> dict:
    """P6: start playback, poll peaks for `tracks` tracks as fast as possible, stop."""
    fl.request("transport", {"cmd": "play"})
    maxima: dict[str, float] = {}
    polls = 0
    t0 = time.perf_counter()
    try:
        while time.perf_counter() - t0 < seconds:
            reply = fl.request("peaks", {"start": 0, "count": min(32, tracks)})
            polls += 1
            for k, v in reply.items():
                if v is not None:
                    maxima[k] = max(maxima.get(k, 0.0), float(v))
    finally:
        fl.request("transport", {"cmd": "stop"})
    elapsed = time.perf_counter() - t0
    rate = polls / elapsed if elapsed else 0.0
    active = {k: round(v, 4) for k, v in maxima.items() if v > 0}
    out(f"{polls} polls in {elapsed:.2f} s = {rate:.1f} polls/s for {min(32, tracks)} tracks")
    out(f"tracks with peak > 0: {active or 'none'}")
    out("P6 pass criterion: > 0 on an active track; >= 20 polls/s for 32 tracks")
    return {"polls": polls, "seconds": elapsed, "polls_per_s": rate, "max": active}


def probe_notes(fl, transport: BridgeTransport, channel: int, record: bool = True, out=print) -> dict:
    """P8: select a channel, optionally arm recording, play a C major scale over the bridge port."""
    fl.request("select", {"channel": channel})
    tr = fl.request("transport", {"cmd": "pos"})
    bpm = float(tr.get("bpm") or 120.0)
    eighth = 30.0 / bpm
    if record:
        fl.request("transport", {"cmd": "rec_on"})
    fl.request("transport", {"cmd": "play", "pos_ticks": 0})
    sent = []
    try:
        time.sleep(4 * 60.0 / bpm)  # one bar of 4/4 pre-roll
        for pitch in (60, 62, 64, 65, 67, 69, 71, 72):
            transport.send_short(0x90, pitch, 100)
            sent.append(pitch)
            time.sleep(eighth * 0.9)
            transport.send_short(0x80, pitch, 0)
            time.sleep(eighth * 0.1)
    finally:
        transport.send_short(0xB0, 123, 0)
        fl.request("transport", {"cmd": "stop"})
        if record:
            fl.request("transport", {"cmd": "rec_off"})
    out(f"sent {len(sent)} notes at {bpm} BPM on channel {channel} (record={record})")
    out("P8: check the channel's piano roll for 8 notes starting at bar 2; repeat with FL unfocused")
    return {"bpm": bpm, "notes": sent}


def send_test_note(transport: BridgeTransport, pitch: int = 60, velocity: int = 100, seconds: float = 1.0,
                   sleep=None, out=print) -> None:
    """Note on, wait, note off on MIDI channel 1: an audibility check that needs neither Gemma nor SysEx."""
    transport.send_short(0x90, pitch, velocity)
    try:
        (sleep or time.sleep)(seconds)
    finally:
        transport.send_short(0x80, pitch, 0)
    out(f"sent note {pitch} (velocity {velocity}) for {seconds:g} s on MIDI channel 1 of '{transport.out_name}'")
    out("FL plays it on the selected Channel Rack channel. If its LED blinked but you heard nothing, check that "
        "channel's instrument and volume, its mixer track, and the master level.")


def _report_tempo(fl, timeout: float, out=print) -> None:
    try:
        bpm = fl.request("transport", {"cmd": "pos"}, timeout=timeout)["bpm"]
    except ToolError as exc:
        out(f"tempo: not read ({exc.code}: {exc.message})")
        return
    out(f"tempo: FL reports {bpm:g} BPM")
    if fl._warned_tempo_x1000:
        out("warning: the installed device script reports BPM x 1000 and is outdated; re-copy "
            "fl_script/MixAssist/device_MixAssist.py and restart any backend started before commit bd21eab")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mixassist.fl.bridge", description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="list MIDI ports and exit")
    parser.add_argument("--check", action="store_true", help="send hello and print the round trip (P1)")
    parser.add_argument("--probe", choices=["p1", "p2", "p3", "p4", "p5", "p6", "p8"], help="run one probe helper")
    parser.add_argument("--track", type=int, default=1, help="mixer track for p3/p4/p5 (default 1)")
    parser.add_argument("--channel", type=int, default=0, help="channel rack index for p8 (global, default 0)")
    parser.add_argument("--seconds", type=float, default=5.0, help="p6 duration")
    parser.add_argument("--no-record", action="store_true", help="p8: play live without recording")
    parser.add_argument("--test-note", action="store_true",
                        help="play one note on the selected channel (default: middle C for 1 s) and read FL's tempo")
    parser.add_argument("--pitch", type=int, default=60, help="--test-note pitch (default 60, middle C)")
    parser.add_argument("--note-seconds", type=float, default=1.0, help="--test-note length (default 1 s)")
    parser.add_argument("--to-port", default=DEFAULT_TO_PORT)
    parser.add_argument("--from-port", default=DEFAULT_FROM_PORT)
    parser.add_argument("--timeout", type=float, default=1.0)
    args = parser.parse_args(argv)

    if not 0 <= args.pitch <= 127 or not 0 < args.note_seconds <= 30:
        parser.error("--pitch must be 0..127 and --note-seconds 0..30")
    if args.list or not (args.check or args.probe or args.test_note):
        try:
            ports = list_ports()
        except ToolError as exc:
            print(f"{exc.code}: {exc.message}", file=sys.stderr)
            return 2
        print("MIDI inputs (server reads 'MA from FL'):", ports["inputs"] or "none")
        print("MIDI outputs (server writes 'MA to FL'):", ports["outputs"] or "none")
        if not (args.check or args.probe or args.test_note):
            return 0

    from mixassist.fl.client import FLClient

    try:
        transport = BridgeTransport(args.to_port, args.from_port)
    except ToolError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"hint: {exc.hint}", file=sys.stderr)
        return 2
    fl = FLClient(transport, "bridge")
    print(f"ports: out '{transport.out_name}', in '{transport.in_name}'")
    try:
        if args.check:
            reply, rtt = _timed(fl, "hello", timeout=args.timeout)
            print(f"hello round trip: {_ms(rtt)}; FL API {reply.get('api')}, script port {reply.get('port')}")
        probe = args.probe
        if probe == "p1":
            probe_hello(fl)
        elif probe == "p2":
            probe_sizes(fl)
        elif probe == "p3":
            probe_fader(fl, args.track)
        elif probe == "p4":
            probe_eq(fl, args.track)
        elif probe == "p5":
            probe_readback(fl, args.track)
        elif probe == "p6":
            probe_peaks(fl, args.seconds)
        elif probe == "p8":
            probe_notes(fl, transport, args.channel, record=not args.no_record)
        if args.test_note:
            send_test_note(transport, args.pitch, seconds=args.note_seconds)
            _report_tempo(fl, args.timeout)
    except ToolError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"hint: {exc.hint}", file=sys.stderr)
        return 1
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
