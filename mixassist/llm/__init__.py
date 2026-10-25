"""LLM provider selection: SOUNDPILOT_LLM = vertex (default) | aistudio | ollama; model from SOUNDPILOT_MODEL."""
from __future__ import annotations

import os

from mixassist.llm.base import LLMError, LLMProvider, LLMResponse, ToolCall, convert_schema, function_declarations

DEFAULT_MODELS = {
    "vertex": "gemma-4-26b-a4b-it-maas",
    "aistudio": "gemma-4-26b-a4b-it",
    "ollama": "gemma4:31b-cloud",
}


def provider_config() -> tuple[str, str]:
    kind = (os.environ.get("SOUNDPILOT_LLM") or "vertex").lower()
    if kind not in DEFAULT_MODELS:
        raise LLMError(f"SOUNDPILOT_LLM must be vertex, aistudio or ollama (got {kind!r})")
    return kind, os.environ.get("SOUNDPILOT_MODEL") or DEFAULT_MODELS[kind]


def make_provider(kind: str | None = None, model: str | None = None) -> LLMProvider:
    env_kind, env_model = provider_config()
    kind = (kind or env_kind).lower()
    model = model or (env_model if kind == env_kind else DEFAULT_MODELS[kind])
    if kind == "ollama":
        from mixassist.llm.ollama_provider import OllamaProvider

        return OllamaProvider(model)
    from mixassist.llm.genai_provider import GenAIProvider

    return GenAIProvider(kind, model)


__all__ = ["LLMError", "LLMProvider", "LLMResponse", "ToolCall", "convert_schema", "function_declarations",
           "make_provider", "provider_config", "DEFAULT_MODELS"]
