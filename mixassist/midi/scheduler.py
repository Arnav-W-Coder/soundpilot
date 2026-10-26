"""F8 scheduler: play notes live on the selected channel, or record them at a bar in FL.

Talks to FL only through an FLClient (`request`, `note`, `now`) from mixassist.fl.
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from mixassist.errors import ToolError
from mixassist.fl.playback import NOT_CONFIRMED_HINT, describe, transport, wait_transport

NOTE_ON = 0x90
NOTE_OFF = 0x80
CONTROL_CHANGE = 0xB0
ALL_NOTES_OFF = 123

SPIN_S = 0.002            # sleep until 2 ms before the target, then spin
SPIN_STALL_READS = 1000
LIVE_LEAD_S = 0.05        # first live event this long after the transport read
SYNC_POLLS = 6
SYNC_POLL_SPACING_S = 0.02
TAIL_S = 0.05             # after the last note-off, before stop/rec_off
MAX_TAKE_S = 600.0
RECORD_CONFIRM_S = 1.0    # rec_on + play must read back as recording and playing within this
STOP_CONFIRM_S = 0.5
MIN_BPM, MAX_BPM = 10.0, 999.0
# A raw getCurrentTempo() value (BPM x 1000) shrinks every note ~1000x: the channel LED blinks but
# most instruments make no sound. Notes this short are flagged in `warnings`.
MIN_AUDIBLE_NOTE_S = 0.010

PATTERN_MODE_WARNING = (
    "FL is in pattern mode (loop mode 0): the take goes into the current pattern and the transport "
    "loops at the pattern length, so bars past the pattern end wrap. Switch to Song mode to record at a bar."
)
FOCUS_WARNING = (
    "PROBE P8 (unverified): FL may only receive notes from the 'MA to FL' port while its window is focused; "
    "keep FL in front during the take."
)


@dataclass(frozen=True)
class PlayNote:
    pitch: int
    start_beat: float
    length_beats: float
    velocity: int = 100


def precise_sleep_until(target: float, now: Callable[[], float], sleep: Callable[[float], None] = time.sleep) -> None:
    last, stalled = None, 0
    while True:
        t = now()
        remaining = target - t
        if remaining <= 0:
            return
        if remaining > SPIN_S:
            sleep(remaining - SPIN_S)
            continue
        stalled = stalled + 1 if t == last else 0
        last = t
        if stalled > SPIN_STALL_READS:  # a virtual clock only moves when slept
            sleep(remaining)


def _events(notes: list[PlayNote]) -> list[tuple[float, int, int, int]]:
    """(beat, order, pitch, velocity) with note-offs (order 0) before note-ons (order 1) on the same beat."""
    ev = []
    for n in notes:
        ev.append((n.start_beat, 1, n.pitch, n.velocity))
        ev.append((n.start_beat + n.length_beats, 0, n.pitch, 0))
    ev.sort(key=lambda e: (e[0], e[1], e[2]))
    return ev


def _lateness(samples_s: list[float]) -> dict:
    if not samples_s:
        return {"mean": 0.0, "p95": 0.0, "max": 0.0}
    ms = np.array(samples_s) * 1000.0
    return {
        "mean": round(float(ms.mean()), 3),
        "p95": round(float(np.percentile(ms, 95)), 3),
        "max": round(float(ms.max()), 3),
    }


def _channel_info(fl, channel: int | None) -> dict:
    if channel is None:
        return {"index": None, "name": "(selected channel)"}
    reply = fl.request("channels", {"start": channel, "count": 1})
    total = reply.get("total")
    if total is not None and channel >= int(total):
        raise ToolError("NOT_FOUND", f"channel {channel} does not exist (FL has {total} channels: 0..{int(total) - 1})")
    name = reply.get(f"n{channel}", reply.get("n0"))
    fl.request("select", {"channel": channel})
    return {"index": channel, "name": name if name is not None else f"Channel {channel}"}


def fit_transport_clock(fl, rate_ticks_per_s: float, polls: int = SYNC_POLLS,
                        spacing_s: float = SYNC_POLL_SPACING_S) -> float:
    """Return `a` in ticks = a + rate·t: per poll, a_i = pos_ticks − rate·(round-trip midpoint); median of a_i."""
    offsets = []
    for i in range(polls):
        t0 = fl.now()
        reply = fl.request("transport", {"cmd": "pos"})
        t1 = fl.now()
        offsets.append(float(reply["pos_ticks"]) - rate_ticks_per_s * (t0 + t1) / 2.0)
        if i < polls - 1:
            _wait_until(fl, t1 + spacing_s)
    return statistics.median(offsets)


def _wait_until(fl, target: float) -> None:
    precise_sleep_until(target, fl.now, getattr(fl, "sleep", time.sleep))


class _Take:
    """Sends events and guarantees every note it started is released."""

    def __init__(self, fl):
        self.fl = fl
        self.sounding: dict[int, int] = {}
        self.lateness: list[float] = []
        self.sent = 0

    def run(self, timed_events: list[tuple[float, int, int, int]]) -> None:
        now = self.fl.now
        for target, order, pitch, vel in timed_events:
            _wait_until(self.fl, target)
            if order == 1:
                self.fl.note(NOTE_ON, pitch, vel)
                self.lateness.append(now() - target)
                self.sent += 1
                self.sounding[pitch] = self.sounding.get(pitch, 0) + 1
            elif self.sounding.get(pitch):
                self.fl.note(NOTE_OFF, pitch, 0)
                self.sounding[pitch] -= 1
                if not self.sounding[pitch]:
                    del self.sounding[pitch]

    def release_all(self) -> None:
        for pitch in list(self.sounding):
            self.fl.note(NOTE_OFF, pitch, 0)
        self.sounding.clear()
        self.fl.note(CONTROL_CHANGE, ALL_NOTES_OFF, 0)


def play(fl, notes: list[PlayNote], channel: int | None = None, record: bool = False, start_bar: int = 2) -> dict:
    if not notes:
        raise ToolError("OUT_OF_RANGE", "no notes to play")
    if record and start_bar < 2:
        raise ToolError("OUT_OF_RANGE", "start_bar must be >= 2 (one bar of pre-roll is needed)")

    chan = _channel_info(fl, channel)
    tr = fl.request("transport", {"cmd": "pos"})
    bpm, ppq = float(tr["bpm"]), int(tr["ppq"])
    if ppq <= 0:
        raise ToolError("FL_ERROR", f"FL reported bpm={bpm}, ppq={ppq}")
    if not MIN_BPM <= bpm <= MAX_BPM:
        raise ToolError(
            "FL_ERROR", f"FL reported an implausible tempo of {bpm:g} BPM (expected {MIN_BPM:g}..{MAX_BPM:g}); not playing",
            hint="restart the SoundPilot backend and re-copy fl_script/MixAssist/device_MixAssist.py into "
                 "FL's Settings/Hardware/MixAssist folder, then reload the script")
    s_per_beat = 60.0 / bpm
    end_beat = max(n.start_beat + n.length_beats for n in notes)
    if end_beat * s_per_beat > MAX_TAKE_S:
        raise ToolError("OUT_OF_RANGE", f"take is {end_beat * s_per_beat:.0f} s at {bpm:g} BPM; the limit is {MAX_TAKE_S:.0f} s")

    warnings = [FOCUS_WARNING]
    short = [n for n in notes if n.length_beats * s_per_beat < MIN_AUDIBLE_NOTE_S]
    if short:
        shortest_ms = min(n.length_beats for n in short) * s_per_beat * 1000.0
        warnings.append(
            f"{len(short)} of {len(notes)} notes are shorter than {MIN_AUDIBLE_NOTE_S * 1000:.0f} ms at {bpm:g} BPM "
            f"(shortest {shortest_ms:.2f} ms); FL may not sound them. Use longer length_beats.")
    loop_mode = tr.get("loop_mode")
    if loop_mode is not None and int(loop_mode) == 0:
        warnings.append(PATTERN_MODE_WARNING)

    events = _events(notes)
    take = _Take(fl)
    if not record:
        t0 = fl.now() + LIVE_LEAD_S
        try:
            take.run([(t0 + beat * s_per_beat, o, p, v) for beat, o, p, v in events])
        finally:
            take.release_all()
        return _result("live", bpm, ppq, take, chan, warnings, end_beat * s_per_beat)

    rate = ppq * bpm / 60.0
    preroll_tick = (start_bar - 2) * 4 * ppq
    first_tick = preroll_tick + 4 * ppq
    started = False
    try:
        transport(fl, "rec_on")
        transport(fl, "pos", pos_ticks=preroll_tick)
        started = True
        transport(fl, "play")
        recording, state = wait_transport(fl, RECORD_CONFIRM_S, playing=True, recording=True)
        if not recording:
            raise ToolError(
                "FL_ERROR",
                f"FL did not start recording within {RECORD_CONFIRM_S:g} s ({describe(state)}); no notes were sent",
                hint=NOT_CONFIRMED_HINT)
        a = fit_transport_clock(fl, rate)
        timed = [((first_tick + beat * ppq - a) / rate, o, p, v) for beat, o, p, v in events]
        if timed[0][0] < fl.now():
            warnings.append("Clock sync finished after the first note was due; the first notes may be late.")
        take.run(timed)
        _wait_until(fl, fl.now() + TAIL_S)
    finally:
        take.release_all()
        cleanup_errors = _stop_recording(fl, started)
    if cleanup_errors:
        warnings.append("Could not stop FL after the take: " + "; ".join(cleanup_errors)
                        + ". Press Stop and turn Record off in FL.")
    else:
        stopped, state = wait_transport(fl, STOP_CONFIRM_S, playing=False, recording=False)
        if not stopped:
            warnings.append(f"FL still reports {describe(state)} after the take; press Stop and turn Record off in FL.")
    out = _result("record", bpm, ppq, take, chan, warnings, end_beat * s_per_beat)
    out["start_bar"] = start_bar
    return out


def _stop_recording(fl, started: bool) -> list[str]:
    """Best-effort stop and rec_off; never raises, so an earlier error is not masked."""
    errors = []
    for cmd in (["stop"] if started else []) + ["rec_off"]:
        try:
            transport(fl, cmd)
        except ToolError as exc:
            errors.append(f"{cmd}: {exc.message}")
    return errors


def _result(mode: str, bpm: float, ppq: int, take: _Take, chan: dict, warnings: list[str], duration_s: float) -> dict:
    return {
        "mode": mode,
        "bpm": bpm,
        "ppq": ppq,
        "notes_sent": take.sent,
        "duration_s": round(duration_s, 3),
        "timing_lateness_ms": _lateness(take.lateness),
        "channel": chan,
        "warnings": warnings,
    }
