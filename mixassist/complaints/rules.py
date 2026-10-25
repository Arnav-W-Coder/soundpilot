"""diagnose_complaint (F5) and verify_fix (F6) rules."""
from __future__ import annotations

import math

from mixassist.analysis import analyze, get_record
from mixassist.analysis.analyze import Record
from mixassist.errors import ToolError

from .lexicon import load_lexicon
from .measures import Measurement, ReferenceBaseline, band_levels, fmt_hz, fmt_range, measure

CLARIFY_RATIO = 1.5
VERIFY_FRACTION = 0.5
MAX_LUFS_CHANGE = 1.0
TRUE_PEAK_CEILING = -1.0
UNCHANGED_DB = 0.05
GENERIC_OPTIONS = ["Too bright or harsh", "Muddy or boxy", "Too much bass (boomy)", "Thin, no body"]
FALLBACK_TRACK_OPTIONS = ["The synths or pads", "The guitars", "The backing vocals"]
MIDI_NOTE = ("That sounds like a MIDI problem (wrong notes or stiff timing), which an audio file can't show. "
             "Export the pattern as MIDI and use analyze_midi / fix_midi.")


def _reference_record(record: Record) -> Record | None:
    ref = record.output.get("reference")
    if not ref:
        return None
    try:
        return get_record(ref["analysis_id"])
    except ToolError:
        return None


def _measure_all(names: list[str], record: Record, reference: Record | None) -> list[Measurement]:
    lex = load_lexicon()
    out = [measure(n, lex.descriptors[n], record, reference) for n in names]
    return sorted((m for m in out if m is not None), key=lambda m: -m.severity)


def _option(m: Measurement) -> str:
    label = load_lexicon().descriptors[m.descriptor]["label"]
    return f"{label[0].upper()}{label[1:]} ({m.summary})"


def _question(options: list[str]) -> str:
    opts = [o[0].lower() + o[1:] for o in options]
    if len(opts) == 2:
        return f"Is it {opts[0]} or {opts[1]}?"
    return f"Which is it: {', '.join(opts[:-1])} or {opts[-1]}?"


def _clarify(question: str, options: list[str], findings: list[Measurement], notes: list[str]) -> dict:
    out = {"needs_clarification": True, "question": question, "options": options[:4],
           "findings": [m.finding() for m in findings], "proposed_changes": []}
    if notes:
        out["notes"] = notes
    return out


def _track_options() -> list[str]:
    try:
        from mixassist.tools.fl_tools import fl_get_mix_overview

        tracks = fl_get_mix_overview({"include_empty": False})["tracks"]
        names = [t["name"] for t in tracks if t.get("index") != 0][:4]
        if len(names) >= 2:
            return names
    except Exception:
        pass
    return list(FALLBACK_TRACK_OPTIONS)


def _volume_cut(true_peak: float) -> float:
    return max(-24.0, -math.ceil((true_peak - TRUE_PEAK_CEILING) * 2) / 2)


def proposed_gain_db(m: Measurement) -> float | None:
    """The signed EQ gain or volume change the rule proposes for a fired measurement."""
    change = load_lexicon().descriptors[m.descriptor]["change"]
    if not m.triggered:
        return None
    if change["kind"] == "eq":
        return round(change["sign"] * min(max(m.excess, change["min_db"]), change["max_db"]), 1)
    if change["kind"] == "volume" and m.unit == "dBTP":
        return _volume_cut(m.value)
    return None


