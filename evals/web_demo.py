"""Web-tool demos through the real HTTP API (uvicorn subprocess, FakeFL, real Gemma, live web search).

    .venv/bin/python -m evals.web_demo --llm vertex
    .venv/bin/python -m evals.web_demo --llm ollama

Writes evals/demos/web-<llm>.json (full transcript) and .md (readable summary).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

import httpx

from evals.run_demo import DEMOS, ROOT, Run, _free_port

KEY_TEMPO = ("What key and tempo is 'Blinding Lights' by The Weeknd? "
             "Then play its main synth riff idea as a short arpeggio")
LOUDNESS = "What loudness does Spotify normalize to?"

_NOTE_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_MAJOR, _MINOR = (0, 2, 4, 5, 7, 9, 11), (0, 2, 3, 5, 7, 8, 10)
_KEY = re.compile(r"\b([A-G])\s?([#♯b♭])?\s?(major|minor|maj|min)\b", re.I)


def _key_scale(text: str) -> tuple[str, set[int]] | None:
    m = _KEY.search(text or "")
    if not m:
        return None
    tonic = (_NOTE_PC[m.group(1).upper()] + {"#": 1, "♯": 1, "b": -1, "♭": -1}.get(m.group(2) or "", 0)) % 12
    minor = m.group(3).lower().startswith("min")
    scale = {(tonic + s) % 12 for s in (_MINOR if minor else _MAJOR)}
    if minor:
        scale.add((tonic + 11) % 12)  # harmonic minor's leading tone is fine in a riff
    return m.group(0), scale


def _web_urls(chat: dict) -> list[str]:
    urls = []
    for t in chat.get("tool_trace", []):
        r = t["result"]
        if t["tool"] == "web_search":
            urls += [x["url"] for x in r.get("results", [])]
        elif t["tool"] == "web_fetch" and r.get("url"):
            urls.append(r["url"])
    return urls


def _cites(reply: str, chat: dict, question: str) -> bool:
    """A URL, a result title, or a result's site name (e.g. "Tunebat" for tunebat.com) not already in the question."""
    reply, question = (reply or "").lower(), question.lower()
    if "http" in reply:
        return True
    hosts = {re.sub(r"^www\.", "", httpx.URL(u).host) for u in _web_urls(chat)}
    sites = {h.split(".")[-2] for h in hosts if h.count(".") >= 1}
    squashed = re.sub(r"[^a-z0-9]", "", reply)
    if any(len(s) >= 4 and s.replace("-", "") in squashed and s not in question for s in sites):
        return True
    titles = [t["result"].get("title", "") for t in chat.get("tool_trace", []) if t["tool"] == "web_fetch"]
    titles += [x["title"] for t in chat.get("tool_trace", []) if t["tool"] == "web_search"
               for x in t["result"].get("results", [])]
    return any(h in reply for h in hosts) or any(t and t.lower()[:25] in reply for t in titles)


def key_tempo(run: Run) -> None:
    chat = run.call("POST", "/api/chat", {"session_id": "web-key", "message": KEY_TEMPO})
    tools = [t["tool"] for t in chat.get("tool_trace", [])]
    reply = chat.get("reply", "")
    run.check("Gemma searched the web", "web_search" in tools, json.dumps(tools))
    run.check("at most 6 tool calls", len(tools) <= 6, str(len(tools)))
    bpm = re.search(r"\b(1[0-9]{2}|[6-9][0-9])\s?(?:BPM|bpm)", reply)
    key = _key_scale(reply)
    run.check("reply states a key and a BPM", bool(bpm and key), f"key={key[0] if key else None} bpm={bpm.group(0) if bpm else None}")
    run.check("reply cites a source", _cites(reply, chat, KEY_TEMPO), reply[:200])
    if key:
        texts = " ".join(x["snippet"] for t in chat.get("tool_trace", []) if t["tool"] == "web_search"
                         for x in t["result"].get("results", []))
        texts += " ".join(t["result"].get("text", "") for t in chat.get("tool_trace", []) if t["tool"] == "web_fetch")
        sources = {(m.group(1).upper(), (m.group(2) or "").replace("♯", "#").replace("♭", "b"), m.group(3)[:3].lower())
                   for m in _KEY.finditer(texts)}
        m = _KEY.search(reply)
        stated = (m.group(1).upper(), (m.group(2) or "").replace("♯", "#").replace("♭", "b"), m.group(3)[:3].lower())
        run.check("stated key appears in a source text", stated in sources, f"reply={key[0]} sources={sorted(sources)}")
    plays = [t for t in chat.get("tool_trace", []) if t["tool"] == "fl_play_midi"]
    played = next((t for t in plays if "error" not in t["result"]), None)
    run.check("Gemma played an arpeggio live with fl_play_midi", played is not None and not played["args"].get("record"),
              json.dumps([t["args"] for t in plays])[:300])
    if played and key and played["args"].get("notes"):
        pitches = [n["pitch"] for n in played["args"]["notes"]]
        outside = [p for p in pitches if p % 12 not in key[1]]
        run.check(f"all notes in {key[0]}", not outside, f"pitches={pitches} outside={outside}")
    run.check("no write proposals", not chat.get("proposals"), json.dumps([p["preview"]["summary"] for p in chat.get("proposals", [])]))


