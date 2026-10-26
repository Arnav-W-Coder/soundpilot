"""F1 mix overview, F2 set/undo with readback, F3 peak watch (spec §6)."""
from __future__ import annotations

import math
import uuid

from mixassist.errors import ToolError
from mixassist.fl import curves, get_fl
from mixassist.fl.client import FLClient
from mixassist.fl.fake import is_default_name
from mixassist.fl.playback import describe, transport, wait_transport
from mixassist.fl.tracks import resolve_track

TRACK_BATCH = 8
PEAK_BATCH = 32
MIN_POLL_S = 0.05
PLAY_CONFIRM_S = 1.0
MAX_ROUND_TRIPS = 8
VOLUME_AIM_DB = 0.1
VOLUME_TOL_DB = 0.25
PAN_TOL_PCT = 1.0
WIDTH_TOL_PCT = 1.0
EQ_GAIN_TOL_DB = 0.25
EQ_FREQ_TOL_REL = 0.02
UNDO_TOL_DB = 0.1
BAND_INDEX = {name: i for i, name in enumerate(curves.EQ_BANDS)}


# ------------------------------------------------------------------ reading
def _parse_tracks(reply: dict, start: int, count: int, limit: int) -> list[dict]:
    out = []
    for i in range(start, min(start + count, limit)):
        if f"n{i}" not in reply:
            continue
        out.append(
            {
                "index": i,
                "name": reply[f"n{i}"],
                "v": float(reply.get(f"v{i}") or 0.0),
                "volume_db": float(reply[f"vd{i}"]) if reply.get(f"vd{i}") is not None else None,
                "pan": float(reply.get(f"p{i}") or 0.0),
                "sep": float(reply.get(f"s{i}") or 0.0),
                "muted": bool(reply.get(f"m{i}")),
                "solo": bool(reply.get(f"so{i}")),
            }
        )
    return out


def read_tracks(fl: FLClient) -> list[dict]:
    """All mixer tracks except the trailing "current" track, as raw (normalized) state."""
    first = fl.request("tracks", {"start": 0, "count": TRACK_BATCH})
    total = int(first.get("total") or 0)
    limit = max(1, total - 1)  # v1 excludes the last index ("current")
    tracks = _parse_tracks(first, 0, TRACK_BATCH, limit)
    for start in range(TRACK_BATCH, limit, TRACK_BATCH):
        reply = fl.request("tracks", {"start": start, "count": TRACK_BATCH})
        tracks += _parse_tracks(reply, start, TRACK_BATCH, limit)
    return tracks


def read_track(fl: FLClient, index: int) -> dict:
    reply = fl.request("tracks", {"start": index, "count": 1})
    parsed = _parse_tracks(reply, index, 1, index + 1)
    if not parsed:
        raise ToolError("NOT_FOUND", f"no mixer track with index {index}")
    return parsed[0]


def read_plugins(fl: FLClient, index: int) -> list[dict]:
    reply = fl.request("fx", {"track": index})
    plugins = []
    for slot in range(10):
        if reply.get(f"s{slot}") and reply.get(f"n{slot}") is not None:
            plugins.append({"slot": slot, "name": reply[f"n{slot}"]})
    return plugins


def read_eq(fl: FLClient, index: int) -> list[dict]:
    reply = fl.request("eq_get", {"track": index})
    bands = []
    for b, name in enumerate(curves.EQ_BANDS):
        if f"g{b}" not in reply:
            continue
        gain_db = reply.get(f"gd{b}")
        freq_hz = reply.get(f"fh{b}")
        bands.append(
            {
                "band": name,
                "gain_db": round(float(gain_db if gain_db is not None else curves.eq_gain_norm_to_db(reply[f"g{b}"])), 2),
                "freq_hz": round(float(freq_hz if freq_hz is not None else curves.eq_freq_norm_to_hz(reply[f"f{b}"])), 1),
                "g": float(reply[f"g{b}"]),
                "f": float(reply[f"f{b}"]),
            }
        )
    return bands


def _volume_db(track: dict) -> float:
    return float(track["volume_db"]) if track.get("volume_db") is not None else curves.norm_to_db(track["v"])


