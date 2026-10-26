"""Music-theory and mixing-judgement suite: 10 prompts, temperature 0, FakeFL + fixtures.

Each prompt runs one agent turn with a fresh session and a fresh FakeFL, like evals/gemma_suite.py. The checks are
deterministic: pitch classes, repetitions and timing of the fl_play_midi notes Gemma computes, and for mix prompts
that no proposal widens or pans the low end, moves more than a few dB, or skips the measure-first path.

    .venv/bin/python -m evals.theory_suite --llm vertex
    .venv/bin/python -m evals.theory_suite --llm ollama --model gemma4:31b-cloud --tag before
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from evals.gemma_suite import AUDIO, ROOT, RESULTS, _fresh_fl, near, track_is
from mixassist.agent import Agent
from mixassist.schemas import TOOL_NAMES
from mixassist.tools import coerce_args, is_write

NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
LOW_END = ("Kick", "Bass", "Sub")
MU = AUDIO / "muddy.wav"


def pcs(*names: str) -> list[int]:
    return [NAMES.index(n) for n in names]


def notes_of(args: dict) -> list[tuple[int, float, float, int]]:
    """(pitch, start_beat, length_beats, velocity) in the order the model sent them."""
    return [(int(n["pitch"]), float(n["start_beat"]), float(n["length_beats"]), int(n.get("velocity", 100)))
            for n in args.get("notes") or []]


def _name(p: int) -> str:
    return f"{NAMES[p % 12]}{p // 12 - 1}"


def show(notes) -> str:
    return " ".join(f"{_name(p)}@{s:g}" for p, s, _, _ in notes[:24]) + (" ..." if len(notes) > 24 else "")


# ------------------------------------------------------------------ note checks: return a problem or None
def check_pitch_classes(notes, allowed: set[int], required: set[int] | None = None) -> str | None:
    used = {p % 12 for p, *_ in notes}
    extra = used - allowed
    if extra:
        return "out-of-set pitch classes " + ",".join(NAMES[x] for x in sorted(extra))
    missing = (required or set()) - used
    if missing:
        return "missing " + ",".join(NAMES[x] for x in sorted(missing))
    return None


def check_strictly_increasing_starts(notes) -> str | None:
    starts = [s for _, s, _, _ in notes]
    if any(b <= a for a, b in zip(starts, starts[1:])):
        return "start_beats not strictly increasing"
    return None


def repetitions(pitches: list[int], times: int) -> bool:
    """The pitch sequence is one figure (>= 3 notes) played `times` times back to back, optionally plus a closing
    note on the figure's first pitch class."""
    for extra in (0, 1):
        n = len(pitches) - extra
        if n <= 0 or n % times:
            continue
        size = n // times
        if size < 3 or pitches[:n] != pitches[:size] * times:
            continue
        if extra and pitches[-1] % 12 != pitches[0] % 12:
            continue
        return True
    return False


def check_scale(notes, degrees: list[int]) -> str | None:
    pitches = [p for p, *_ in notes]
    if len(pitches) != len(degrees):
        return f"{len(pitches)} notes, expected {len(degrees)}"
    if [p % 12 for p in pitches] != degrees:
        return "wrong notes " + " ".join(_name(p) for p in pitches)
    if any(b <= a for a, b in zip(pitches, pitches[1:])):
        return "not ascending"
    return check_strictly_increasing_starts(notes)


def onset_groups(notes, tol: float = 0.02) -> list[set[int]]:
    groups: list[tuple[float, set[int]]] = []
    for p, s, _, _ in sorted(notes, key=lambda n: (n[1], n[0])):
        if groups and abs(s - groups[-1][0]) <= tol:
            groups[-1][1].add(p % 12)
        else:
            groups.append((s, {p % 12}))
    return [g for _, g in groups]


def check_progression(notes, chords: list[tuple[set[int], set[int]]]) -> str | None:
    """Notes split, in time order, into len(chords) consecutive spans; span i uses only chords[i][1] (allowed pitch
    classes) and contains all of chords[i][0] (the triad). Works for block chords and for arpeggiated ones."""
    groups = onset_groups(notes)

    def fits(i: int, k: int) -> bool:
        if k == len(chords):
            return i == len(groups)
        need, allowed = chords[k]
        used: set[int] = set()
        for j in range(i, len(groups)):
            used |= groups[j]
            if not used <= allowed:
                return False
            if need <= used and fits(j + 1, k + 1):
                return True
        return False

    return None if fits(0, 0) else "chord spans don't match " + " | ".join(
        ",".join(NAMES[x] for x in sorted(g)) for g in groups[:12])


# ------------------------------------------------------------------ mix checks
def mix_problems(writes: list[dict], max_db: float) -> list[str]:
    out = []
    for w in writes:
        a = w["args"]
        if w["tool"] != "fl_set_track":
            out.append(f"unexpected write {w['tool']}")
            continue
        low = any(track_is(a, t) for t in LOW_END)
        if low and float(a.get("stereo_width_pct", 0) or 0) > 0:
            out.append(f"widened low-end track {a.get('track')}")
        if low and float(a.get("pan_pct", 0) or 0) != 0:
            out.append(f"panned low-end track {a.get('track')}")
        for key in ("volume_change_db", "eq_gain_db"):
            if key in a and abs(float(a[key])) > max_db:
                out.append(f"{key} {a[key]} on {a.get('track')} is more than {max_db:g} dB")
        if "volume_db" in a:
            out.append(f"absolute volume_db {a['volume_db']} on {a.get('track')} for a relative request")
    return out


