"""Per-turn chat latency on FakeFL: wall time, LLM calls, LLM vs tool vs retry-wait time, tokens, FL round trips.

Each prompt runs one agent turn in a fresh session on a fresh FakeFL (virtual clock, so playback and peak
watching cost no real time). The provider is wrapped, not changed: the bench times every ``provider.chat``,
reads token usage from the raw response, and counts the provider's retry sleeps separately.

    .venv/bin/python -m evals.latency_bench --llm vertex --label before
    .venv/bin/python -m evals.latency_bench --llm ollama --label after
    .venv/bin/python -m evals.latency_bench --fl-only --rt-ms 5     # SysEx round trips per FL read

Results go to evals/results/latency-<provider>-<label>.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import types
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIO = ROOT / "fixtures" / "audio"
RESULTS = ROOT / "evals" / "results"

from mixassist import agent as agent_mod  # noqa: E402
from mixassist.fl import set_fl  # noqa: E402
from mixassist.fl.client import FLClient  # noqa: E402
from mixassist.fl.fake import FakeTransport, VirtualClock, default_fake  # noqa: E402
from mixassist.llm.base import LLMProvider  # noqa: E402

PROMPTS = [
    ("kick_down", "turn the kick down 2 dB"),
    ("overview", "what's on my mixer?"),
    ("pan_hats", "pan the hats 30% left"),
    ("bright", f"analyze {AUDIO / 'bright.wav'} against {AUDIO / 'pink.wav'}, it's too bright"),
    ("arpeggio", "play an E major arpeggio"),
    ("clipping", "is anything clipping?"),
]


class Stats:
    def __init__(self):
        self.llm_calls: list[dict] = []
        self.retry_wait_s = 0.0
        self.retries = 0
        self.tool_s = 0.0
        self.tool_calls = 0
        self.round_trips = 0


_current: Stats | None = None


def _usage(raw) -> dict:
    """Input/output/thought tokens from a google-genai response or an Ollama /api/chat reply."""
    meta = getattr(raw, "usage_metadata", None)
    if meta is not None:
        return {"in": getattr(meta, "prompt_token_count", None), "out": getattr(meta, "candidates_token_count", None),
                "thought": getattr(meta, "thoughts_token_count", None)}
    if isinstance(raw, dict):
        return {"in": raw.get("prompt_eval_count"), "out": raw.get("eval_count"), "thought": None}
    return {"in": None, "out": None, "thought": None}


class TimedProvider(LLMProvider):
    def __init__(self, inner: LLMProvider):
        self.inner = inner

    @property
    def name(self):
        return self.inner.name

    @property
    def model(self):
        return self.inner.model

    def __getattr__(self, item):
        return getattr(self.inner, item)

    def chat(self, messages, tools, temperature=0.0):
        stats = _current
        wait0 = stats.retry_wait_s if stats else 0.0
        t0 = time.monotonic()
        response = None
        try:
            response = self.inner.chat(messages, tools, temperature)
            return response
        finally:
            if stats is not None:
                total = time.monotonic() - t0
                waited = stats.retry_wait_s - wait0
                usage = _usage(getattr(response, "raw", None))
                stats.llm_calls.append({"s": round(total - waited, 2), "retry_wait_s": round(waited, 2), **usage})

    def check_model(self):
        return self.inner.check_model()


def _sleep_shim(module):
    """Replace ``module.time`` with a copy whose sleep() is counted as retry wait."""
    real = module.time

    def sleep(seconds):
        if _current is not None:
            _current.retry_wait_s += seconds
            _current.retries += 1
        real.sleep(seconds)

    shim = types.SimpleNamespace(**{k: getattr(real, k) for k in dir(real) if not k.startswith("__")})
    shim.sleep = sleep
    module.time = shim


def _patch_tools():
    real = agent_mod.call_tool

    def timed(name, args):
        t0 = time.monotonic()
        try:
            return real(name, args)
        finally:
            if _current is not None:
                _current.tool_s += time.monotonic() - t0
                _current.tool_calls += 1

    agent_mod.call_tool = timed


class CountingTransport:
    """FakeTransport wrapper: counts SysEx round trips and optionally adds a per-round-trip delay."""

    def __init__(self, inner, rt_ms: float = 0.0):
        self.inner, self.rt_s, self.count = inner, rt_ms / 1000.0, 0

    def send_sysex(self, frame):
        self.count += 1
        if _current is not None:
            _current.round_trips += 1
        if self.rt_s:
            time.sleep(self.rt_s)
        return self.inner.send_sysex(frame)

    def __getattr__(self, item):
        return getattr(self.inner, item)


def _fresh_fl(rt_ms: float = 0.0) -> CountingTransport:
    transport = CountingTransport(FakeTransport(default_fake(clock=VirtualClock())), rt_ms)
    set_fl(FLClient(transport, "fake"))
    agent_mod.reset_proposals()
    return transport


@contextmanager
def _measuring(stats: Stats):
    global _current
    _current = stats
    try:
        yield
    finally:
        _current = None


def run_turns(provider: LLMProvider, prompts=PROMPTS, pause_s: float = 0.0, rt_ms: float = 0.0) -> list[dict]:
    from mixassist.llm import genai_provider, ollama_provider

    for module in (genai_provider, ollama_provider):
        if not isinstance(module.time, types.SimpleNamespace):
            _sleep_shim(module)
    if not getattr(agent_mod.call_tool, "_timed", False):
        _patch_tools()
        agent_mod.call_tool._timed = True
    agent = agent_mod.Agent(TimedProvider(provider), temperature=0.0)
    rows = []
    for i, (key, prompt) in enumerate(prompts):
        if i and pause_s:
            time.sleep(pause_s)
        _fresh_fl(rt_ms)
        stats = Stats()
        t0 = time.monotonic()
        error = None
        with _measuring(stats):
            try:
                turn = agent.run_turn(f"bench-{key}-{time.time_ns()}", prompt)
            except Exception as exc:  # noqa: BLE001
                turn, error = None, f"{type(exc).__name__}: {exc}"
        wall = time.monotonic() - t0
        llm_s = sum(c["s"] for c in stats.llm_calls)
        row = {
            "turn": key, "prompt": prompt, "wall_s": round(wall, 2), "llm_calls": len(stats.llm_calls),
            "llm_s": round(llm_s, 2), "retry_wait_s": round(stats.retry_wait_s, 2), "retries": stats.retries,
            "tool_s": round(stats.tool_s, 3), "tool_calls": stats.tool_calls, "fl_round_trips": stats.round_trips,
            "in_tokens": sum(c["in"] or 0 for c in stats.llm_calls),
            "out_tokens": sum(c["out"] or 0 for c in stats.llm_calls),
            "thought_tokens": sum(c["thought"] or 0 for c in stats.llm_calls),
            "calls": stats.llm_calls, "error": error,
        }
        if turn is not None:
            row.update(tools=[c["tool"] for c in turn.attempted_calls], proposals=len(turn.proposals),
                       needs_clarification=turn.needs_clarification, reply=turn.reply,
                       reply_source=getattr(turn, "reply_source", "model"))
        rows.append(row)
        print(f"{key:10s} wall {wall:6.1f}s  llm {row['llm_calls']}x {llm_s:5.1f}s  wait {stats.retry_wait_s:5.1f}s "
              f"({stats.retries} retries)  tools {stats.tool_s:5.2f}s/{stats.round_trips} rt  "
              f"tok in {row['in_tokens']} out {row['out_tokens']} thought {row['thought_tokens']}  "
              f"{row.get('tools', error)}", flush=True)
    return rows


def fl_costs(rt_ms: float) -> list[dict]:
    """SysEx round trips (and time at an assumed per-round-trip latency) for the FL reads the agent does."""
    from mixassist.tools import call_tool

    cases = [
        ("fl_get_mix_overview (used tracks)", lambda: call_tool("fl_get_mix_overview", {})),
        ("fl_get_mix_overview include_empty", lambda: call_tool("fl_get_mix_overview", {"include_empty": True})),
        ("proposal preview Kick -2 dB", lambda: agent_mod.create_proposal(
            "fl_set_track", {"track": "Kick", "volume_change_db": -2}, "bench")),
        ("fl_set_track apply Kick -2 dB", lambda: call_tool("fl_set_track", {"track": "Kick", "volume_change_db": -2})),
    ]
    out = []
    for label, fn in cases:
        transport = _fresh_fl(rt_ms)
        t0 = time.monotonic()
        fn()
        dt = time.monotonic() - t0
        out.append({"what": label, "round_trips": transport.count, "seconds": round(dt, 3), "rt_ms": rt_ms})
        print(f"{label:38s} {transport.count:4d} round trips  {dt:6.2f} s at {rt_ms:g} ms/rt", flush=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", default="vertex", choices=["vertex", "aistudio", "ollama", "env"],
                        help="env: the provider the backend builds (SOUNDPILOT_LLM + SOUNDPILOT_LLM_FALLBACK chain)")
    parser.add_argument("--model", default=None)
    parser.add_argument("--label", default="run")
    parser.add_argument("--only", nargs="*", help="turn keys to run")
    parser.add_argument("--pause", type=float, default=0.0, help="seconds between turns (AI Studio free tier)")
    parser.add_argument("--rt-ms", type=float, default=5.0, help="assumed SysEx round-trip latency for --fl-only")
    parser.add_argument("--fl-only", action="store_true")
    args = parser.parse_args()

    from mixassist.envfile import load_env
    from mixassist.logctx import configure

    load_env()
    configure()
    if args.fl_only:
        fl_costs(args.rt_ms)
        return 0
    if not (AUDIO / "bright.wav").exists():
        import subprocess

        subprocess.run([sys.executable, "-m", "fixtures.make", "--out", str(ROOT / "fixtures")], cwd=ROOT, check=True)
    from mixassist.llm import make_provider

    provider = make_provider() if args.llm == "env" else make_provider(args.llm, args.model)
    kind = os.environ.get("SOUNDPILOT_LLM", "vertex") if args.llm == "env" else provider.name
    prompts = [p for p in PROMPTS if not args.only or p[0] in args.only]
    t0 = time.monotonic()
    rows = run_turns(provider, prompts, args.pause)
    total = time.monotonic() - t0
    result = {"provider": kind, "model": provider.model, "label": args.label,
              "fallback": os.environ.get("SOUNDPILOT_LLM_FALLBACK") if args.llm == "env" else None,
              "date": datetime.now(timezone.utc).isoformat(), "total_s": round(total, 1), "rows": rows}
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"latency-{kind}-{args.label}.json"
    if not args.only:
        out.write_text(json.dumps(result, indent=1, default=str))
    print(f"\n{provider.name} {provider.model} [{args.label}]: total {total:.1f} s, "
          f"{sum(r['llm_calls'] for r in rows)} LLM calls, {sum(r['retry_wait_s'] for r in rows):.1f} s retry wait"
          + ("" if args.only else f"  ({out.relative_to(ROOT)})"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
