"""HTTP API (build brief) through FastAPI's TestClient with FakeFL and a scripted LLM."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mixassist import agent as agent_mod
from mixassist import api
from mixassist.agent import Agent

from test_agent import ScriptedLLM

ROOT = Path(__file__).resolve().parents[1]
HAS_AUDIO = (ROOT / "mixassist" / "tools" / "audio_tools.py").exists()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "start_model_check", lambda: None)
    agent_mod.reset_proposals()
    with TestClient(api.app) as c:
        yield c
    agent_mod.set_agent(None)


def _agent(responses):
    agent_mod.set_agent(Agent(ScriptedLLM(responses)))


def test_status(client):
    body = client.get("/api/status").json()
    assert body["fl_backend"] == "fake" and body["fl_connected"] is True
    assert {"llm_provider", "model", "model_available"} <= set(body)


def test_mix(client):
    assert len(client.get("/api/mix").json()["tracks"]) == 13
    assert len(client.get("/api/mix?include_empty=true").json()["tracks"]) == 128


def test_cors(client):
    r = client.options("/api/mix", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_chat_apply_undo_flow(client):
    _agent([[("fl_set_track", {"track": "Kick", "volume_change_db": -2})], "Proposed."])
    body = client.post("/api/chat", json={"session_id": "s1", "message": "turn the kick down 2 dB"}).json()
    assert set(body) == {"reply", "tool_trace", "proposals", "needs_clarification"}
    pid = body["proposals"][0]["proposal_id"]
    applied = client.post(f"/api/proposals/{pid}/apply").json()
    assert applied["proposal_id"] == pid and applied["tool"] == "fl_set_track"
    assert applied["result"]["after"]["volume_db"] == pytest.approx(-2.0, abs=0.25)
    kick = next(t for t in client.get("/api/mix").json()["tracks"] if t["name"] == "Kick")
    assert kick["volume_db"] == pytest.approx(-2.0, abs=0.25)
    undo = client.post("/api/undo", json={"change_id": applied["result"]["change_id"]}).json()
    assert undo["restored"]["volume_db"] == pytest.approx(0.0, abs=0.1)
    assert client.post("/api/undo", json={}).json()["error"]["code"] == "NOT_FOUND"


def test_discard_and_unknown(client):
    _agent([[("fl_set_track", {"track": "Kick", "pan_pct": 10})], "Proposed."])
    pid = client.post("/api/chat", json={"session_id": "s", "message": "pan kick"}).json()["proposals"][0]["proposal_id"]
    assert client.post(f"/api/proposals/{pid}/discard").json() == {"proposal_id": pid, "status": "discarded"}
    assert "error" in client.post(f"/api/proposals/{pid}/apply").json()["result"]
    assert client.post("/api/proposals/p_nope/apply").status_code == 404
    assert client.post("/api/proposals/p_nope/discard").status_code == 404


def test_malformed_requests_are_422(client):
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 422
    assert client.post("/api/analyze/audio", json={}).status_code == 422
    assert client.post("/api/verify", json={"complaint": "x"}).status_code == 422


def test_tool_errors_are_200(client):
    r = client.post("/api/analyze/midi", json={"path": "/nonexistent/x.mid"})
    assert r.status_code == 200 and "error" in r.json()


def test_llm_failure_is_502(client):
    from mixassist.llm.base import LLMError

    class Broken(ScriptedLLM):
        def chat(self, *a, **k):
            raise LLMError("quota")

    agent_mod.set_agent(Agent(Broken([])))
    r = client.post("/api/chat", json={"session_id": "s", "message": "hi"})
    assert r.status_code == 502 and r.json()["error"]["code"] == "LLM_ERROR"


def test_watch_peaks_returns_proposals(client, fake_fl):
    from mixassist.fl.fake import VirtualClock

    fake_fl.clock = VirtualClock()
    body = client.post("/api/watch_peaks", json={"seconds": 5}).json()
    assert 8 in body["clipping"]
    assert body["proposals"][0]["args"] == {"track": 8, "volume_change_db": -7.0}
    assert body["proposals"][0]["preview"]["changes"][0]["after"] == pytest.approx(-12.0, abs=0.01)


def test_midi_endpoints(client, tmp_path, monkeypatch):
    monkeypatch.setenv("SOUNDPILOT_PYSCRIPT_DIR", str(tmp_path))
    mid = tmp_path / "melody.mid"
    shutil.copy(ROOT / "fixtures" / "midi" / "melody_c_major_3wrong.mid", mid)
    analysis = client.post("/api/analyze/midi", json={"path": str(mid)}).json()
    assert len(analysis["out_of_key"]) == 3
    fix = client.post("/api/midi/fix", json={"path": str(mid), "fix": "snap_to_scale"}).json()
    p = fix["proposals"][0]
    assert p["tool"] == "fix_midi" and p["preview"]["track"] is None and "melody.mid" in p["preview"]["summary"]
    applied = client.post(f"/api/proposals/{p['proposal_id']}/apply").json()
    assert applied["result"]["user_action"] and len(applied["result"]["changed"]) == 3
    assert client.post("/api/midi/fix", json={"path": str(mid), "fix": "explode"}).json()["error"]["code"] == "OUT_OF_RANGE"
    rec = client.post("/api/midi/play", json={"path": str(mid), "record": True}).json()
    assert rec["proposals"][0]["tool"] == "fl_play_midi" and rec["proposals"][0]["args"]["record"] is True


@pytest.mark.skipif(not HAS_AUDIO, reason="audio tools not available yet")
def test_bright_flow_endpoints(client, fixtures_dir):
    audio = fixtures_dir / "audio"
    a = client.post("/api/analyze/audio", json={"path": str(audio / "bright.wav"),
                                                "reference_path": str(audio / "pink.wav")}).json()
    assert "analysis_id" in a, a
    d = client.post("/api/diagnose", json={"complaint": "it's too bright", "analysis_id": a["analysis_id"]}).json()
    assert d["needs_clarification"] is False and d["proposals"]
    p = d["proposals"][0]
    assert p["args"]["eq_band"] == "high" and -6 <= p["args"]["eq_gain_db"] <= -4
    applied = client.post(f"/api/proposals/{p['proposal_id']}/apply").json()
    assert applied["result"]["verified"] is True
    v = client.post("/api/verify", json={"complaint": "too bright", "before_analysis_id": a["analysis_id"],
                                         "after_path": str(audio / "bright_fixed.wav")}).json()
    assert v["passed"] is True