def _looks_empty(track: dict) -> bool:
    return (
        track["index"] != 0
        and is_default_name(track["name"], track["index"])
        and abs(track["v"] - curves.DEFAULT_VOLUME_NORM) < 1e-3
        and abs(track["pan"]) < 1e-3
        and not track["muted"]
        and not track["solo"]
    )


def _api_level(fl: FLClient) -> int:
    return int(fl.request("hello").get("api") or 0)


def _public_track(track: dict, eq: list[dict], plugins: list[dict]) -> dict:
    return {
        "index": track["index"],
        "name": track["name"],
        "volume_db": round(_volume_db(track), 2),
        "pan_pct": round(track["pan"] * 100.0, 1),
        "stereo_width_pct": round(track["sep"] * 100.0, 1),
        "muted": track["muted"],
        "solo": track["solo"],
        "eq": [{"band": b["band"], "gain_db": b["gain_db"], "freq_hz": b["freq_hz"]} for b in eq],
        "plugins": plugins,
    }


# --------------------------------------------------------------------- F1
def fl_get_mix_overview(args: dict) -> dict:
    fl = get_fl()
    api = _api_level(fl)
    transport = fl.request("transport", {"cmd": "pos"})
    tracks = read_tracks(fl)
    out = []
    for track in tracks:
        plugins = read_plugins(fl, track["index"])
        if not args.get("include_empty") and _looks_empty(track) and not plugins:
            continue
        eq = read_eq(fl, track["index"]) if api >= 35 else []
        out.append(_public_track(track, eq, plugins))
    return {"fl_api": api, "bpm": float(transport.get("bpm") or 0.0), "tracks": out}


# --------------------------------------------------------------------- F2
def _state(track: dict, eq_band: dict | None) -> dict:
    state = {
        "volume_db": round(_volume_db(track), 2),
        "pan_pct": round(track["pan"] * 100.0, 1),
        "stereo_width_pct": round(track["sep"] * 100.0, 1),
    }
    if eq_band is not None:
        state.update({"eq_band": eq_band["band"], "eq_gain_db": eq_band["gain_db"], "eq_freq_hz": eq_band["freq_hz"]})
    return state


def _search_volume(fl: FLClient, index: int, target_db: float, first_norm: float, track: dict) -> tuple[dict, int]:
    """Readback search on getTrackVolume(i,1): secant inside a shrinking bracket, ≤ MAX_ROUND_TRIPS sets."""
    lo, hi = 0.0, 1.0
    points: list[tuple[float, float]] = []
    norm = first_norm
    rounds = 1
    while True:
        db = _volume_db(track)
        points.append((norm, db))
        if abs(db - target_db) <= VOLUME_AIM_DB or rounds >= MAX_ROUND_TRIPS:
            return track, rounds
        if db < target_db:
            lo = max(lo, norm)
        else:
            hi = min(hi, norm)
        guess = None
        if len(points) >= 2:
            (n0, d0), (n1, d1) = points[-2], points[-1]
            if d1 != d0:
                guess = n1 + (target_db - d1) * (n1 - n0) / (d1 - d0)
        if guess is None:
            guess = curves.db_to_norm(curves.norm_to_db(norm) + (target_db - db))
        if not (lo < guess < hi):
            guess = (lo + hi) / 2
        norm = guess
        fl.request("set", {"track": index, "v": norm, "pk": 0})
        fl.sync()
        track = read_track(fl, index)
        rounds += 1