def _proposal(m: Measurement, track) -> tuple[dict | None, list[str]]:
    spec = load_lexicon().descriptors[m.descriptor]
    change = spec["change"]
    target = 0 if track is None else track
    where = "the master" if track is None else f"track {track!r}" if isinstance(track, str) else f"track {track}"
    if change["kind"] == "eq":
        gain = proposed_gain_db(m)
        args = {"track": target, "eq_band": change["eq_band"], "eq_gain_db": gain}
        at = ""
        if "eq_freq_hz" in change:
            args["eq_freq_hz"] = change["eq_freq_hz"]
            at = f" at {fmt_hz(change['eq_freq_hz'])}"
        verb = "cut" if gain < 0 else "boost"
        why = (f"{spec['label'][0].upper()}{spec['label'][1:]}: {m.summary} vs {m.basis}; "
               f"{verb} the {change['eq_band']} EQ band on {where} by {abs(gain):.1f} dB{at}.")
        return {"tool": "fl_set_track", "args": args, "why": why}, []
    if change["kind"] == "volume":
        if m.unit != "dBTP":
            return None, [f"Crest factor is {m.value:.1f} dB (under 6 dB): the mix is heavily limited or "
                          "compressed. Ease off the master limiter; v1 can't change plugins."]
        cut = _volume_cut(m.value)
        why = (f"True peak is {m.value:+.1f} dBTP (above −1 dBTP); turn {where} down {abs(cut):.1f} dB. "
               "If several tracks are hot, trim the loudest ones (fl_watch_peaks) rather than the master.")
        return {"tool": "fl_set_track", "args": {"track": target, "volume_change_db": cut}, "why": why}, []
    if change["kind"] == "width":
        pct = change["stereo_width_pct"]
        why = (f"{m.summary[0].upper()}{m.summary[1:]} ({'above 0.95' if pct > 0 else 'below 0'}); "
               f"set stereo separation on {where} to {pct:+d} %.")
        return {"tool": "fl_set_track", "args": {"track": target, "stereo_width_pct": pct}, "why": why}, []
    return None, [f"The mix is {m.summary}. v1 has no limiter: raise the master limiter's gain or output "
                  "level in FL to match the reference."]


def diagnose(complaint: str, analysis_id: str, track=None) -> dict:
    """diagnose_complaint: match the complaint to lexicon descriptors and measure them.

    Findings are sorted most severe first, so findings[0] is the top finding. needs_clarification is true
    (and nothing is proposed) when nothing in the complaint matches the lexicon, when two or more matched
    descriptors fire with the top excess under 1.5× the second, or when a width change needs a track
    that wasn't given. Only the top finding gets a proposal, since several EQ moves can compete for the
    same band. With no `track`, proposals target the master (track 0).
    """
    record = get_record(analysis_id)
    reference = _reference_record(record)
    lex = load_lexicon()
    matched = lex.match(complaint)
    notes = [MIDI_NOTE] if matched.midi else []

    if not matched.descriptors:
        if matched.midi:
            return {"needs_clarification": False, "findings": [], "proposed_changes": [], "notes": notes}
        fired = [m for m in _measure_all(list(lex.descriptors), record, reference) if m.triggered]
        options = [_option(m) for m in fired[:4]]
        options += [o for o in GENERIC_OPTIONS if len(options) < 4 and o not in options]
        return _clarify("What sounds wrong? Pick the closest option.", options, fired, notes)

    measured = _measure_all(matched.descriptors, record, reference)
    for name in matched.descriptors:
        if name not in {m.descriptor for m in measured}:
            notes.append(f"Can't measure '{name}' from this file"
                         + (" without a reference: call analyze_audio with reference_path." if name == "quiet" else "."))
    fired = [m for m in measured if m.triggered]
    if len(fired) >= 2 and fired[0].severity < CLARIFY_RATIO * fired[1].severity:
        options = [_option(m) for m in fired[:4]]
        return _clarify(_question(options), options, measured, notes)
    if not fired:
        if measured:
            notes.append("Nothing measures outside the normal range for this complaint ("
                         + "; ".join(f"{m.descriptor}: {m.summary} vs {m.basis}" for m in measured) + ").")
        out = {"needs_clarification": False, "findings": [m.finding() for m in measured], "proposed_changes": []}
        if notes:
            out["notes"] = notes
        return out

    top = fired[0]
    change = lex.descriptors[top.descriptor]["change"]
    if change["kind"] == "width" and track is None:
        options = _track_options()
        verb = "widen" if change["stereo_width_pct"] > 0 else "narrow"
        return _clarify(f"Which track should I {verb}? Stereo width is set per track, not on the master.",
                        options, measured, notes)
    proposal, extra = _proposal(top, track)
    notes += extra
    for m in fired[1:]:
        notes.append(f"Also measured: {m.descriptor} ({m.summary}). Fix {top.descriptor} first, then re-check.")
    out = {"needs_clarification": False, "findings": [m.finding() for m in measured],
           "proposed_changes": [proposal] if proposal else []}
    if notes:
        out["notes"] = notes
    return out


