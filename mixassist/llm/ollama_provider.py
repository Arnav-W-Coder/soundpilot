"""Gemma through a local Ollama server (/api/chat with native tools)."""
from __future__ import annotations

import json
import logging
import os
import time

import httpx

from mixassist import progress
from mixassist.llm.base import (LLMError, LLMProvider, LLMResponse, RateLimitedError, ToolCall, max_output_tokens,
                                parse_retry_delay)

log = logging.getLogger(__name__)

BACKOFF_S = (1, 2, 4)
RETRY_CODES = {429, 500, 502, 503}
MAX_SERVER_DELAY_S = 60.0


class OllamaProvider(LLMProvider):
    def __init__(self, model: str, host: str | None = None, timeout: float = 300.0,
                 backoff: tuple[float, ...] = BACKOFF_S, max_wait_s: float | None = None):
        self.name = "ollama"
        self.model = model
        self.backoff = backoff
        self.max_wait_s = max_wait_s
        host = host or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434"
        if not host.startswith("http"):
            host = "http://" + host
        self.host = host.rstrip("/")
        self.timeout = timeout
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)  # keep-alive across the calls of a turn
        return self._client

    def _messages(self, messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            role = m["role"]
            if role == "tool":
                out.append({"role": "tool", "content": m["content"], "tool_name": m["name"]})
            elif role == "assistant":
                msg: dict = {"role": "assistant", "content": m.get("content") or ""}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [{"function": {"name": c["name"], "arguments": c.get("args") or {}}}
                                         for c in m["tool_calls"]]
                out.append(msg)
            else:
                out.append({"role": role, "content": m["content"]})
        return out

    def chat(self, messages: list[dict], tools: list[dict] | None, temperature: float = 0.0) -> LLMResponse:
        body: dict = {
            "model": self.model,
            "messages": self._messages(messages),
            "stream": False,
            "think": False,
            "options": {"temperature": temperature, "seed": 7, "num_predict": max_output_tokens()},
        }
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        data = None
        attempts = len(self.backoff) + 1
        waited = 0.0
        for attempt in range(1, attempts + 1):
            hint = None
            try:
                resp = self.client.post(f"{self.host}/api/chat", json=body)
                if resp.status_code in RETRY_CODES:
                    what = f"HTTP {resp.status_code}"
                    hint = parse_retry_delay(resp.headers.get("retry-after"))
                    message = f"ollama {self.model}: HTTP {resp.status_code}: {resp.text[:300]}"
                else:
                    if resp.status_code in (401, 403):
                        raise LLMError(f"ollama {self.model}: HTTP {resp.status_code}; cloud models need `ollama signin`")
                    if resp.status_code != 200:
                        raise LLMError(f"ollama {self.model}: HTTP {resp.status_code}: {resp.text[:300]}")
                    data = resp.json()
                    break
            except httpx.HTTPError as exc:
                what, message = type(exc).__name__, f"ollama at {self.host} unreachable: {exc}"
            delay = self.backoff[attempt - 1] if attempt < attempts else None
            if delay is not None and hint is not None:
                delay = min(max(hint, 0.5), MAX_SERVER_DELAY_S)
            if delay is None or (self.max_wait_s is not None and waited + delay > self.max_wait_s):
                log.error("ollama %s: giving up after %d attempts: %s", self.model, attempt, what)
                raise RateLimitedError(message, retry_after=hint)
            self._retry(attempt, attempts, what, delay)
            waited += delay
        msg = (data or {}).get("message") or {}
        calls = []
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            calls.append(ToolCall(fn.get("name", ""), args if isinstance(args, dict) else {}))
        return LLMResponse(text=(msg.get("content") or "").strip(), tool_calls=calls, raw=data)

    def _retry(self, attempt: int, attempts: int, what: str, delay: float) -> None:
        log.warning("ollama %s: %s on attempt %d/%d; retrying in %g s", self.model, what, attempt, attempts, delay)
        reason = "rate limit" if "429" in what else "server busy" if what.startswith("HTTP") else "network"
        progress.emit("retry", f"ollama {reason} ({what}); retrying in {delay:g} s", provider=self.name, wait_s=delay,
                      attempt=attempt)
        time.sleep(delay)

    def check_model(self) -> tuple[bool | None, str]:
        try:
            resp = self.client.get(f"{self.host}/api/tags", timeout=10)
            names = {m.get("name") for m in resp.json().get("models", [])}
        except (httpx.HTTPError, ValueError) as exc:
            return None, f"ollama at {self.host} unreachable: {exc}"
        if self.model in names or f"{self.model}:latest" in names:
            return True, f"ollama: model {self.model} is pulled"
        return False, (f"ollama: model {self.model!r} is not available (have: {', '.join(sorted(n for n in names if n))}). "
                       "Use the cloud model gemma4:31b-cloud (`ollama signin`, then `ollama pull gemma4:31b-cloud`); "
                       "on Vertex use gemma-4-26b-a4b-it-maas.")
