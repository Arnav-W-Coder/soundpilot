"""End-to-end demos through the real HTTP API (uvicorn subprocess, FakeFL, real Gemma).

    .venv/bin/python -m evals.run_demo tracer --llm vertex
    .venv/bin/python -m evals.run_demo bright --llm ollama --model gemma4:31b-cloud

Writes evals/demos/<demo>-<llm>.json (full transcript) and .md (readable summary).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEMOS = ROOT / "evals" / "demos"
AUDIO = ROOT / "fixtures" / "audio"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Run:
    def __init__(self, base: str, demo: str, llm: str, model: str):
        self.base = base
        self.http = httpx.Client(base_url=base, timeout=600)
        self.log: list[dict] = []
        self.checks: list[dict] = []
        self.meta = {"demo": demo, "llm": llm, "model": model, "started": datetime.now(timezone.utc).isoformat()}

    def call(self, method: str, path: str, body: dict | None = None, note: str = "") -> dict:
        t0 = time.time()
        r = self.http.request(method, path, json=body)
        data = r.json()
        self.log.append({"request": f"{method} {path}", "body": body, "status": r.status_code,
                         "seconds": round(time.time() - t0, 2), "response": data, "note": note})
        return data

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"check": name, "passed": bool(ok), "detail": detail})
        print(("PASS " if ok else "FAIL ") + name + (f" ({detail})" if detail else ""))
        return ok


def tracer(run: Run) -> None:
    status = run.call("GET", "/api/status")
    run.check("model available", status.get("model_available") is not False, str(status.get("model_message")))
    before = next(t for t in run.call("GET", "/api/mix")["tracks"] if t["name"] == "Kick")
    chat = run.call("POST", "/api/chat", {"session_id": "tracer", "message": "turn the kick down 2 dB"})
    props = [p for p in chat.get("proposals", []) if p["tool"] == "fl_set_track"]
    run.check("Gemma proposed fl_set_track", bool(props), json.dumps([t["tool"] for t in chat.get("tool_trace", [])]))
    if not props:
        return
    p = props[0]
    run.check("proposal targets Kick", p["preview"]["track"]["name"] == "Kick", p["preview"]["summary"])
    mid = next(t for t in run.call("GET", "/api/mix", note="nothing applied before approval")["tracks"] if t["name"] == "Kick")
    run.check("FL unchanged before approval", abs(mid["volume_db"] - before["volume_db"]) < 1e-6)
    applied = run.call("POST", f"/api/proposals/{p['proposal_id']}/apply")
    after = applied["result"].get("after", {}).get("volume_db")
    run.check("readback -2.0 +/- 0.25 dB", after is not None and abs(after - (before["volume_db"] - 2.0)) <= 0.25, f"after={after}")
    run.check("verified", applied["result"].get("verified") is True)
    kick = next(t for t in run.call("GET", "/api/mix")["tracks"] if t["name"] == "Kick")
    run.check("mix shows Kick at -2 dB", abs(kick["volume_db"] - (before["volume_db"] - 2.0)) <= 0.25, str(kick["volume_db"]))

    chat2 = run.call("POST", "/api/chat", {"session_id": "tracer", "message": "undo that"})
    undo_props = [q for q in chat2.get("proposals", []) if q["tool"] == "fl_undo"]
    if run.check("Gemma proposed fl_undo", bool(undo_props), json.dumps([t["tool"] for t in chat2.get("tool_trace", [])])):
        res = run.call("POST", f"/api/proposals/{undo_props[0]['proposal_id']}/apply")["result"]
    else:
        res = run.call("POST", "/api/undo", {"change_id": applied["result"]["change_id"]}, note="fallback: direct undo")
    restored = res.get("restored", {}).get("volume_db")
    run.check("undo restores 0.0 +/- 0.1 dB", restored is not None and abs(restored - before["volume_db"]) <= 0.1, f"restored={restored}")


def bright(run: Run) -> None:
    status = run.call("GET", "/api/status")
    run.check("model available", status.get("model_available") is not False, str(status.get("model_message")))
    mix, ref, fixed = AUDIO / "bright.wav", AUDIO / "pink.wav", AUDIO / "bright_fixed.wav"
    msg = f"My mix is too bright. The exported mix is {mix} and my reference track is {ref}."
    chat = run.call("POST", "/api/chat", {"session_id": "bright", "message": msg})
    tools = [t["tool"] for t in chat.get("tool_trace", [])]
    run.check("Gemma called analyze_audio then diagnose_complaint",
              "analyze_audio" in tools and "diagnose_complaint" in tools, json.dumps(tools))
    analysis = next((t["result"] for t in chat.get("tool_trace", []) if t["tool"] == "analyze_audio"
                     and "analysis_id" in t["result"]), None)
    diag = next((t["result"] for t in chat.get("tool_trace", []) if t["tool"] == "diagnose_complaint"
                 and "findings" in t["result"]), None)
    if analysis is None:
        analysis = run.call("POST", "/api/analyze/audio", {"path": str(mix), "reference_path": str(ref)}, note="fallback")
    if diag is None:
        diag = run.call("POST", "/api/diagnose", {"complaint": "my mix is too bright", "analysis_id": analysis["analysis_id"]},
                        note="fallback: model did not diagnose")
        props = diag.get("proposals", [])
    else:
        props = chat.get("proposals", [])
    top = (diag.get("findings") or [{}])[0]
    run.check("top finding bright, delta 6 +/- 1 dB", top.get("descriptor") == "bright" and abs(top.get("delta", 0) - 6) <= 1,
              json.dumps(top))
    shelf = [p for p in props if p["tool"] == "fl_set_track" and p["args"].get("eq_band") == "high"]
    run.check("high-shelf proposal in [-6, -4] dB", bool(shelf) and -6 <= shelf[0]["args"].get("eq_gain_db", 0) <= -4,
              shelf[0]["preview"]["summary"] if shelf else "none")
    if not shelf:
        return
    applied = run.call("POST", f"/api/proposals/{shelf[0]['proposal_id']}/apply")
    run.check("EQ change verified on FakeFL", applied["result"].get("verified") is True,
              json.dumps(applied["result"].get("after")))

    msg2 = f"I applied it and re-exported the mix to {fixed}. Did the brightness fix work?"
    chat2 = run.call("POST", "/api/chat", {"session_id": "bright", "message": msg2})
    ver = next((t["result"] for t in chat2.get("tool_trace", []) if t["tool"] == "verify_fix" and "passed" in t["result"]), None)
    run.check("Gemma called verify_fix successfully", ver is not None,
              json.dumps([(t["tool"], "error" in t["result"]) for t in chat2.get("tool_trace", [])]))
    if ver is None or "passed" not in ver:
        ver = run.call("POST", "/api/verify", {"complaint": "too bright", "before_analysis_id": analysis["analysis_id"],
                                               "after_path": str(fixed)}, note="fallback: direct verify")
    run.check("verify_fix passed", ver.get("passed") is True, json.dumps(ver.get("checks")))


def _markdown(run: Run) -> str:
    lines = [f"# Demo: {run.meta['demo']}", "",
             f"- LLM provider: `{run.meta['llm']}`, model `{run.meta['model']}`",
             f"- Started: {run.meta['started']}", "- FL backend: FakeFL (no real FL Studio)", "",
             "## Checks", ""]
    lines += [f"- {'PASS' if c['passed'] else 'FAIL'} {c['check']}" + (f": `{c['detail'][:200]}`" if c["detail"] else "")
              for c in run.checks]
    lines += ["", "## Transcript", ""]
    for step in run.log:
        lines.append(f"### {step['request']} ({step['seconds']} s){' - ' + step['note'] if step['note'] else ''}")
        if step["body"]:
            lines.append(f"Request: `{json.dumps(step['body'])}`")
        resp = step["response"]
        if "reply" in resp:
            lines.append(f"\nGemma: {resp['reply']}\n")
            for t in resp.get("tool_trace", []):
                r = t["result"]
                short = json.dumps({k: r[k] for k in list(r)[:6]})[:300]
                lines.append(f"- tool `{t['tool']}` args `{json.dumps(t['args'])}` -> `{short}`")
            for p in resp.get("proposals", []):
                lines.append(f"- proposal `{p['proposal_id']}` {p['tool']}: {p['preview']['summary']} (why: {p['why']})")
        else:
            lines.append(f"\n```json\n{json.dumps(resp, indent=1)[:1500]}\n```")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("demo", choices=["tracer", "bright"])
    parser.add_argument("--llm", default=os.environ.get("SOUNDPILOT_LLM", "vertex"))
    parser.add_argument("--model", default=None)
    parser.add_argument("--suffix", default="")
    args = parser.parse_args()

    from mixassist.llm import DEFAULT_MODELS

    model = args.model or DEFAULT_MODELS[args.llm]
    port = _free_port()
    env = {**os.environ, "SOUNDPILOT_LLM": args.llm, "SOUNDPILOT_MODEL": model, "SOUNDPILOT_FL": "fake",
           "PYTHONPATH": str(ROOT)}
    DEMOS.mkdir(parents=True, exist_ok=True)
    name = f"{args.demo}-{args.llm}{args.suffix}"
    (ROOT / "evals" / "out").mkdir(parents=True, exist_ok=True)
    server_log = open(ROOT / "evals" / "out" / f"{name}.server.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "mixassist.api:app", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=server_log, stderr=subprocess.STDOUT)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                if httpx.get(base + "/api/status", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        # give the background model check time to finish (Vertex may retry 429s)
        for _ in range(120):
            if httpx.get(base + "/api/status", timeout=5).json().get("model_available") is not None:
                break
            time.sleep(1)
        run = Run(base, args.demo, args.llm, model)
        {"tracer": tracer, "bright": bright}[args.demo](run)
    finally:
        proc.terminate()
        proc.wait(10)
        server_log.close()
    run.meta["passed"] = all(c["passed"] for c in run.checks)
    (DEMOS / f"{name}.json").write_text(json.dumps({"meta": run.meta, "checks": run.checks, "log": run.log}, indent=1))
    (DEMOS / f"{name}.md").write_text(_markdown(run))
    print(f"{'ALL PASS' if run.meta['passed'] else 'SOME CHECKS FAILED'} -> evals/demos/{name}.md")
    return 0 if run.meta["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