# --- verify_fix --------------------------------------------------------------------------------

def _unchanged(before: Record, after: Record) -> bool:
    a, b = after.output, before.output
    keys = ("integrated_lufs", "true_peak_dbtp", "sample_peak_dbfs")
    if any((a[k] is None) != (b[k] is None) or (a[k] is not None and abs(a[k] - b[k]) > UNCHANGED_DB) for k in keys):
        return False
    for name, val in a["bands_db"].items():
        other = b["bands_db"][name]
        if (val is None) != (other is None) or (val is not None and abs(val - other) > UNCHANGED_DB):
            return False
    return True


def _check(descriptor: str, before: float, after: float, rule: str, passed: bool) -> dict:
    return {"descriptor": descriptor, "before": round(before, 2), "after": round(after, 2), "rule": rule,
            "passed": bool(passed)}


def _descriptor_check(name: str, before: Record, after: Record, reference: Record | None) -> dict | None:
    spec = load_lexicon().descriptors[name]
    m = spec["measure"]
    kind = m["kind"]
    bm = measure(name, spec, before, reference)
    if kind == "band":
        value, target = band_levels(ReferenceBaseline(after.spectrum, before.spectrum), m["bands"])
        moved = value - target
        before_level = target - ReferenceBaseline(after.spectrum, before.spectrum).offset
        proposed = proposed_gain_db(bm) if bm else None
        need = VERIFY_FRACTION * (abs(proposed) if proposed else m["threshold_db"])
        down = m["direction"] == "excess"
        gained = -moved if down else moved
        basis = (f"{VERIFY_FRACTION:.0%} of the proposed {proposed:+.1f} dB change" if proposed
                 else f"{VERIFY_FRACTION:.0%} of the {m['threshold_db']:g} dB threshold")
        rule = (f"{fmt_range(m['bands'])} level, loudness-matched to before, must {'drop' if down else 'rise'} "
                f"≥ {need:.2f} dB ({basis}); moved {moved:+.2f} dB")
        return _check(name, before_level, before_level + moved, rule, gained >= need)
    b, a = before.output, after.output
    if kind == "loud":
        tp_b, tp_a = b["true_peak_dbtp"], a["true_peak_dbtp"]
        if bm and bm.triggered and bm.unit == "dBTP":
            need = VERIFY_FRACTION * abs(_volume_cut(tp_b))
            return _check(name, tp_b, tp_a, f"true peak must drop ≥ {need:.2f} dB (50% of the proposed cut)",
                          tp_b - tp_a >= need)
        if bm and bm.triggered:
            need = VERIFY_FRACTION * bm.excess
            return _check(name, b["crest_db"], a["crest_db"], f"crest factor must rise ≥ {need:.2f} dB",
                          a["crest_db"] - b["crest_db"] >= need)
        return _check(name, tp_b, tp_a, "true peak must stay ≤ −1 dBTP with crest ≥ 6 dB",
                      tp_a <= m["max_true_peak_dbtp"] and (a["crest_db"] or 0) >= m["min_crest_db"])
    if kind == "quiet":
        ref_b, ref_a = b.get("reference"), a.get("reference")
        if ref_b and ref_a and ref_b.get("lufs_diff") is not None and ref_a.get("lufs_diff") is not None:
            need = VERIFY_FRACTION * max(-ref_b["lufs_diff"], -m["max_lufs_diff"])
            return _check(name, ref_b["lufs_diff"], ref_a["lufs_diff"],
                          f"loudness vs the reference must rise ≥ {need:.2f} LU", ref_a["lufs_diff"] - ref_b["lufs_diff"] >= need)
        return _check(name, b["integrated_lufs"], a["integrated_lufs"], "integrated loudness must rise ≥ 1.00 LU",
                      a["integrated_lufs"] - b["integrated_lufs"] >= 1.0)
    if kind == "corr_mean":
        cb = b["correlation_mean"] if b["correlation_mean"] is not None else 1.0
        ca = a["correlation_mean"] if a["correlation_mean"] is not None else 1.0
        need = VERIFY_FRACTION * max(cb - m["above"], 0.05)
        return _check(name, cb, ca, f"correlation_mean must drop ≥ {need:.3f}", cb - ca >= need)
    if kind == "corr_p5":
        cb, ca = b["correlation_p5"], a["correlation_p5"]
        if cb is None or ca is None:
            return None
        need = VERIFY_FRACTION * max(m["below"] - cb, 0.1)
        return _check(name, cb, ca, f"correlation_p5 must rise ≥ {need:.3f}", ca - cb >= need)
    raise ValueError(kind)


