"""Agent loop with a scripted fake LLM (no network)."""
from __future__ import annotations

import pytest

from mixassist import agent as agent_mod
from mixassist.agent import Agent
from mixassist.llm.base import LLMProvider, LLMResponse, ToolCall


class ScriptedLLM(LLMProvider):
    """Returns queued responses; records the messages it was shown."""

    name, model = "scripted", "scripted-1"

    def __init__(self, responses):
        self.responses = list(responses)
        self.seen: list[list[dict]] = []

    def chat(self, messages, tools, temperature=0.0):
        self.seen.append(list(messages))
        item = self.responses.pop(0) if self.responses else "OK."
        if isinstance(item, LLMResponse):
            return item
        if isinstance(item, list):
            return LLMResponse("", [ToolCall(n, a) for n, a in item])
        return LLMResponse(item)

    def check_model(self):
        return True, "scripted"


@pytest.fixture(autouse=True)
def _clean():
    agent_mod.reset_proposals()
    yield
    agent_mod.reset_proposals()


def test_tracer_kick_down_2db_becomes_proposal_then_apply_and_undo(fake_fl):
    llm = ScriptedLLM([[("fl_set_track", {"track": "kick", "volume_change_db": "-2"})], "Proposed: Kick down 2 dB."])
    out = Agent(llm).chat("s1", "turn the kick down 2 dB")
    assert out["needs_clarification"] is False
    assert len(out["proposals"]) == 1
    p = out["proposals"][0]
    assert set(p) == {"proposal_id", "tool", "args", "why", "status", "preview"}
    assert p["tool"] == "fl_set_track" and p["status"] == "pending"
    assert p["args"] == {"track": "kick", "volume_change_db": -2.0}
    assert p["preview"]["track"] == {"index": 1, "name": "Kick"}
    assert p["preview"]["changes"] == [{"field": "volume_db", "before": 0.0, "after": -2.0, "unit": "dB"}]
    assert p["preview"]["summary"] == "Kick: volume 0.0 dB -> -2.0 dB"
    assert fake_fl.tracks[1]["vol"] == pytest.approx(0.8)  # nothing written yet
    assert out["tool_trace"][0]["result"]["status"] == "proposed"

    applied = agent_mod.apply_proposal(p["proposal_id"])
    assert applied["result"]["after"]["volume_db"] == pytest.approx(-2.0, abs=0.25)
    assert applied["result"]["verified"] is True
    assert agent_mod.get_proposal(p["proposal_id"])["status"] == "applied"
    again = agent_mod.apply_proposal(p["proposal_id"])
    assert "error" in again["result"]

    from mixassist.tools import call_tool

    undo = call_tool("fl_undo", {"change_id": applied["result"]["change_id"]})
    assert undo["restored"]["volume_db"] == pytest.approx(0.0, abs=0.1)


def test_read_tools_run_and_results_go_back_to_model():
    llm = ScriptedLLM([[("fl_get_mix_overview", {})], "You have 13 tracks."])
    out = Agent(llm).chat("s", "what's on my mixer?")
    assert out["proposals"] == [] and out["reply"] == "You have 13 tracks."
    assert len(out["tool_trace"][0]["result"]["tracks"]) == 13
    tool_msg = llm.seen[1][-1]
    assert tool_msg["role"] == "tool" and tool_msg["name"] == "fl_get_mix_overview"
    assert llm.seen[0][0]["role"] == "system" and "clarification" in llm.seen[0][0]["content"]


def test_validation_error_returned_to_model_then_retry():
    llm = ScriptedLLM([
        [("fl_set_track", {"track": "kick", "volume": "-2"})],
        [("fl_set_track", {"track": "kick", "volume_change_db": -2})],
        "Done.",
    ])
    out = Agent(llm).chat("s", "kick down 2")
    assert out["tool_trace"][0]["result"]["error"]["code"] == "OUT_OF_RANGE"
    assert '"error"' in llm.seen[1][-1]["content"]
    assert len(out["proposals"]) == 1


def test_tool_call_cap_six():
    llm = ScriptedLLM([[("fl_get_mix_overview", {})]] * 10 + ["Summary."])
    agent = Agent(llm)
    result = agent.run_turn("s", "loop forever")
    executed = [t for t in result.tool_trace]
    assert len(executed) == 6
    assert result.hit_call_cap


def test_text_tool_call_fallback(caplog):
    llm = ScriptedLLM(['```json\n{"name": "fl_set_track", "arguments": {"track": "Snare", "pan_pct": -30}}\n```', "Proposed."])
    with caplog.at_level("WARNING"):
        result = Agent(llm).run_turn("s", "pan the snare 30% left")
    assert result.text_fallbacks == 1
    assert "text fallback" in caplog.text
    assert result.proposals[0]["preview"]["changes"][0] == {"field": "pan_pct", "before": 0.0, "after": -30.0, "unit": "%"}


