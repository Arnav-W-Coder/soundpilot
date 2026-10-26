"""Streaming chat endpoint: progress steps, then the /api/chat payload or an error."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from mixassist import agent as agent_mod
from mixassist import api
from mixassist.agent import Agent
from mixassist.llm.base import LLMError

from test_agent import ScriptedLLM


@pytest.fixture(autouse=True)
def _clean():
    agent_mod.reset_proposals()
    yield
    agent_mod.reset_proposals()


# ------------------------------------------------------------- streaming endpoint
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "start_model_check", lambda: None)
    with TestClient(api.app) as c:
        yield c
    agent_mod.set_agent(None)


def _events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_chat_stream_emits_steps_then_the_chat_payload(client):
    agent_mod.set_agent(Agent(ScriptedLLM([[("fl_get_mix_overview", {})], "13 tracks."])))
    r = client.post("/api/chat/stream", json={"session_id": "s", "message": "what's on my mixer?"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = _events(r.text)
    steps = [(e["type"], e["text"]) for kind, e in events if kind == "step"]
    assert steps == [("llm", "Asking Gemma (scripted)"), ("tool", "Reading the FL mixer"),
                     ("llm", "Asking Gemma (scripted), step 2")]
    kind, result = events[-1]
    assert kind == "result" and result["reply"] == "13 tracks."
    assert set(result) == {"reply", "tool_trace", "proposals", "needs_clarification"}


def test_chat_stream_reports_llm_errors(client):
    class Broken(ScriptedLLM):
        def chat(self, *a, **k):
            raise LLMError("vertex: 429 quota")

    agent_mod.set_agent(Agent(Broken([])))
    events = _events(client.post("/api/chat/stream", json={"session_id": "s", "message": "hi"}).text)
    assert events[-1] == ("error", {"error": {"code": "LLM_ERROR", "message": "vertex: 429 quota"}})


def test_status_lists_the_fallback(client, monkeypatch):
    monkeypatch.setenv("SOUNDPILOT_LLM_FALLBACK", "ollama")
    monkeypatch.setenv("SOUNDPILOT_LLM", "vertex")
    assert client.get("/api/status").json()["llm_fallback"] == ["ollama"]
