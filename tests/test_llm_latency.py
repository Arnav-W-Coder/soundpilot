"""LLM providers under rate limits: server retry delays, minimal thinking, output caps, the fallback chain."""
from __future__ import annotations

import json
import logging

import httpx
import pytest

from mixassist import progress
from mixassist.llm.base import LLMError, RateLimitedError
from mixassist.llm.fallback import FallbackProvider

from test_agent import ScriptedLLM


# --------------------------------------------------------------- provider retries
def _genai(kind="vertex", backoff=(1, 2, 4), max_wait_s=None):
    from mixassist.llm.genai_provider import GenAIProvider

    p = GenAIProvider.__new__(GenAIProvider)
    p.name, p.model, p.backoff, p.max_wait_s = kind, "gemma-test", backoff, max_wait_s
    return p


def _quota_error(delay="20.4s", message="quota"):
    from google.genai import errors

    details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}] if delay else []
    return errors.APIError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": message,
                                           "details": details}})


def test_server_retry_delay_sources():
    from mixassist.llm.genai_provider import server_retry_delay

    assert server_retry_delay(_quota_error("42s")) == 42.0
    assert server_retry_delay(_quota_error(None, "Quota exceeded. Please retry in 20.456525294s.")) == pytest.approx(20.4565, abs=1e-3)
    assert server_retry_delay(_quota_error(None, "Resource exhausted. Please try again later.")) is None


def test_genai_honors_the_server_delay_and_reports_progress(monkeypatch):
    from mixassist.llm import genai_provider

    slept, events = [], []
    monkeypatch.setattr(genai_provider.time, "sleep", slept.append)
    replies = [_quota_error("7s"), "ok"]

    def fn():
        item = replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    with progress.listen(events.append):
        assert _genai()._with_retries(fn) == "ok"
    assert slept == [7.0]
    assert events == [{"type": "retry", "text": "vertex rate limit (HTTP 429 RESOURCE_EXHAUSTED); retrying in 7 s",
                       "provider": "vertex", "wait_s": 7.0, "attempt": 1}]


def test_genai_gives_up_early_when_the_wait_exceeds_its_budget(monkeypatch):
    from mixassist.llm import genai_provider

    slept = []
    monkeypatch.setattr(genai_provider.time, "sleep", slept.append)

    def fn():
        raise _quota_error("42s")

    with pytest.raises(RateLimitedError) as info:
        _genai(max_wait_s=4)._with_retries(fn)
    assert slept == [] and info.value.retry_after == 42.0

    def blind():
        raise _quota_error(None, "Resource exhausted. Please try again later.")

    with pytest.raises(RateLimitedError):
        _genai(max_wait_s=4)._with_retries(blind)
    assert slept == [1, 2]  # 1 + 2 s fit the 4 s budget; the next 4 s wait would not


def test_genai_minimal_thinking_and_output_cap(monkeypatch):
    from google.genai import errors

    sent = []

    class Models:
        def generate_content(self, model, contents, config):
            sent.append(config)
            if config.thinking_config is not None and len(sent) == 1:
                raise errors.APIError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                                      "message": "Thinking level is not supported for this model."}})
            return type("R", (), {"candidates": []})()

    p = _genai()
    p.minimal_thinking = True
    p.client = type("C", (), {"models": Models()})()
    p.chat([{"role": "user", "content": "hi"}], None)
    assert sent[0].thinking_config.thinking_level.name == "MINIMAL" and sent[0].max_output_tokens == 2048
    assert sent[1].thinking_config is None and p.minimal_thinking is False
    p.chat([{"role": "user", "content": "hi"}], None)
    assert len(sent) == 3 and sent[2].thinking_config is None


