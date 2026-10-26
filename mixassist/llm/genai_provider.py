"""Gemma through the google-genai SDK: Vertex AI (ADC) or AI Studio (API key)."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from contextlib import contextmanager

from mixassist import progress
from mixassist.llm.base import (LLMError, LLMProvider, LLMResponse, RateLimitedError, ToolCall, max_output_tokens,
                                parse_retry_delay)

log = logging.getLogger(__name__)

RETRY_CODES = {429, 500, 503}
BACKOFF_S = (1, 2, 4, 8, 16)
MAX_SERVER_DELAY_S = 60.0
_RETRY_IN = re.compile(r"retry in (\d+(?:\.\d+)?)\s*s", re.I)


def server_retry_delay(exc) -> float | None:
    """The wait the server asked for: RetryInfo.retryDelay, a Retry-After header, or "Please retry in 20.4s"."""
    details = getattr(exc, "details", None)
    error = details.get("error") if isinstance(details, dict) else None
    for item in (error or {}).get("details") or []:
        if isinstance(item, dict) and str(item.get("@type", "")).endswith("RetryInfo"):
            delay = parse_retry_delay(item.get("retryDelay"))
            if delay is not None:
                return delay
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is not None:
        try:
            delay = parse_retry_delay(headers.get("retry-after"))
        except Exception:  # noqa: BLE001 - unusual header containers
            delay = None
        if delay is not None:
            return delay
    m = _RETRY_IN.search(str((error or {}).get("message") or getattr(exc, "message", "") or ""))
    return float(m.group(1)) if m else None


def gcloud_project() -> str | None:
    try:
        out = subprocess.run(["gcloud", "config", "get-value", "project"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.strip()
    return value if value and value != "(unset)" else None


class GenAIProvider(LLMProvider):
    def __init__(self, kind: str, model: str, project: str | None = None, location: str | None = None,
                 api_key: str | None = None, backoff: tuple[float, ...] = BACKOFF_S, max_wait_s: float | None = None):
        from google import genai

        self.name = kind
        self.model = model
        self.backoff = backoff
        self.max_wait_s = max_wait_s  # give up (RateLimitedError) instead of waiting longer than this in total
        # Gemma 4 on AI Studio thinks 50-450 tokens per call unless asked not to; Vertex MaaS reports no thinking.
        thinking = (os.environ.get("SOUNDPILOT_THINKING") or ("minimal" if kind == "aistudio" else "default")).lower()
        self.minimal_thinking = thinking == "minimal"
        if kind == "vertex":
            self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT") or gcloud_project()
            self.location = location or os.environ.get("GOOGLE_CLOUD_LOCATION") or "global"
            if not self.project:
                raise LLMError("Vertex needs a project: set GOOGLE_CLOUD_PROJECT or run `gcloud config set project ...`")
            with _quiet_env_key_warning():
                self.client = genai.Client(vertexai=True, project=self.project, location=self.location)
            if _client_api_key(self.client):
                log.warning("vertex: the client picked up an API key from the environment instead of ADC; "
                            "unset GOOGLE_API_KEY")
            else:
                log.info("vertex: ADC credentials, project %s, location %s (API keys in the environment are not used)",
                         self.project, self.location)
        elif kind == "aistudio":
            key = api_key or os.environ.get("GEMINI_API_KEY")
            if not key:
                raise LLMError("AI Studio needs GEMINI_API_KEY")
            self.project, self.location = None, None
            with _quiet_env_key_warning():
                self.client = genai.Client(api_key=key)
            ignored = " (GOOGLE_API_KEY in the environment is ignored)" if os.environ.get("GOOGLE_API_KEY") else ""
            log.info("aistudio: API key %s passed explicitly%s", "argument" if api_key else "GEMINI_API_KEY", ignored)
        else:
            raise ValueError(f"unknown genai provider {kind!r}")

    # -------------------------------------------------------------- calls
    def _with_retries(self, fn):
        from google.genai import errors

        attempts = len(self.backoff) + 1
        waited = 0.0
        for attempt in range(1, attempts + 1):
            try:
                return fn()
            except errors.APIError as exc:
                code = getattr(exc, "code", None)
                what = "HTTP " + " ".join(str(x) for x in (code, getattr(exc, "status", None)) if x)
                if code not in RETRY_CODES:
                    raise LLMError(f"{self.name} {self.model}: {code} {getattr(exc, 'message', exc)}") from exc
                hint = server_retry_delay(exc)
                message, last = f"{self.name} {self.model}: {code} {getattr(exc, 'message', exc)}", exc
            except Exception as exc:  # network errors etc.
                if not _transient(exc):
                    raise LLMError(f"{self.name} {self.model}: {type(exc).__name__}: {exc}") from exc
                what, hint = type(exc).__name__, None
                message, last = f"{self.name} {self.model}: {type(exc).__name__}: {exc}", exc
            delay = self.backoff[attempt - 1] if attempt < attempts else None
            if delay is not None and hint is not None:
                delay = min(max(hint, 0.5), MAX_SERVER_DELAY_S)
            if delay is None or (self.max_wait_s is not None and waited + delay > self.max_wait_s):
                log.error("%s %s: giving up after %d attempts: %s", self.name, self.model, attempt, what)
                raise RateLimitedError(message, retry_after=hint) from last
            self._backoff(attempt, attempts, what, delay, hint is not None)
            waited += delay

    def _backoff(self, attempt: int, attempts: int, what: str, delay: float, from_server: bool = False) -> None:
        source = " (server's retry delay)" if from_server else ""
        log.warning("%s %s: %s on attempt %d/%d; retrying in %g s%s", self.name, self.model, what,
                    attempt, attempts, delay, source)
        reason = "rate limit" if "429" in what else "server busy" if what.startswith("HTTP") else "network"
        progress.emit("retry", f"{self.name} {reason} ({what}); retrying in {delay:g} s", provider=self.name,
                      wait_s=delay, attempt=attempt)
        time.sleep(delay)

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

    def _tools(self, tools: list[dict]):
        """Function declarations, built once per tool list (the agent passes the same list every call)."""
        from google.genai import types

        cached = getattr(self, "_tools_cache", None)
        if cached is not None and cached[0] is tools:
            return cached[1]
        built = [types.Tool(function_declarations=[
            types.FunctionDeclaration(name=t["name"], description=t["description"], parameters_json_schema=t["parameters"])
            for t in tools
        ])]
        self._tools_cache = (tools, built)
        return built

    def chat(self, messages: list[dict], tools: list[dict] | None, temperature: float = 0.0) -> LLMResponse:
        from google.genai import types

        config_kwargs: dict = {"temperature": temperature, "max_output_tokens": max_output_tokens()}
        if tools:
            config_kwargs["tools"] = self._tools(tools)
            config_kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
        contents = self._contents(messages)

        def generate():
            kwargs = dict(config_kwargs)
            if self.minimal_thinking:
                kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="MINIMAL")
            return self.client.models.generate_content(model=self.model, contents=contents,
                                                       config=types.GenerateContentConfig(**kwargs))

        try:
            response = self._with_retries(generate)
        except LLMError as exc:
            if not (self.minimal_thinking and "thinking" in str(exc).lower() and getattr(exc.__cause__, "code", 0) == 400):
                raise
            log.info("%s %s rejects thinking_level=MINIMAL; using the model's default thinking", self.name, self.model)
            self.minimal_thinking = False
            response = self._with_retries(generate)
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
            "(location global), gemma-4-26b-a4b-it or gemma-4-31b-it on AI Studio, gemma4:31b-cloud on Ollama.")


class _DropEnvKeyWarning(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not str(record.msg).startswith("Both GOOGLE_API_KEY and GEMINI_API_KEY are set")


@contextmanager
def _quiet_env_key_warning():
    """genai.Client() warns "Using GOOGLE_API_KEY" whenever both keys are set, even when it then uses ADC or the
    explicit key; the provider logs the credentials it really uses instead."""
    sdk_log = logging.getLogger("google_genai._api_client")
    flt = _DropEnvKeyWarning()
    sdk_log.addFilter(flt)
    try:
        yield
    finally:
        sdk_log.removeFilter(flt)


def _client_api_key(client) -> str | None:
    return getattr(getattr(client, "_api_client", None), "api_key", None)


def _transient(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return any(s in name for s in ("timeout", "connect", "remoteprotocol"))