def loudness(run: Run) -> None:
    chat = run.call("POST", "/api/chat", {"session_id": "web-lufs", "message": LOUDNESS})
    tools = [t["tool"] for t in chat.get("tool_trace", [])]
    reply = chat.get("reply", "")
    run.check("Gemma searched the web", "web_search" in tools, json.dumps(tools))
    run.check("reply says -14 LUFS", bool(re.search(r"[-−–]\s?14\s?(dB\s?)?LUFS", reply)), reply[:200])
    run.check("reply cites a source", _cites(reply, chat, LOUDNESS), reply[:200])
    run.check("no write proposals", not chat.get("proposals"))


def _markdown(run: Run) -> str:
    m = run.meta
    lines = [f"# Demo: web tools ({m['llm']})", "", f"- LLM provider: `{m['llm']}`, model `{m['model']}`",
             f"- Started: {m['started']}", f"- Web backend: `{m.get('web_backend')}`", "- FL backend: FakeFL", "",
             "## Checks", ""]
    lines += [f"- {'PASS' if c['passed'] else 'FAIL'} {c['check']}" + (f": `{c['detail'][:240]}`" if c["detail"] else "")
              for c in run.checks]
    lines += ["", "## Transcript", ""]
    for step in run.log:
        resp = step["response"]
        lines.append(f"### {step['request']} ({step['seconds']} s)")
        if step["body"]:
            lines.append(f"\nUser: {step['body'].get('message', json.dumps(step['body']))}\n")
        if "reply" not in resp:
            lines.append(f"```json\n{json.dumps(resp, indent=1)[:1500]}\n```\n")
            continue
        lines.append(f"Gemma: {resp['reply']}\n")
        for i, t in enumerate(resp.get("tool_trace", []), 1):
            r = t["result"]
            lines.append(f"{i}. `{t['tool']}` `{json.dumps(t['args'])[:300]}`")
            if "error" in r and len(r) == 1:
                lines.append(f"   - error {r['error']['code']}: {r['error']['message']}")
            elif t["tool"] == "web_search":
                lines += [f"   - [{x['title']}]({x['url']}): {x['snippet'][:160]}" for x in r.get("results", [])]
            elif t["tool"] == "web_fetch":
                lines.append(f"   - [{r.get('title') or r['url']}]({r['url']}): {len(r.get('text', ''))} chars, "
                             f"starts \"{r.get('text', '')[:160].replace(chr(10), ' ')}\"")
            else:
                lines.append(f"   - `{json.dumps({k: r[k] for k in list(r)[:6]})[:300]}`")
        for p in resp.get("proposals", []):
            lines.append(f"- proposal `{p['proposal_id']}` {p['tool']}: {p['preview']['summary']}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    from mixassist.envfile import load_env
    from mixassist.llm import DEFAULT_MODELS
    from mixassist.web.backends import backend_name

    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", default=os.environ.get("SOUNDPILOT_LLM", "vertex"))
    parser.add_argument("--model", default=None)
    parser.add_argument("--suffix", default="")
    args = parser.parse_args()

    model = args.model or DEFAULT_MODELS[args.llm]
    port = _free_port()
    env = {**os.environ, "SOUNDPILOT_LLM": args.llm, "SOUNDPILOT_MODEL": model, "SOUNDPILOT_FL": "fake",
           "SOUNDPILOT_WEB": "1", "SOUNDPILOT_LLM_FALLBACK": "", "PYTHONPATH": str(ROOT)}
    name = f"web-{args.llm}{args.suffix}"
    (ROOT / "evals" / "out").mkdir(parents=True, exist_ok=True)
    DEMOS.mkdir(parents=True, exist_ok=True)
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
        run = Run(base, "web", args.llm, model)
        run.meta["web_backend"] = backend_name()
        key_tempo(run)
        loudness(run)
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
