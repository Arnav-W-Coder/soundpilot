"""Faster turns: replies built from tool results, lean proposal previews, compact results, the opt-in fast path."""
from __future__ import annotations

import json

import pytest

from mixassist import agent as agent_mod
from mixassist.agent import Agent, compact_for_model, parse_fast_command

from test_agent import ScriptedLLM


@pytest.fixture(autouse=True)
def _clean():
    agent_mod.reset_proposals()
    yield
    agent_mod.reset_proposals()


def _count_llm(llm: ScriptedLLM) -> int:
    return len(llm.seen)


# ------------------------------------------------------- replies from tool results
def test_write_turn_skips_the_summary_call():
    llm = ScriptedLLM([[("fl_set_track", {"track": "kick", "volume_change_db": -2})], "never asked"])
    agent = Agent(llm)
    turn = agent.run_turn("s", "turn the kick down 2 dB")
    assert _count_llm(llm) == 1 and turn.reply_source == "tools"
    assert turn.reply == "Proposed for your approval: Kick: volume 0.0 dB -> -2.0 dB."
    assert len(turn.proposals) == 1 and turn.proposals[0]["status"] == "pending"
    assert agent.sessions["s"].messages[-1] == {"role": "assistant", "content": turn.reply}


def test_read_then_write_settles_but_a_question_gets_prose():
    llm = ScriptedLLM([[("fl_get_mix_overview", {})], [("fl_set_track", {"track": "Kick", "volume_change_db": -2})]])
    turn = Agent(llm).run_turn("s", "turn the kick down 2 dB")
    assert _count_llm(llm) == 2 and turn.reply_source == "tools"

    llm = ScriptedLLM([[("fl_get_mix_overview", {})], "You have 13 tracks; Bass sits at -3.0 dB."])
    turn = Agent(llm).run_turn("s", "what's on my mixer?")
    assert _count_llm(llm) == 2 and turn.reply_source == "model" and turn.reply.startswith("You have 13 tracks")


def test_multi_part_request_keeps_the_model_in_the_loop():
    llm = ScriptedLLM([[("fl_set_track", {"track": "Kick", "volume_change_db": -2})],
                       [("fl_set_track", {"track": "Snare", "pan_pct": -30})], "Both proposed."])
    turn = Agent(llm).run_turn("s", "turn the kick down 2 dB and pan the snare 30% left")
    assert _count_llm(llm) == 3 and len(turn.proposals) == 2 and turn.reply == "Both proposed."


def test_tool_error_goes_back_to_the_model():
    llm = ScriptedLLM([[("fl_set_track", {"track": "kick", "volume": "-2"})],
                       [("fl_set_track", {"track": "kick", "volume_change_db": -2})]])
    turn = Agent(llm).run_turn("s", "kick down 2")
    assert _count_llm(llm) == 2 and turn.reply_source == "tools" and len(turn.proposals) == 1


def test_clarification_and_ambiguity_settle_with_the_question(monkeypatch):
    import mixassist.tools as tools

    monkeypatch.setitem(tools.TOOLS, "diagnose_complaint", lambda a: {
        "needs_clarification": True, "question": "Is it the highs or the low mids?", "options": ["highs", "low mids"],
        "findings": [], "proposed_changes": []})
    llm = ScriptedLLM([[("diagnose_complaint", {"complaint": "sounds bad", "analysis_id": "abc"})], "unused"])
    turn = Agent(llm).run_turn("s", "it sounds bad")
    assert _count_llm(llm) == 1 and turn.needs_clarification and turn.proposals == []
    assert turn.reply == "Is it the highs or the low mids?" and turn.options == ["highs", "low mids"]

    llm = ScriptedLLM([[("fl_set_track", {"track": "hat", "pan_pct": -30})], "unused"])
    out = Agent(llm).chat("s2", "pan the hat left")
    assert _count_llm(llm) == 1 and out["needs_clarification"]
    assert out["reply"] == "Which track do you mean: Hat Open or Hat Closed?"
    assert set(out["options"]) == {"Hat Open", "Hat Closed"}


