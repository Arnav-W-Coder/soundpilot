"""Live progress logs: chat turn, LLM calls, provider retries, tools, proposals and the per-turn tag."""
from __future__ import annotations

import io
import logging

import httpx
import pytest

from mixassist import agent as agent_mod
from mixassist import logctx
from mixassist.agent import Agent
from mixassist.llm.base import LLMError

from test_agent import ScriptedLLM


def _lines(caplog, prefix: str = "mixassist") -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name.startswith(prefix)]


@pytest.fixture(autouse=True)
def _clean():
    agent_mod.reset_proposals()
    yield
    agent_mod.reset_proposals()


def test_chat_turn_logs_start_llm_tools_proposal_and_end(fake_fl, caplog):
    llm = ScriptedLLM([[("fl_get_mix_overview", {})],
                       [("fl_set_track", {"track": "kick", "volume_change_db": -2})],
                       "Proposed: Kick down 2 dB."])
    with caplog.at_level(logging.INFO, logger="mixassist"):
        Agent(llm).chat("sess-1234567890", "turn the kick down 2 dB " + "x" * 200)
    lines = _lines(caplog)
    start = next(line for line in lines if line.startswith("chat start: "))
    assert "turn the kick down 2 dB" in start and len(start) < 120
    assert "llm #1 start: scripted scripted-1 (2 messages)" in lines
    assert any(line.startswith("llm #1 done in ") and "1 function call(s) ['fl_get_mix_overview']" in line for line in lines)
    assert any(line.startswith("llm #3 done in ") and line.endswith("text (25 chars)") for line in lines)
    assert any(line.startswith("tool fl_get_mix_overview start args={}") for line in lines)
    assert any(line.startswith("tool fl_get_mix_overview done in ") and '"tracks":"<13>"' in line for line in lines)
    assert any(line.startswith("proposal p_") and "created for fl_set_track: Kick: volume 0.0 dB -> -2.0 dB" in line
               for line in lines)
    end = next(line for line in lines if line.startswith("chat end in "))
    assert "3 LLM call(s), 2 tool call(s), 1 proposal(s), needs_clarification=False" in end


def test_lines_carry_session_tag_and_elapsed_time(fake_fl):
    stream = io.StringIO()
    handler = logctx.make_handler(stream)
    pkg = logging.getLogger("mixassist")
    old_level = pkg.level
    pkg.addHandler(handler)
    pkg.setLevel(logging.INFO)
    try:
        agent = Agent(ScriptedLLM([[("fl_get_mix_overview", {})], "13 tracks.", "Again."]))
        agent.chat("abcdefgh-ijkl", "what's on my mixer?")
        agent.chat("abcdefgh-ijkl", "and now?")
    finally:
        pkg.removeHandler(handler)
        pkg.setLevel(old_level)
    out = stream.getvalue().splitlines()
    assert any("[s=abcdefgh#1 +0.0s] agent: chat start: " in line for line in out)
    assert any("[s=abcdefgh#1 +" in line and "tools: tool fl_get_mix_overview done in" in line for line in out)
    assert any("[s=abcdefgh#2 +0.0s] agent: chat start: 'and now?'" in line for line in out)
    assert all(line.startswith("INFO:     ") for line in out)


def test_llm_failure_logs_and_reraises(caplog):
    class Failing(ScriptedLLM):
        def chat(self, messages, tools, temperature=0.0):
            raise LLMError("vertex m: 429 quota")

    with caplog.at_level(logging.INFO, logger="mixassist"), pytest.raises(LLMError):
        Agent(Failing([])).chat("s", "hi")
    lines = _lines(caplog)
    assert any(line.startswith("llm #1 failed after ") and "429 quota" in line for line in lines)
    assert any(line.startswith("chat failed after ") and "1 LLM call(s): LLMError" in line for line in lines)


def test_apply_and_discard_are_logged(fake_fl, caplog):
    p1 = agent_mod.create_proposal("fl_set_track", {"track": "Kick", "volume_change_db": -2}, "")
    p2 = agent_mod.create_proposal("fl_set_track", {"track": "Kick", "volume_change_db": 1}, "")
    with caplog.at_level(logging.INFO, logger="mixassist"):
        agent_mod.apply_proposal(p1["proposal_id"])
        agent_mod.discard_proposal(p2["proposal_id"])
    lines = _lines(caplog)
    applied = next(line for line in lines if line.startswith(f"proposal {p1['proposal_id']} applied: "))
    assert '"verified":true' in applied and '"change_id":' in applied
    assert f"proposal {p2['proposal_id']} discard: status discarded" in lines


def test_tool_summary_shortens_long_paths_and_reports_errors(caplog):
    from mixassist.tools import call_tool

    path = "/Users/someone/" + "deep/" * 30 + "mix.wav"
    with caplog.at_level(logging.INFO, logger="mixassist"):
        call_tool("analyze_audio", {"path": path})
    lines = _lines(caplog)
    start = next(line for line in lines if line.startswith("tool analyze_audio start"))
    assert path not in start and start.endswith('mix.wav"}')
    done = next(line for line in lines if line.startswith("tool analyze_audio done in "))
    assert ": ERROR FILE_ERROR: " in done
    assert any(r.levelno == logging.WARNING for r in caplog.records if r.getMessage() == done)