# ------------------------------------------------------------------ cases
@dataclass
class Case:
    id: int
    prompt: str
    check: Callable[[list[dict], object], tuple[bool, str]]
    note: str = ""


def _play_check(note_check: Callable[[list], str | None], record: bool = False, start_bar: int | None = None):
    def check(calls, turn) -> tuple[bool, str]:
        plays = [c for c in calls if c["tool"] == "fl_play_midi"]
        if not plays:
            return False, f"expected fl_play_midi, got {[c['tool'] for c in calls] or 'no tool call'}"
        args = plays[-1]["args"]
        if not args.get("notes"):
            return False, "fl_play_midi without notes: " + json.dumps(args, default=str)[:120]
        if bool(is_write("fl_play_midi", args)) != record:
            return False, f"record={args.get('record')!r}, expected {record}"
        if start_bar is not None and not near(args.get("start_bar"), start_bar):
            return False, f"start_bar={args.get('start_bar')!r}, expected {start_bar}"
        try:
            notes = notes_of(args)
        except (KeyError, TypeError, ValueError) as exc:
            return False, f"bad note: {exc}"
        first = min(s for _, s, _, _ in notes)
        problem = f"first note at start_beat {first:g}, not 0" if abs(first) > 1e-6 else note_check(notes)
        return problem is None, (problem + ": " if problem else "") + show(notes)
    return check


def _arpeggio_3x(notes):
    return (check_pitch_classes(notes, set(pcs("E", "G#", "B")), set(pcs("E", "G#", "B")))
            or check_strictly_increasing_starts(notes)
            or (None if repetitions([p for p, *_ in notes], 3) else "not 3 back-to-back repetitions"))


def _a_minor_melody(notes):
    scale = set(pcs("A", "B", "C", "D", "E", "F", "G"))
    if len(notes) < 6:
        return f"only {len(notes)} notes"
    start = min(s for _, s, _, _ in notes)
    end = max(s + length for _, s, length, _ in notes)
    if end - start > 16 + 1e-6:
        return f"spans {end - start:g} beats (> 16)"
    if len({v for *_, v in notes}) < 2:
        return "all velocities identical"
    return check_pitch_classes(notes, scale)


def _ii_v_i(notes):
    return check_progression(notes, [
        (set(pcs("D", "F", "A")), set(pcs("D", "F", "A", "C"))),
        (set(pcs("G", "B", "D")), set(pcs("G", "B", "D", "F"))),
        (set(pcs("C", "E", "G")), set(pcs("C", "E", "G", "B"))),
    ])


def _g_first_inversion(notes):
    problem = check_pitch_classes(notes, set(pcs("G", "B", "D")), set(pcs("G", "B", "D")))
    if problem:
        return problem
    return None if min(p for p, *_ in notes) % 12 == NAMES.index("B") else "lowest note is not B"


def _c_arpeggio(notes):
    return check_pitch_classes(notes, set(pcs("C", "E", "G")), set(pcs("C", "E", "G"))) or \
        check_strictly_increasing_starts(notes)


def _mix_check(max_db: float, need_diagnose: bool = False):
    def check(calls, turn) -> tuple[bool, str]:
        writes = [c for c in calls if c["tool"] in TOOL_NAMES and is_write(c["tool"], c["args"])]
        problems = mix_problems(writes, max_db)
        if need_diagnose:
            analyzed = any(c["tool"] == "analyze_audio" and Path(str(c["args"].get("path"))).name == MU.name
                           for c in calls)
            diagnosed = any(c["tool"] == "diagnose_complaint" and c["args"].get("analysis_id") for c in calls)
            if not (analyzed and diagnosed):
                problems.append("skipped analyze_audio -> diagnose_complaint")
        if turn.writes_after_clarification:
            problems.append("wrote after a clarification")
        summary = "; ".join(p["preview"]["summary"] for p in turn.proposals) or "no proposals"
        return not problems, ("; ".join(problems) + " | " if problems else "") + summary
    return check


CASES = [
    Case(1, "Play an E major arpeggio 3 times", _play_check(_arpeggio_3x)),
    Case(2, "Play a C major scale", _play_check(lambda n: check_scale(n, pcs("C", "D", "E", "F", "G", "A", "B", "C")))),
    Case(3, "Play a D dorian scale", _play_check(lambda n: check_scale(n, pcs("D", "E", "F", "G", "A", "B", "C", "D")))),
    Case(4, "Write a 4-bar melody in A minor and play it", _play_check(_a_minor_melody)),
    Case(5, "Play a ii-V-I in C", _play_check(_ii_v_i)),
    Case(6, "Play a G major chord in first inversion", _play_check(_g_first_inversion)),
    Case(7, "Record a C major arpeggio into the piano roll starting at bar 3",
         _play_check(_c_arpeggio, record=True, start_bar=3)),
    Case(8, "The bass and kick are fighting", _mix_check(6.0), note="no file: carve or ask, never widen/pan low end"),
    Case(9, f"In my mix {MU} the bass and kick are fighting", _mix_check(6.0, need_diagnose=True)),
    Case(10, "The vocals are getting buried, make them sit better", _mix_check(4.0),
         note="small relative moves only"),
]


