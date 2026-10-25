"""Gemma through a local Ollama server (/api/chat with native tools)."""
from __future__ import annotations

import json
import logging
import os
import time

import httpx

from mixassist.llm.base import LLMError, LLMProvider, LLMResponse, ToolCall

log = logging.getLogger(__name__)

BACKOFF_S = (1, 2, 4)


class OllamaProvider(LLMProvider):
    def __init__(self, model: str, host: str | None = None, timeout: float = 300.0):
        self.name = "ollama"
        self.model = model
        host = host or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434"
        if not host.startswith("http"):
            host = "http://" + host
        self.host = host.rstrip("/")
        self.timeout = timeout

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
            "options": {"temperature": temperature, "seed": 7},
        }
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        data = None
        for attempt in range(len(BACKOFF_S) + 1):
            try:
                resp = httpx.post(f"{self.host}/api/chat", json=body, timeout=self.timeout)
                if resp.status_code in (429, 500, 502, 503) and attempt < len(BACKOFF_S):
                    log.warning("ollama %s returned %s; retrying", self.model, resp.status_code)
                    time.sleep(BACKOFF_S[attempt])
                    continue
                if resp.status_code != 200:
                    raise LLMError(f"ollama {self.model}: HTTP {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
                break
            except httpx.HTTPError as exc:
                if attempt < len(BACKOFF_S):
                    time.sleep(BACKOFF_S[attempt])
                    continue
                raise LLMError(f"ollama at {self.host} unreachable: {exc}") from exc
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

    def check_model(self) -> tuple[bool | None, str]:
        try:
            resp = httpx.get(f"{self.host}/api/tags", timeout=10)
            names = {m.get("name") for m in resp.json().get("models", [])}
        except (httpx.HTTPError, ValueError) as exc:
            return None, f"ollama at {self.host} unreachable: {exc}"
        if self.model in names or f"{self.model}:latest" in names:
            return True, f"ollama: model {self.model} is pulled"
        return False, (f"ollama: model {self.model!r} is not pulled (have: {', '.join(sorted(n for n in names if n))}). "
                       "Documented IDs: gemma4:31b-cloud on Ollama, gemma-4-26b-a4b-it-maas on Vertex.")