def test_diagnosis_reply_quotes_the_measurement(monkeypatch):
    import mixassist.tools as tools

    why = "The highs: +5.4 dB at 5-20 kHz vs the reference; cut the high EQ band on the master by 5.4 dB at 8 kHz."
    monkeypatch.setitem(tools.TOOLS, "diagnose_complaint", lambda a: {
        "needs_clarification": False, "findings": [{"descriptor": "bright", "delta": 5.4, "unit": "dB"}],
        "proposed_changes": [{"tool": "fl_set_track", "why": why,
                              "args": {"track": 0, "eq_band": "high", "eq_gain_db": -5.4, "eq_freq_hz": 8000}}]})
    llm = ScriptedLLM([[("diagnose_complaint", {"complaint": "too bright", "analysis_id": "abc"})]])
    turn = Agent(llm).run_turn("s", "my mix at /tmp/mix.wav is too bright")
    assert _count_llm(llm) == 1
    assert turn.reply.startswith(why + " Proposed for your approval: Master: high EQ gain 0.0 dB -> -5.4 dB")


def test_peak_and_playback_replies(fake_fl):
    from mixassist.fl.fake import VirtualClock

    fake_fl.clock = VirtualClock()
    llm = ScriptedLLM([[("fl_watch_peaks", {"seconds": 5})]])
    turn = Agent(llm).run_turn("s", "is anything clipping?")
    assert _count_llm(llm) == 1 and turn.proposals
    assert turn.reply.startswith("Lead Synth clipped") and "Proposed for your approval: Lead Synth: volume" in turn.reply

    notes = [{"pitch": p, "start_beat": i * 0.5, "length_beats": 0.45} for i, p in enumerate((64, 68, 71, 76))]
    llm = ScriptedLLM([[("fl_play_midi", {"notes": notes})]])
    turn = Agent(llm).run_turn("s2", "play an E major arpeggio")
    assert _count_llm(llm) == 1 and turn.reply_source == "tools"
    assert turn.reply.startswith("Played E4 G#4 B4 E5 live on the selected channel (4 notes")


def test_quiet_peaks_reply():
    from mixassist.agent import _peaks_reply

    result = {"polls": 100, "tracks": [{"name": "Master", "max_peak_dbfs": -1.94, "clip_count": 0},
                                       {"name": "Kick", "max_peak_dbfs": -6.0, "clip_count": 0}], "warnings": []}
    assert _peaks_reply(result) == "Nothing clipped over 100 meter polls; the loudest was Master at -1.94 dBFS."


def test_tool_replies_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("SOUNDPILOT_TOOL_REPLIES", "0")
    llm = ScriptedLLM([[("fl_set_track", {"track": "kick", "volume_change_db": -2})], "Proposed: Kick -2 dB."])
    turn = Agent(llm).run_turn("s", "turn the kick down 2 dB")
    assert _count_llm(llm) == 2 and turn.reply == "Proposed: Kick -2 dB." and turn.reply_source == "model"


# ----------------------------------------------------------- previews and results
def test_preview_reads_one_track_not_the_whole_mixer(fake_fl):
    from mixassist.fl import get_fl
    from mixassist.tools import call_tool

    transport = get_fl().transport
    sent = []
    real = transport.send_sysex
    transport.send_sysex = lambda frame: (sent.append(frame), real(frame))[1]
    proposal = agent_mod.create_proposal("fl_set_track", {"track": "Bass", "eq_band": "low", "eq_gain_db": -3}, "")
    assert len(sent) <= 20  # names/levels in batches of 8 + hello + one EQ read (the full overview needs ~270)
    bass = next(t for t in call_tool("fl_get_mix_overview", {})["tracks"] if t["name"] == "Bass")
    low = next(b for b in bass["eq"] if b["band"] == "low")
    assert proposal["preview"]["changes"] == [{"field": "eq_gain_db", "before": low["gain_db"], "after": -3.0, "unit": "dB"}]
    assert proposal["preview"]["track"] == {"index": bass["index"], "name": "Bass"}