def test_configure_adds_one_stderr_handler_when_unconfigured(monkeypatch):
    pkg = logging.getLogger("mixassist")
    monkeypatch.setattr(logging.getLogger(), "handlers", [])
    monkeypatch.setattr(pkg, "handlers", [])
    monkeypatch.setattr(pkg, "level", logging.NOTSET)
    monkeypatch.setenv("SOUNDPILOT_LOG", "debug")
    logctx.configure()
    logctx.configure()
    assert len(pkg.handlers) == 1 and pkg.level == logging.DEBUG
    assert any(isinstance(f, logctx.ContextFilter) for f in pkg.handlers[0].filters)


# ------------------------------------------------------------------ providers

def _genai_provider(kind="vertex", backoff=(0, 0)):
    from mixassist.llm.genai_provider import GenAIProvider

    p = GenAIProvider.__new__(GenAIProvider)
    p.name, p.model, p.backoff, p.max_wait_s = kind, "gemma-test", backoff, None
    return p


def test_genai_retries_log_attempt_status_and_wait_then_give_up(caplog):
    from google.genai import errors

    calls = []

    def fn():
        calls.append(1)
        raise errors.APIError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota"}})

    with caplog.at_level(logging.INFO, logger="mixassist"), pytest.raises(LLMError):
        _genai_provider()._with_retries(fn)
    lines = _lines(caplog)
    assert len(calls) == 3
    assert "vertex gemma-test: HTTP 429 RESOURCE_EXHAUSTED on attempt 1/3; retrying in 0 s" in lines
    assert "vertex gemma-test: HTTP 429 RESOURCE_EXHAUSTED on attempt 2/3; retrying in 0 s" in lines
    assert "vertex gemma-test: giving up after 3 attempts: HTTP 429 RESOURCE_EXHAUSTED" in lines


def test_genai_retry_then_success(caplog):
    from google.genai import errors

    replies = [errors.APIError(503, {"error": {"code": 503, "status": "UNAVAILABLE"}}), "ok"]

    def fn():
        item = replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    with caplog.at_level(logging.INFO, logger="mixassist"):
        assert _genai_provider("aistudio")._with_retries(fn) == "ok"
    lines = _lines(caplog)
    assert lines == ["aistudio gemma-test: HTTP 503 UNAVAILABLE on attempt 1/3; retrying in 0 s"]


def test_vertex_uses_adc_and_hides_misleading_env_key_warning(monkeypatch, caplog):
    from mixassist.llm.genai_provider import GenAIProvider

    monkeypatch.setenv("GOOGLE_API_KEY", "stale-google-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    with caplog.at_level(logging.INFO):
        p = GenAIProvider("vertex", "gemma-4-26b-a4b-it-maas", project="proj-x", location="global")
    api = p.client._api_client
    assert api.vertexai is True and api.api_key is None
    assert (api.project, api.location) == ("proj-x", "global")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Both GOOGLE_API_KEY and GEMINI_API_KEY" not in text
    assert "vertex: ADC credentials, project proj-x, location global" in text
    assert "stale-google-key" not in text and "gemini-key" not in text


def test_aistudio_passes_gemini_key_explicitly(monkeypatch, caplog):
    from mixassist.llm.genai_provider import GenAIProvider

    monkeypatch.setenv("GOOGLE_API_KEY", "stale-google-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    with caplog.at_level(logging.INFO):
        p = GenAIProvider("aistudio", "gemma-4-26b-a4b-it")
    assert not p.client._api_client.vertexai and p.client._api_client.api_key == "gemini-key"
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Both GOOGLE_API_KEY and GEMINI_API_KEY" not in text
    assert "aistudio: API key GEMINI_API_KEY passed explicitly (GOOGLE_API_KEY in the environment is ignored)" in text
    assert "stale-google-key" not in text and "gemini-key" not in text


def test_ollama_retries_are_logged(monkeypatch, caplog):
    from mixassist.llm.ollama_provider import OllamaProvider

    statuses = [503, 429, 200]

    def handler(request):
        code = statuses.pop(0)
        body = {"message": {"content": "hi"}} if code == 200 else {"error": "busy"}
        return httpx.Response(code, json=body)

    provider = OllamaProvider("gemma4:31b-cloud", host="http://x", backoff=(0, 0, 0))
    provider._client = httpx.Client(transport=httpx.MockTransport(handler))
    with caplog.at_level(logging.INFO, logger="mixassist"):
        out = provider.chat([], None)
    assert out.text == "hi"
    assert _lines(caplog) == ["ollama gemma4:31b-cloud: HTTP 503 on attempt 1/4; retrying in 0 s",
                              "ollama gemma4:31b-cloud: HTTP 429 on attempt 2/4; retrying in 0 s"]


def test_ollama_gives_up_after_connection_errors(monkeypatch, caplog):
    from mixassist.llm.ollama_provider import OllamaProvider

    def handler(request):
        raise httpx.ConnectError("refused")

    provider = OllamaProvider("m", host="http://x", backoff=(0,))
    provider._client = httpx.Client(transport=httpx.MockTransport(handler))
    with caplog.at_level(logging.INFO, logger="mixassist"), pytest.raises(LLMError):
        provider.chat([], None)
    assert _lines(caplog) == ["ollama m: ConnectError on attempt 1/2; retrying in 0 s",
                              "ollama m: giving up after 2 attempts: ConnectError"]