def _search_eq_gain(fl: FLClient, index: int, band: int, target_db: float, first_norm: float, current: dict) -> dict:
    lo, hi = 0.0, 1.0
    norm = first_norm
    points: list[tuple[float, float]] = []
    for rounds in range(1, MAX_ROUND_TRIPS + 1):
        db = current["gain_db"]
        points.append((norm, db))
        if abs(db - target_db) <= 0.1 or rounds == MAX_ROUND_TRIPS:
            break
        if db < target_db:
            lo = max(lo, norm)
        else:
            hi = min(hi, norm)
        (n0, d0), (n1, d1) = (points[-2], points[-1]) if len(points) >= 2 else ((None, None), points[-1])
        guess = n1 + (target_db - d1) * (n1 - n0) / (d1 - d0) if n0 is not None and d1 != d0 else None
        if guess is None:
            guess = curves.eq_gain_db_to_norm(curves.eq_gain_norm_to_db(norm) + (target_db - db))
        if not (lo < guess < hi):
            guess = (lo + hi) / 2
        norm = guess
        fl.request("eq_set", {"track": index, "band": band, "g": norm, "pk": 0})
        fl.sync()
        current = read_eq(fl, index)[band]
    return current


def fl_set_track(args: dict) -> dict:
    fl = get_fl()
    has_vol = "volume_db" in args
    has_change = "volume_change_db" in args
    if has_vol and has_change:
        raise ToolError("OUT_OF_RANGE", "give volume_db or volume_change_db, not both")
    eq_band_name = args.get("eq_band")
    has_eq_values = "eq_gain_db" in args or "eq_freq_hz" in args
    if has_eq_values and not eq_band_name:
        raise ToolError("OUT_OF_RANGE", "eq_gain_db / eq_freq_hz need eq_band ('low', 'mid' or 'high')")
    if eq_band_name and not has_eq_values:
        raise ToolError("OUT_OF_RANGE", "eq_band needs eq_gain_db and/or eq_freq_hz")
    if not (has_vol or has_change or "pan_pct" in args or "stereo_width_pct" in args or eq_band_name):
        raise ToolError(
            "OUT_OF_RANGE",
            "nothing to change",
            hint="give volume_change_db, volume_db, pan_pct, stereo_width_pct or eq_band with eq_gain_db/eq_freq_hz",
        )

    api = _api_level(fl) if eq_band_name else None
    if eq_band_name and api is not None and api < 35:
        raise ToolError("UNSUPPORTED", f"FL API {api} has no built-in EQ access (needs 35+)")

    tracks = read_tracks(fl)
    target = resolve_track(args["track"], tracks)
    index = target["index"]
    track = read_track(fl, index)
    band = BAND_INDEX[eq_band_name] if eq_band_name else None
    eq_before = read_eq(fl, index)[band] if band is not None else None
    before = _state(track, eq_before)

    vol_min, vol_max = curves.table_range(curves.VOLUME_TABLE)
    target_db = None
    if has_vol:
        target_db = float(args["volume_db"])
    elif has_change:
        target_db = _volume_db(track) + float(args["volume_change_db"])
    if target_db is not None and not (max(vol_min, -60.0) <= target_db <= vol_max):
        raise ToolError(
            "OUT_OF_RANGE",
            f"{track['name']} would end at {target_db:.1f} dB; the fader range is -60..{vol_max:.1f} dB",
            hint="PROBE P3: fader range from the placeholder curve table",
        )

    set_args: dict = {"track": index, "pk": 0}
    vol_norm = None
    if target_db is not None:
        vol_norm = curves.db_to_norm(target_db)
        set_args["v"] = vol_norm
    if "pan_pct" in args:
        set_args["p"] = float(args["pan_pct"]) / 100.0
    if "stereo_width_pct" in args:
        set_args["s"] = float(args["stereo_width_pct"]) / 100.0

    undo_entry = {"v": track["v"], "p": track["pan"], "s": track["sep"]}
    if len(set_args) > 2:
        fl.request("set", set_args)
    eq_norm = None
    if band is not None:
        eq_args: dict = {"track": index, "band": band, "pk": 0}
        if "eq_gain_db" in args:
            eq_norm = curves.eq_gain_db_to_norm(float(args["eq_gain_db"]))
            eq_args["g"] = eq_norm
        if "eq_freq_hz" in args:
            eq_args["f"] = curves.eq_freq_hz_to_norm(float(args["eq_freq_hz"]))
        undo_entry.update({"band": band, "g": eq_before["g"], "f": eq_before["f"]})
        fl.request("eq_set", eq_args)
    fl.sync()

    track = read_track(fl, index)
    round_trips = 1
    if target_db is not None:
        track, round_trips = _search_volume(fl, index, target_db, vol_norm, track)
    eq_after = None
    if band is not None:
        eq_after = read_eq(fl, index)[band]
        if eq_norm is not None:
            eq_after = _search_eq_gain(fl, index, band, float(args["eq_gain_db"]), eq_norm, eq_after)
    after = _state(track, eq_after)

    checks = []
    if target_db is not None:
        checks.append(abs(after["volume_db"] - target_db) <= VOLUME_TOL_DB)
    if "pan_pct" in args:
        checks.append(abs(after["pan_pct"] - float(args["pan_pct"])) <= PAN_TOL_PCT)
    if "stereo_width_pct" in args:
        checks.append(abs(after["stereo_width_pct"] - float(args["stereo_width_pct"])) <= WIDTH_TOL_PCT)
    if "eq_gain_db" in args:
        checks.append(abs(after["eq_gain_db"] - float(args["eq_gain_db"])) <= EQ_GAIN_TOL_DB)
    if "eq_freq_hz" in args:
        want = float(args["eq_freq_hz"])
        checks.append(abs(after["eq_freq_hz"] - want) <= max(1.0, EQ_FREQ_TOL_REL * want))

    change_id = "c_" + uuid.uuid4().hex[:8]
    fl.undo_history.append(
        {"change_id": change_id, "index": index, "name": track["name"], "restore": undo_entry, "before": before, "undone": False}
    )
    return {
        "change_id": change_id,
        "track": {"index": index, "name": track["name"]},
        "before": before,
        "after": after,
        "verified": all(checks),
        "round_trips": round_trips,
    }