def verify(complaint: str, before_analysis_id: str, after_path: str) -> dict:
    """verify_fix: re-analyze the after file and check the complaint's descriptors moved.

    Checks: each matched descriptor moved the right way by ≥ 50 % of the change diagnose_complaint
    proposes for the before file (spectral checks align after to before on the median third-octave
    difference, i.e. they're loudness-matched); |ΔLUFS| ≤ 1 LU unless the complaint is loud or quiet;
    true peak doesn't rise above −1 dBTP.

    Failure modes: when checks fail this returns the normal output with passed=false (e.g. the loudness
    check for a +3 LU re-export). It raises ToolError VERIFY_FAILED only when the after file measures the
    same as before (every band, LUFS and peak within 0.05 dB), i.e. nothing changed and there's nothing
    to check; that is almost always a missing re-export. Unknown complaints raise NEEDS_CLARIFICATION;
    MIDI complaints raise UNSUPPORTED.
    """
    before = get_record(before_analysis_id)
    lex = load_lexicon()
    matched = lex.match(complaint)
    if not matched.descriptors:
        if matched.midi:
            raise ToolError("UNSUPPORTED", MIDI_NOTE)
        raise ToolError("NEEDS_CLARIFICATION", f"can't tell which problem {complaint!r} refers to",
                        candidates=list(lex.descriptors),
                        hint="Repeat the complaint that was fixed, e.g. 'too bright' or 'muddy'")
    after_out = analyze(after_path, before.reference_path)
    after = get_record(after_out["analysis_id"])
    if _unchanged(before, after):
        raise ToolError("VERIFY_FAILED",
                        "the after file measures the same as before (no band, loudness or peak change), "
                        "so the fix can't be verified; passed=false",
                        hint="Apply the change in FL, re-export the mix to a new file, then verify that file")
    reference = _reference_record(before)
    checks = [c for c in (_descriptor_check(n, before, after, reference) for n in matched.descriptors) if c]
    b, a = before.output, after.output
    if not {"loud", "quiet"} & set(matched.descriptors) and None not in (a["integrated_lufs"], b["integrated_lufs"]):
        d = a["integrated_lufs"] - b["integrated_lufs"]
        checks.append(_check("integrated_lufs", b["integrated_lufs"], a["integrated_lufs"],
                             f"|ΔLUFS| ≤ {MAX_LUFS_CHANGE:.1f} LU; measured {d:+.2f} LU", abs(d) <= MAX_LUFS_CHANGE))
    tp_b, tp_a = b["true_peak_dbtp"], a["true_peak_dbtp"]
    checks.append(_check("true_peak_dbtp", tp_b, tp_a, "true peak must not rise above −1 dBTP",
                         tp_a <= TRUE_PEAK_CEILING or tp_a <= tp_b))
    return {"passed": all(c["passed"] for c in checks), "after_analysis_id": after.analysis_id, "checks": checks}