def test_minimal_thinking_defaults_to_aistudio_only(monkeypatch):
    from mixassist.llm.genai_provider import GenAIProvider

    monkeypatch.delenv("SOUNDPILOT_THINKING", raising=False)
    assert GenAIProvider("aistudio", "gemma-test", api_key="k").minimal_thinking is True
    assert GenAIProvider("vertex", "gemma-test", project="p").minimal_thinking is False
    monkeypatch.setenv("SOUNDPILOT_THINKING", "minimal")
    assert GenAIProvider("vertex", "gemma-test", project="p").minimal_thinking is True
    monkeypatch.setenv("SOUNDPILOT_THINKING", "default")
    assert GenAIProvider("aistudio", "gemma-test", api_key="k").minimal_thinking is False


def test_ollama_honors_retry_after_and_disables_thinking(monkeypatch):
    from mixassist.llm import ollama_provider

    slept, bodies = [], []
    monkeypatch.setattr(ollama_provider.time, "sleep", slept.append)
    statuses = [429, 200]

    def handler(request):
        bodies.append(json.loads(request.content))
        code = statuses.pop(0)
        if code == 429:
            return httpx.Response(429, json={"error": "busy"}, headers={"Retry-After": "3"})
        return httpx.Response(200, json={"message": {"content": "hi"}})

    provider = ollama_provider.OllamaProvider("gemma4:31b-cloud", host="http://x")
    provider._client = httpx.Client(transport=httpx.MockTransport(handler))
    assert provider.chat([{"role": "user", "content": "x"}], None).text == "hi"
    assert slept == [3.0]
    assert bodies[0]["think"] is False and bodies[0]["options"]["num_predict"] == 2048


# ---------------------------------------------------------------------- fallback
class _Limited(ScriptedLLM):
    def __init__(self, name):
        super().__init__([])
        self.name, self.calls = name, 0

    def chat(self, messages, tools, temperature=0.0):
        self.calls += 1
        raise RateLimitedError(f"{self.name}: 429")


def test_fallback_switches_on_rate_limits_and_returns_after_cooldown(caplog):
    now = [0.0]
    primary, backup = _Limited("vertex"), ScriptedLLM(["one", "two", "three"])
    backup.name = "ollama"
    chain = FallbackProvider([primary, backup], cooldown_s=60, clock=lambda: now[0])
    events = []
    with caplog.at_level(logging.WARNING, logger="mixassist"), progress.listen(events.append):
        assert chain.chat([], None).text == "one"
    assert primary.calls == 1 and chain.name == "ollama"
    assert any("switching to ollama scripted-1" in r.getMessage() for r in caplog.records)
    assert events[0]["type"] == "fallback" and "switching to ollama" in events[0]["text"]
    now[0] = 30.0
    assert chain.chat([], None).text == "two" and primary.calls == 1  # still on the fallback
    now[0] = 61.0
    assert chain.chat([], None).text == "three" and primary.calls == 2  # cooldown over: primary tried first


def test_fallback_does_not_hide_other_errors():
    class Broken(ScriptedLLM):
        def chat(self, *a, **k):
            raise LLMError("400 bad request")

    backup = ScriptedLLM(["x"])
    with pytest.raises(LLMError, match="400"):
        FallbackProvider([Broken([]), backup]).chat([], None)
    assert backup.seen == []
    with pytest.raises(RateLimitedError):
        FallbackProvider([_Limited("a"), _Limited("b")]).chat([], None)


def test_make_provider_builds_the_chain_from_env(monkeypatch):
    from mixassist.llm import make_provider

    monkeypatch.setenv("SOUNDPILOT_LLM", "ollama")
    monkeypatch.setenv("SOUNDPILOT_LLM_FALLBACK", "aistudio, ollama, bogus")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("SOUNDPILOT_LLM_FALLBACK_AFTER_S", "5")
    chain = make_provider()
    assert isinstance(chain, FallbackProvider)
    assert [p.name for p in chain.providers] == ["ollama", "aistudio"]
    assert chain.providers[0].max_wait_s == 5.0 and chain.providers[1].max_wait_s is None
    assert not isinstance(make_provider("ollama"), FallbackProvider)  # evals pin one provider
    monkeypatch.delenv("SOUNDPILOT_LLM_FALLBACK")
    assert not isinstance(make_provider(), FallbackProvider)