def test_invented_tool_is_error_and_counted():
    llm = ScriptedLLM([[("make_it_pop", {})], "Sorry."])
    result = Agent(llm).run_turn("s", "make it pop")
    assert result.invented_tools == ["make_it_pop"]
    assert result.tool_trace[0]["result"]["error"]["code"] == "NOT_FOUND"


def test_clarification_blocks_writes(monkeypatch):
    import mixassist.tools as tools

    monkeypatch.setitem(tools.TOOLS, "diagnose_complaint", lambda a: {
        "needs_clarification": True, "question": "Is it the highs or the low mids?", "options": ["highs", "low mids"],
        "findings": [], "proposed_changes": []})
    llm = ScriptedLLM([
        [("diagnose_complaint", {"complaint": "sounds bad", "analysis_id": "abc"}),
         ("fl_set_track", {"track": 0, "eq_band": "high", "eq_gain_db": -3})],
        "Is it the highs or the low mids?",
    ])
    result = Agent(llm).run_turn("s", "it sounds bad")
    assert result.needs_clarification and result.proposals == []
    assert result.writes_after_clarification == 1
    assert result.tool_trace[1]["result"]["error"]["code"] == "NEEDS_CLARIFICATION"
    api = result.to_api()
    assert api["question"] == "Is it the highs or the low mids?" and api["options"] == ["highs", "low mids"]
    assert agent_mod.list_proposals() == []


def test_diagnosis_proposals_and_dedupe(monkeypatch):
    import mixassist.tools as tools

    change = {"tool": "fl_set_track", "args": {"track": 0, "eq_band": "high", "eq_gain_db": -5, "eq_freq_hz": 8000},
              "why": "highs +6 dB vs reference"}
    monkeypatch.setitem(tools.TOOLS, "diagnose_complaint", lambda a: {
        "needs_clarification": False, "findings": [{"descriptor": "bright", "value": 6, "target": 0, "delta": 6, "unit": "dB"}],
        "proposed_changes": [change]})
    llm = ScriptedLLM([
        [("diagnose_complaint", {"complaint": "too bright", "analysis_id": "abc"})],
        [("fl_set_track", change["args"])],
        "I propose a -5 dB high shelf on the master.",
    ])
    result = Agent(llm).run_turn("s", "too bright")
    assert len(result.proposals) == 1
    p = result.proposals[0]
    assert p["why"] == "highs +6 dB vs reference"
    assert p["preview"]["track"]["name"] == "Master"
    assert {c["field"] for c in p["preview"]["changes"]} == {"eq_gain_db", "eq_freq_hz"}
    assert "high EQ gain" in p["preview"]["summary"]


def test_ambiguous_target_becomes_clarification():
    llm = ScriptedLLM([[("fl_set_track", {"track": "hat", "pan_pct": -30})], "Which hat: Hat Open or Hat Closed?"])
    out = Agent(llm).chat("s", "pan the hat left")
    assert out["needs_clarification"] and set(out["options"]) == {"Hat Open", "Hat Closed"}
    assert out["proposals"] == []


def test_peak_suggestions_become_proposals(fake_fl):
    from mixassist.fl.fake import VirtualClock

    fake_fl.clock = VirtualClock()
    llm = ScriptedLLM([[("fl_watch_peaks", {"seconds": 5})], "Lead Synth clips."])
    out = Agent(llm).chat("s", "is anything clipping?")
    assert out["proposals"] and out["proposals"][0]["preview"]["track"]["name"] == "Lead Synth"
    assert out["proposals"][0]["args"] == {"track": 8, "volume_change_db": -7.0}


def test_write_proposals_for_undo_fix_midi_and_record(tmp_path):
    from mixassist.tools import call_tool

    call_tool("fl_set_track", {"track": "Kick", "volume_change_db": -3})
    mid = tmp_path / "x.mid"
    mid.write_bytes(b"MThd")
    llm = ScriptedLLM([
        [("fl_undo", {}), ("fix_midi", {"path": str(mid), "fix": "quantize", "grid_beats": 0.25}),
         ("fl_play_midi", {"path": str(mid), "record": True}), ("fl_play_midi", {"path": "/nope.mid", "record": True})],
        "Proposed.",
    ])
    result = Agent(llm).run_turn("s", "undo, quantize and record")
    tools = [p["tool"] for p in result.proposals]
    assert tools == ["fl_undo", "fix_midi", "fl_play_midi"]
    undo = result.proposals[0]["preview"]
    assert undo["track"] is None and undo["changes"][0]["after"] == pytest.approx(0.0, abs=0.1)
    assert "x.mid" in result.proposals[1]["preview"]["summary"]
    assert result.tool_trace[3]["result"]["error"]["code"] == "FILE_ERROR"


def test_sessions_keep_history():
    llm = ScriptedLLM(["Hi.", "Still here."])
    agent = Agent(llm)
    agent.chat("a", "hello")
    agent.chat("a", "again")
    assert [m["role"] for m in llm.seen[1]] == ["system", "user", "assistant", "user"]
    agent.chat("b", "new")
    assert [m["role"] for m in llm.seen[2]] == ["system", "user"]
