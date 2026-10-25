"""Gemma tool-call suite (spec §8.2, adapted): 20 prompts, temperature 0, FakeFL + fixtures.

Each prompt runs one agent turn (the real loop: read tools execute, writes become proposals) with a fresh
session and a fresh FakeFL. Pass criteria: >= 16/20 prompts whose expected tool was called with arguments
satisfying the predicate, 0 invented tool names, and 0 write calls after a needs_clarification=true result.

    .venv/bin/python -m evals.gemma_suite --llm vertex
    .venv/bin/python -m evals.gemma_suite --llm ollama --model gemma4:31b-cloud
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
AUDIO = ROOT / "fixtures" / "audio"
MIDI = ROOT / "fixtures" / "midi"
RESULTS = ROOT / "evals" / "results"

from mixassist import agent as agent_mod  # noqa: E402
from mixassist.agent import Agent  # noqa: E402
from mixassist.errors import ToolError  # noqa: E402
from mixassist.fl import set_fl  # noqa: E402
from mixassist.fl.client import FLClient  # noqa: E402
from mixassist.fl.fake import FakeTransport, VirtualClock, default_fake  # noqa: E402
from mixassist.fl.tracks import resolve_track  # noqa: E402
from mixassist.schemas import TOOL_NAMES  # noqa: E402
from mixassist.tools import call_tool, coerce_args, is_write  # noqa: E402

_TRACKS = [{"index": i, "name": t.get("name", f"Insert {i}")} for i, t in enumerate(default_fake().tracks[:-1])]


def track_is(args: dict, name: str) -> bool:
    try:
        return resolve_track(args.get("track"), _TRACKS)["name"] == name
    except (ToolError, TypeError):
        return False


def near(value, target, tol=0.01) -> bool:
    try:
        return abs(float(value) - target) <= tol
    except (TypeError, ValueError):
        return False


def same_path(value, path: Path) -> bool:
    return isinstance(value, str) and Path(value).expanduser() == path


@dataclass
class Case:
    id: int
    prompt: str
    tool: str  # expected tool, or "clarify" / "no_write"
    predicate: Callable[[dict], bool] = lambda a: True
    setup: Callable[[], None] | None = None
    note: str = ""


def _seed_change():
    call_tool("fl_set_track", {"track": "Kick", "volume_change_db": -3})


B, P, BF, MU = AUDIO / "bright.wav", AUDIO / "pink.wav", AUDIO / "bright_fixed.wav", AUDIO / "muddy.wav"
MEL, SLOP, ROB, SCALE = (MIDI / "melody_c_major_3wrong.mid", MIDI / "sloppy.mid", MIDI / "robotic.mid",
                         MIDI / "scale_8.mid")

CASES = [
    Case(1, "What's on my mixer?", "fl_get_mix_overview"),
    Case(2, "Turn the kick down 2 dB", "fl_set_track",
         lambda a: track_is(a, "Kick") and (near(a.get("volume_change_db"), -2) or near(a.get("volume_db"), -2))),
    Case(3, "Pan the snare 30% to the left", "fl_set_track",
         lambda a: track_is(a, "Snare") and near(a.get("pan_pct"), -30)),
    Case(4, "Set the bass fader to exactly -6 dB", "fl_set_track",
         lambda a: track_is(a, "Bass") and near(a.get("volume_db"), -6)),
    Case(5, "Cut 3 dB of low end on the Bass with the built-in EQ at 120 Hz", "fl_set_track",
         lambda a: track_is(a, "Bass") and a.get("eq_band") == "low" and near(a.get("eq_gain_db"), -3)
         and (a.get("eq_freq_hz") is None or near(a.get("eq_freq_hz"), 120, 1))),
    Case(6, "Undo that last change", "fl_undo", setup=_seed_change),
    Case(7, "Is anything clipping? Listen for 10 seconds.", "fl_watch_peaks",
         lambda a: a.get("seconds") is None or near(a.get("seconds"), 10)),
    Case(8, f"Analyze my mix at {P}", "analyze_audio", lambda a: same_path(a.get("path"), P)),
    Case(9, f"How loud is {B} compared with my reference {P}?", "analyze_audio",
         lambda a: same_path(a.get("path"), B) and same_path(a.get("reference_path"), P)),
    Case(10, f"My mix {B} is too bright compared to my reference {P}.", "diagnose_complaint",
         lambda a: "bright" in str(a.get("complaint", "")).lower() and bool(a.get("analysis_id"))),
    Case(11, f"The export at {MU} sounds boxy.", "diagnose_complaint",
         lambda a: bool(a.get("complaint")) and bool(a.get("analysis_id"))),
    Case(12, f"My mix at {P} sounds bad. Fix it.", "clarify", note="vague: diagnose must ask, no writes"),
    Case(13, "Ugh, the whole thing just sounds off. Make it better.", "no_write",
         note="vague and no file: ask for an export, propose nothing"),
    Case(14, f"I exported {B}, then fixed the brightness and re-exported to {BF}. Did the fix work?", "verify_fix",
         lambda a: same_path(a.get("after_path"), BF) and bool(a.get("before_analysis_id"))),
    Case(15, f"Does the melody in {MEL} have any wrong notes?", "analyze_midi", lambda a: same_path(a.get("path"), MEL)),
    Case(16, f"Fix the out-of-key notes in {MEL}", "fix_midi",
         lambda a: same_path(a.get("path"), MEL) and a.get("fix") == "snap_to_scale"),
    Case(17, f"Quantize {SLOP} to 16th notes", "fix_midi",
         lambda a: same_path(a.get("path"), SLOP) and a.get("fix") == "quantize" and near(a.get("grid_beats"), 0.25)),
    Case(18, f"{ROB} sounds robotic and stiff, make it feel more human", "fix_midi",
         lambda a: same_path(a.get("path"), ROB) and a.get("fix") == "humanize"),
    Case(19, f"Play {SCALE} on my selected synth so I can hear it", "fl_play_midi",
         lambda a: same_path(a.get("path"), SCALE) and not a.get("record")),
    Case(20, f"Record {SCALE} into the piano roll starting at bar 5", "fl_play_midi",
         lambda a: same_path(a.get("path"), SCALE) and a.get("record") in (True, "true") and near(a.get("start_bar"), 5)),
]


def _fresh_fl():
    set_fl(FLClient(FakeTransport(default_fake(clock=VirtualClock())), "fake"))
    agent_mod.reset_proposals()


def judge(case: Case, turn) -> tuple[bool, str]:
    calls = [{"tool": c["tool"], "args": coerce_args(c["tool"], c["args"]) if c["tool"] in TOOL_NAMES else c["args"]}
             for c in turn.attempted_calls]
    writes = [c for c in calls if c["tool"] in TOOL_NAMES and is_write(c["tool"], c["args"])]
    if case.tool == "clarify":
        diag = [t for t in turn.tool_trace if t["tool"] == "diagnose_complaint" and t["result"].get("needs_clarification")]
        ok = bool(diag) and turn.needs_clarification and not turn.proposals
        return ok, "asked: " + str(turn.question) if ok else "no clarification via diagnose_complaint"
    if case.tool == "no_write":
        ok = not writes and not turn.proposals
        return ok, "no writes proposed" if ok else f"wrote {[w['tool'] for w in writes]}"
    matching = [c for c in calls if c["tool"] == case.tool]
    if not matching:
        return False, f"expected {case.tool}, got {[c['tool'] for c in calls] or 'no tool call'}"
    for c in matching:
        try:
            if case.predicate(c["args"]):
                return True, json.dumps(c["args"], default=str)
        except Exception:  # noqa: BLE001
            pass
    return False, "args failed predicate: " + json.dumps([c["args"] for c in matching], default=str)


def run_suite(provider, cases=CASES) -> dict:
    agent = Agent(provider, temperature=0.0)
    rows = []
    for case in cases:
        _fresh_fl()
        if case.setup:
            case.setup()
        t0 = time.time()
        try:
            turn = agent.run_turn(f"eval-{case.id}", case.prompt)
            ok, detail = judge(case, turn)
            row = {"id": case.id, "prompt": case.prompt, "expected": case.tool, "passed": ok, "detail": detail,
                   "calls": turn.attempted_calls, "reply": turn.reply, "invented": turn.invented_tools,
                   "writes_after_clarification": turn.writes_after_clarification,
                   "text_fallbacks": turn.text_fallbacks, "needs_clarification": turn.needs_clarification}
        except Exception as exc:  # noqa: BLE001 - an LLM failure is a failed case, not a crashed suite
            row = {"id": case.id, "prompt": case.prompt, "expected": case.tool, "passed": False,
                   "detail": f"{type(exc).__name__}: {exc}", "calls": [], "reply": "", "invented": [],
                   "writes_after_clarification": 0, "text_fallbacks": 0, "needs_clarification": False}
        row["seconds"] = round(time.time() - t0, 1)
        rows.append(row)
        print(f"{'PASS' if row['passed'] else 'FAIL'} {case.id:2d} {case.tool:20s} {row['seconds']:5.1f}s  {row['detail'][:110]}",
              flush=True)
    score = sum(r["passed"] for r in rows)
    invented = sorted({n for r in rows for n in r["invented"]})
    wac = sum(r["writes_after_clarification"] for r in rows)
    return {
        "provider": provider.name, "model": provider.model, "temperature": 0.0,
        "date": datetime.now(timezone.utc).isoformat(), "score": score, "total": len(rows),
        "invented_tools": invented, "writes_after_clarification": wac,
        "text_fallbacks": sum(r["text_fallbacks"] for r in rows),
        "passed": score >= 16 and not invented and wac == 0, "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", default="vertex", choices=["vertex", "aistudio", "ollama"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--only", type=int, nargs="*", help="run only these case ids")
    args = parser.parse_args()

    from mixassist.envfile import load_env
    from mixassist.llm import make_provider

    load_env()

    if not (AUDIO / "bright.wav").exists():
        import subprocess

        subprocess.run([sys.executable, "-m", "fixtures.make", "--out", str(ROOT / "fixtures")], cwd=ROOT, check=True)
    provider = make_provider(args.llm, args.model)
    print(f"model check: {provider.check_model()[1]}")
    cases = [c for c in CASES if not args.only or c.id in args.only]
    result = run_suite(provider, cases)
    RESULTS.mkdir(parents=True, exist_ok=True)
    slug = provider.model.replace(":", "_").replace("/", "_")
    out = RESULTS / f"gemma_suite-{provider.name}-{slug}.json"
    if not args.only:
        out.write_text(json.dumps(result, indent=1, default=str))
    print(f"\n{provider.name} {provider.model}: {result['score']}/{result['total']}, invented={result['invented_tools']}, "
          f"writes_after_clarification={result['writes_after_clarification']}, text_fallbacks={result['text_fallbacks']} "
          f"-> {'PASS' if result['passed'] else 'FAIL'}" + ("" if args.only else f"  ({out.relative_to(ROOT)})"))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