def fl_undo(args: dict) -> dict:
    fl = get_fl()
    history = fl.undo_history
    if args.get("change_id"):
        matches = [h for h in history if h["change_id"] == args["change_id"]]
        if not matches:
            raise ToolError("NOT_FOUND", f"no change {args['change_id']!r} in this session's undo history",
                            candidates=[h["change_id"] for h in history if not h["undone"]][-10:] or None)
        entry = matches[0]
        if entry["undone"]:
            raise ToolError("NOT_FOUND", f"change {entry['change_id']} was already undone")
    else:
        pending = [h for h in history if not h["undone"]]
        if not pending:
            raise ToolError("NOT_FOUND", "nothing to undo", hint="undo history is in memory and lost on restart")
        entry = pending[-1]

    index, restore = entry["index"], entry["restore"]
    fl.request("set", {"track": index, "v": restore["v"], "p": restore["p"], "s": restore["s"], "pk": 0})
    if "band" in restore:
        fl.request("eq_set", {"track": index, "band": restore["band"], "g": restore["g"], "f": restore["f"], "pk": 0})
    fl.sync()
    track = read_track(fl, index)
    eq_band = read_eq(fl, index)[restore["band"]] if "band" in restore else None
    restored = _state(track, eq_band)
    before = entry["before"]
    verified = (
        abs(restored["volume_db"] - before["volume_db"]) <= UNDO_TOL_DB
        and abs(restored["pan_pct"] - before["pan_pct"]) <= PAN_TOL_PCT
        and abs(restored["stereo_width_pct"] - before["stereo_width_pct"]) <= WIDTH_TOL_PCT
        and (eq_band is None or abs(restored["eq_gain_db"] - before["eq_gain_db"]) <= EQ_GAIN_TOL_DB)
    )
    entry["undone"] = True
    return {
        "change_id": entry["change_id"],
        "track": {"index": index, "name": track["name"]},
        "restored": restored,
        "verified": verified,
    }


# --------------------------------------------------------------------- F3
def _round_half(x: float) -> float:
    return math.floor(x * 2 + 0.5) / 2


def _dbfs(linear: float) -> float | None:
    return 20.0 * math.log10(linear) if linear > 0 else None