def judge(case: Case, turn) -> tuple[bool, str]:
    calls = [{"tool": c["tool"], "args": coerce_args(c["tool"], c["args"]) if c["tool"] in TOOL_NAMES else c["args"]}
             for c in turn.attempted_calls]
    if turn.invented_tools:
        return False, f"invented tools {turn.invented_tools}"
    try:
        return case.check(calls, turn)
    except Exception as exc:  # noqa: BLE001 - malformed model output is a failed case
        return False, f"check failed: {type(exc).__name__}: {exc}"


def run_suite(provider, cases=CASES, pause_s: float = 0.0) -> dict:
    agent = Agent(provider, temperature=0.0)
    rows = []
    for i, case in enumerate(cases):
        if i and pause_s:
            time.sleep(pause_s)
        _fresh_fl()
        t0 = time.time()
        try:
            turn = agent.run_turn(f"theory-{case.id}", case.prompt)
            ok, detail = judge(case, turn)
            row = {"id": case.id, "prompt": case.prompt, "passed": ok, "detail": detail, "calls": turn.attempted_calls,
                   "reply": turn.reply, "invented": turn.invented_tools,
                   "writes_after_clarification": turn.writes_after_clarification}
        except Exception as exc:  # noqa: BLE001 - an LLM failure is a failed case, not a crashed suite
            row = {"id": case.id, "prompt": case.prompt, "passed": False, "detail": f"{type(exc).__name__}: {exc}",
                   "calls": [], "reply": "", "invented": [], "writes_after_clarification": 0, "error": True}
        row["seconds"] = round(time.time() - t0, 1)
        rows.append(row)
        print(f"{'PASS' if row['passed'] else 'FAIL'} {case.id:2d} {row['seconds']:5.1f}s  {row['detail'][:150]}",
              flush=True)
    return {
        "provider": provider.name, "model": provider.model, "temperature": 0.0,
        "date": datetime.now(timezone.utc).isoformat(), "score": sum(r["passed"] for r in rows), "total": len(rows),
        "invented_tools": sorted({n for r in rows for n in r["invented"]}),
        "writes_after_clarification": sum(r["writes_after_clarification"] for r in rows), "rows": rows,
    }


def rejudge(path: Path) -> dict:
    """Re-score a saved results file with the current checks (no LLM calls); LLM-failure rows stay failed."""
    data = json.loads(path.read_text())
    cases = {c.id: c for c in CASES}
    for row in data["rows"]:
        if row.get("error") or row["id"] not in cases:
            continue
        turn = SimpleNamespace(attempted_calls=row["calls"], invented_tools=row["invented"], proposals=[],
                               writes_after_clarification=row["writes_after_clarification"])
        row["passed"], row["detail"] = judge(cases[row["id"]], turn)
    data["score"] = sum(r["passed"] for r in data["rows"])
    path.write_text(json.dumps(data, indent=1, default=str))
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", default="vertex", choices=["vertex", "aistudio", "ollama"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--only", type=int, nargs="*", help="run only these case ids")
    parser.add_argument("--tag", default="", help="suffix for the results file, e.g. 'before'")
    parser.add_argument("--pause", type=float, default=0.0, help="seconds between prompts (rate limits)")
    parser.add_argument("--rejudge", type=Path, nargs="*", help="re-score saved results files and exit")
    args = parser.parse_args()
    if args.rejudge:
        for path in args.rejudge:
            data = rejudge(path)
            print(f"{path.name}: {data['score']}/{data['total']}")
        return 0

    from mixassist.envfile import load_env
    from mixassist.llm import make_provider

    load_env()
    if not MU.exists():
        import subprocess

        subprocess.run([sys.executable, "-m", "fixtures.make", "--out", str(ROOT / "fixtures")], cwd=ROOT, check=True)
    provider = make_provider(args.llm, args.model)
    print(f"model check: {provider.check_model()[1]}")
    cases = [c for c in CASES if not args.only or c.id in args.only]
    result = run_suite(provider, cases, args.pause)
    slug = provider.model.replace(":", "_").replace("/", "_")
    out = RESULTS / f"theory_suite-{provider.name}-{slug}{'-' + args.tag if args.tag else ''}.json"
    if not args.only:
        RESULTS.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=1, default=str))
    print(f"\n{provider.name} {provider.model}: {result['score']}/{result['total']}, invented={result['invented_tools']}, "
          f"writes_after_clarification={result['writes_after_clarification']}"
          + ("" if args.only else f"  ({out.relative_to(ROOT)})"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
