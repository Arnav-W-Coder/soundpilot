"""Gemma through the google-genai SDK: Vertex AI (ADC) or AI Studio (API key)."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time

from mixassist.llm.base import LLMError, LLMProvider, LLMResponse, ToolCall

log = logging.getLogger(__name__)

RETRY_CODES = {429, 500, 503}
BACKOFF_S = (1, 2, 4, 8, 16)


def gcloud_project() -> str | None:
    try:
        out = subprocess.run(["gcloud", "config", "get-value", "project"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.strip()
    return value if value and value != "(unset)" else None


class GenAIProvider(LLMProvider):
    def __init__(self, kind: str, model: str, project: str | None = None, location: str | None = None,
                 api_key: str | None = None, backoff: tuple[float, ...] = BACKOFF_S):
        from google import genai

        self.name = kind
        self.model = model
        self.backoff = backoff
        if kind == "vertex":
            self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT") or gcloud_project()
            self.location = location or os.environ.get("GOOGLE_CLOUD_LOCATION") or "global"
            if not self.project:
                raise LLMError("Vertex needs a project: set GOOGLE_CLOUD_PROJECT or run `gcloud config set project ...`")
            self.client = genai.Client(vertexai=True, project=self.project, location=self.location)
        elif kind == "aistudio":
            key = api_key or os.environ.get("GEMINI_API_KEY")
            if not key:
                raise LLMError("AI Studio needs GEMINI_API_KEY")
            self.project, self.location = None, None
            self.client = genai.Client(api_key=key)
        else:
            raise ValueError(f"unknown genai provider {kind!r}")

    # -------------------------------------------------------------- calls
    def _with_retries(self, fn):
        from google.genai import errors

        attempts = len(self.backoff) + 1
        for attempt in range(attempts):
            try:
                return fn()
            except errors.APIError as exc:
                code = getattr(exc, "code", None)
                if code in RETRY_CODES and attempt < attempts - 1:
                    delay = self.backoff[attempt]
                    log.warning("%s %s returned %s; retry %d/%d in %.0f s", self.name, self.model, code,
                                attempt + 1, attempts - 1, delay)
                    time.sleep(delay)
                    continue
                raise LLMError(f"{self.name} {self.model}: {code} {getattr(exc, 'message', exc)}") from exc
            except Exception as exc:  # network errors etc.
                if attempt < attempts - 1 and _transient(exc):
                    time.sleep(self.backoff[attempt])
                    continue
                raise LLMError(f"{self.name} {self.model}: {type(exc).__name__}: {exc}") from exc

    def _contents(self, messages: list[dict]):
        from google.genai import types

        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        contents: list = []
        pending_responses: list = []

        def flush():
            if pending_responses:
                contents.append(types.Content(role="user", parts=list(pending_responses)))
                pending_responses.clear()

        first_user = True
        for m in messages:
            role = m["role"]
            if role == "system":
                continue
            if role == "tool":
                try:
                    payload = json.loads(m["content"])
                except ValueError:
                    payload = {"text": m["content"]}
                if not isinstance(payload, dict):
                    payload = {"result": payload}
                pending_responses.append(types.Part.from_function_response(name=m["name"], response=payload))
                continue
            flush()
            if role == "user":
                text = m["content"]
                if first_user and system:
                    # Gemma has no system role; the instructions ride on the first user turn.
                    text = f"{system}\n\n---\n\nUser: {text}"
                    first_user = False
                contents.append(types.Content(role="user", parts=[types.Part.from_text(text=text)]))
            elif role == "assistant":
                parts = []
                if m.get("content"):
                    parts.append(types.Part.from_text(text=m["content"]))
                for call in m.get("tool_calls") or []:
                    parts.append(types.Part.from_function_call(name=call["name"], args=call.get("args") or {}))
                if parts:
                    contents.append(types.Content(role="model", parts=parts))
        flush()
        return contents

    def chat(self, messages: list[dict], tools: list[dict] | None, temperature: float = 0.0) -> LLMResponse:
        from google.genai import types

        config_kwargs: dict = {"temperature": temperature}
        if tools:
            config_kwargs["tools"] = [types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t["name"], description=t["description"],
                                          parameters_json_schema=t["parameters"])
                for t in tools
            ])]
            config_kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
        config = types.GenerateContentConfig(**config_kwargs)
        contents = self._contents(messages)
        response = self._with_retries(
            lambda: self.client.models.generate_content(model=self.model, contents=contents, config=config))
        texts, calls = [], []
        for cand in (response.candidates or [])[:1]:
            for part in (cand.content.parts if cand.content and cand.content.parts else []):
                if getattr(part, "thought", False):
                    continue
                if part.function_call is not None:
                    calls.append(ToolCall(part.function_call.name, dict(part.function_call.args or {})))
                elif part.text:
                    texts.append(part.text)
        return LLMResponse(text="".join(texts).strip(), tool_calls=calls, raw=response)

    def check_model(self) -> tuple[bool | None, str]:
        wanted = self.model
        try:
            for m in self.client.models.list():
                name = (m.name or "").split("/")[-1]
                if name == wanted:
                    return True, f"{self.name}: model {wanted} is listed"
        except Exception as exc:  # noqa: BLE001
            log.info("%s model list failed (%s); probing instead", self.name, exc)
        # Gemma MaaS models are not listed and 404 on count_tokens, so probe with a 1-token generate.
        try:
            self._with_retries(lambda: self.client.models.generate_content(
                model=wanted, contents="Reply with OK.", config={"max_output_tokens": 1, "temperature": 0}))
            return True, f"{self.name}: model {wanted} answered a 1-token probe"
        except LLMError as exc:
            cause = exc.__cause__
            code = getattr(cause, "code", None)
            if code in (400, 403, 404):
                return False, _unavailable(self.name, wanted, exc)
            return None, f"{self.name}: could not confirm model {wanted}: {exc}"


def _unavailable(kind: str, model: str, exc) -> str:
    return (f"{kind}: model {model!r} is not available ({exc}). Documented IDs: gemma-4-26b-a4b-it-maas on Vertex "
            "(location global), gemma-4-26b-a4b-it on AI Studio, gemma4:31b-cloud on Ollama.")


def _transient(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return any(s in name for s in ("timeout", "connect", "remoteprotocol"))