def fl_watch_peaks(args: dict) -> dict:
    fl = get_fl()
    seconds = float(args.get("seconds", 30))
    start_playback = bool(args.get("start_playback", True))
    from_bar = int(args.get("from_bar", 1))

    tracks = read_tracks(fl)
    n = len(tracks)
    status = fl.request("transport", {"cmd": "pos"})
    ppq = int(status.get("ppq") or 96)
    warnings = []
    if start_playback:
        transport(fl, "play", pos_ticks=(from_bar - 1) * 4 * ppq)
        playing, state = wait_transport(fl, PLAY_CONFIRM_S, playing=True)
        if not playing:
            warnings.append(f"FL did not start playing within {PLAY_CONFIRM_S:g} s ({describe(state)}), so peaks "
                            "may read 0; press Play in FL or keep its window focused with no dialog open")
    elif not status.get("playing"):
        warnings.append("FL is not playing, so every peak reads 0; start playback or set start_playback=true")

    max_peak = [0.0] * n
    clips = [0] * n
    polls = 0
    t_first = fl.now()
    poll_starts = []
    try:
        while True:
            t_poll = fl.now()
            if t_poll - t_first >= seconds:
                break
            poll_starts.append(t_poll)
            for start in range(0, n, PEAK_BATCH):
                reply = fl.request("peaks", {"start": start, "count": min(PEAK_BATCH, n - start)})
                for i in range(start, min(start + PEAK_BATCH, n)):
                    p = float(reply.get(f"p{i}") or 0.0)
                    if p > max_peak[i]:
                        max_peak[i] = p
                    if p > 1.0:
                        clips[i] += 1
            polls += 1
            wait = t_poll + MIN_POLL_S - fl.now()
            if wait > 0:
                fl.sleep(wait)
    finally:
        if start_playback:
            transport(fl, "stop")

    if len(poll_starts) > 1:
        interval_ms = (poll_starts[-1] - poll_starts[0]) / (len(poll_starts) - 1) * 1000.0
    else:
        interval_ms = MIN_POLL_S * 1000.0

    report = []
    for t in tracks:
        i = t["index"]
        if max_peak[i] <= 0 and is_default_name(t["name"], i):
            continue
        db = _dbfs(max_peak[i])
        report.append(
            {"index": i, "name": t["name"], "max_peak_dbfs": round(db, 2) if db is not None else None, "clip_count": clips[i]}
        )
    clipping = [r["index"] for r in report if r["clip_count"] > 0]

    suggestions = []
    suggested = set()
    for r in report:
        db = r["max_peak_dbfs"]
        if r["index"] == 0 or db is None:
            continue
        if r["clip_count"] > 0 or db > -1.0:
            change = max(-24.0, _round_half(-6.0 - db))
            what = f"clipped {r['clip_count']}x" if r["clip_count"] else "runs hot"
            suggestions.append(
                {"track": r["index"], "volume_change_db": change,
                 "reason": f"{r['name']} {what} (max {db:+.2f} dBFS); trim to peak near -6 dBFS"}
            )
            suggested.add(r["index"])
    master = next((r for r in report if r["index"] == 0), None)
    if master and master["max_peak_dbfs"] is not None and master["max_peak_dbfs"] > -1.0:
        over = master["max_peak_dbfs"] + 1.0
        trim = -max(0.5, _round_half(over + 0.25))
        contributors = sorted(
            (r for r in report if r["index"] != 0 and r["index"] not in suggested and r["max_peak_dbfs"] is not None),
            key=lambda r: r["max_peak_dbfs"],
            reverse=True,
        )[:2]
        for r in contributors:
            suggestions.append(
                {"track": r["index"], "volume_change_db": max(-24.0, trim),
                 "reason": f"master peaks at {master['max_peak_dbfs']:+.2f} dBFS; trim the loudest contributor "
                           f"{r['name']} ({r['max_peak_dbfs']:+.2f} dBFS) instead of the master"}
            )

    return {
        "polls": polls,
        "poll_interval_ms": round(interval_ms, 1),
        "tracks": report,
        "clipping": clipping,
        "suggestions": suggestions,
        "warnings": warnings,
        "note": (
            f"Sample peaks polled every ~{interval_ms:.0f} ms from FL's mixer meters: transients shorter than the "
            "poll interval can be missed. Not true peak, not LUFS."
        ),
    }