def test_undo_preview_reads_the_changed_track():
    from mixassist.tools import call_tool

    call_tool("fl_set_track", {"track": "Snare", "volume_change_db": -3})
    preview = agent_mod.create_proposal("fl_undo", {}, "")["preview"]
    assert preview["changes"][0]["field"] == "volume_db"
    assert preview["changes"][0]["after"] == pytest.approx(-2.5, abs=0.1)
    assert preview["summary"].startswith("Undo change c_") and "on Snare" in preview["summary"]


def test_overview_is_compacted_for_the_model():
    from mixassist.tools import call_tool

    call_tool("fl_set_track", {"track": "Bass", "eq_band": "low", "eq_gain_db": -3})
    overview = call_tool("fl_get_mix_overview", {})
    full, lean = json.dumps(overview), compact_for_model(overview, "fl_get_mix_overview")
    assert len(lean) < 0.6 * len(full)
    tracks = {t["name"]: t for t in json.loads(lean)["tracks"]}
    kick, bass = tracks["Kick"], tracks["Bass"]
    assert kick["plugins"] == ["Fruity Parametric EQ 2", "Fruity Compressor"]
    assert kick["eq"] == "flat" and "muted" not in kick and "solo" not in kick
    assert {"index", "name", "volume_db", "pan_pct", "stereo_width_pct"} <= set(kick)
    assert [b["band"] for b in bass["eq"]] == ["low"] and bass["eq"][0]["gain_db"] == pytest.approx(-3, abs=0.25)
    assert "third_octave_db" not in compact_for_model({"third_octave_db": [1.0] * 30, "lufs": -14.0})


# ---------------------------------------------------------------------- fast path
def test_fast_command_parser():
    assert parse_fast_command("Turn the kick down 2 dB.") == {"track": "kick", "volume_change_db": -2.0}
    assert parse_fast_command("bring up the Lead Synth by 1.5dB") == {"track": "Lead Synth", "volume_change_db": 1.5}
    assert parse_fast_command("set the bass fader to exactly -6 dB") == {"track": "bass", "volume_db": -6.0}
    assert parse_fast_command("pan the snare 30% to the left") == {"track": "snare", "pan_pct": -30.0}
    assert parse_fast_command("pan Vocal 15% right") == {"track": "Vocal", "pan_pct": 15.0}
    for message in ("turn the kick down 2 dB and pan the snare left", "what's on my mixer?", "make the kick punchier",
                    "turn it down a bit"):
        assert parse_fast_command(message) is None


def test_fast_path_builds_the_same_proposal_without_the_model(monkeypatch):
    monkeypatch.setenv("SOUNDPILOT_FASTPATH", "1")
    llm = ScriptedLLM(["unused"])
    agent = Agent(llm)
    turn = agent.run_turn("s", "turn the kick down 2 dB")
    assert llm.seen == [] and turn.reply_source == "fastpath"
    (p,) = turn.proposals
    assert p["status"] == "pending" and p["args"] == {"track": "kick", "volume_change_db": -2.0}
    assert p["preview"]["summary"] == "Kick: volume 0.0 dB -> -2.0 dB"
    assert agent_mod.get_proposal(p["proposal_id"])["status"] == "pending"  # still needs approval
    assert [m["role"] for m in agent.sessions["s"].messages] == ["user", "assistant", "tool", "assistant"]


def test_fast_path_hands_ambiguous_tracks_to_the_model(monkeypatch):
    monkeypatch.setenv("SOUNDPILOT_FASTPATH", "1")
    llm = ScriptedLLM([[("fl_set_track", {"track": "Hat Open", "pan_pct": -30})]])
    agent = Agent(llm)
    turn = agent.run_turn("s", "pan the hat 30% left")
    assert len(llm.seen) == 1 and turn.reply_source == "tools"
    assert [m["role"] for m in llm.seen[0]] == ["system", "user"]


def test_fast_path_is_off_by_default():
    llm = ScriptedLLM([[("fl_set_track", {"track": "kick", "volume_change_db": -2})]])
    Agent(llm).run_turn("s", "turn the kick down 2 dB")
    assert len(llm.seen) == 1
