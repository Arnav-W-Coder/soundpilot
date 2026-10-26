"""LLM provider selection: SOUNDPILOT_LLM = vertex (default) | aistudio | ollama; model from SOUNDPILOT_MODEL.

SOUNDPILOT_LLM_FALLBACK (e.g. "ollama" or "ollama,aistudio") names providers to switch to when the primary stays
rate limited: the primary then waits at most SOUNDPILOT_LLM_FALLBACK_AFTER_S (default 4 s) in backoff before the
turn moves on, and stays on the fallback for SOUNDPILOT_LLM_FALLBACK_COOLDOWN_S (default 120 s).
"""
from __future__ import annotations

import logging
import os

from mixassist.llm.base import (LLMError, LLMProvider, LLMResponse, RateLimitedError, ToolCall, convert_schema,
                                function_declarations)

log = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "vertex": "gemma-4-26b-a4b-it-maas",
    "aistudio": "gemma-4-26b-a4b-it",
    "ollama": "gemma4:31b-cloud",
}
DEFAULT_FALLBACK_AFTER_S = 4.0


def provider_config() -> tuple[str, str]:
    kind = (os.environ.get("SOUNDPILOT_LLM") or "vertex").lower()
    if kind not in DEFAULT_MODELS:
        raise LLMError(f"SOUNDPILOT_LLM must be vertex, aistudio or ollama (got {kind!r})")
    return kind, os.environ.get("SOUNDPILOT_MODEL") or DEFAULT_MODELS[kind]


def fallback_config() -> list[str]:
    """Fallback provider kinds from SOUNDPILOT_LLM_FALLBACK, in order, without the primary or unknown names."""
    primary = provider_config()[0]
    kinds = []
    for raw in (os.environ.get("SOUNDPILOT_LLM_FALLBACK") or "").split(","):
        kind = raw.strip().lower()
        if not kind or kind in ("none", "off"):
            continue
        if kind not in DEFAULT_MODELS:
            log.warning("SOUNDPILOT_LLM_FALLBACK: unknown provider %r ignored (use vertex, aistudio or ollama)", kind)
        elif kind != primary and kind not in kinds:
            kinds.append(kind)
    return kinds


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def _single(kind: str, model: str, max_wait_s: float | None = None) -> LLMProvider:
    if kind == "ollama":
        from mixassist.llm.ollama_provider import OllamaProvider

        return OllamaProvider(model, max_wait_s=max_wait_s)
    from mixassist.llm.genai_provider import GenAIProvider

    return GenAIProvider(kind, model, max_wait_s=max_wait_s)


def make_provider(kind: str | None = None, model: str | None = None) -> LLMProvider:
    """The configured provider. With no explicit ``kind``, SOUNDPILOT_LLM_FALLBACK wraps it in a fallback chain."""
    env_kind, env_model = provider_config()
    chain = fallback_config() if kind is None else []
    kind = (kind or env_kind).lower()
    model = model or (env_model if kind == env_kind else DEFAULT_MODELS[kind])
    if not chain:
        return _single(kind, model)
    from mixassist.llm.fallback import FallbackProvider

    providers = [_single(kind, model, max_wait_s=_env_float("SOUNDPILOT_LLM_FALLBACK_AFTER_S", DEFAULT_FALLBACK_AFTER_S))]
    for fb in chain:
        try:
            providers.append(_single(fb, DEFAULT_MODELS[fb]))
        except LLMError as exc:
            log.warning("fallback provider %s unavailable: %s", fb, exc)
    if len(providers) == 1:
        return providers[0]
    log.info("LLM: %s %s, falling back to %s on rate limits", kind, model,
             ", ".join(f"{p.name} {p.model}" for p in providers[1:]))
    return FallbackProvider(providers, cooldown_s=_env_float("SOUNDPILOT_LLM_FALLBACK_COOLDOWN_S", 120.0))


__all__ = ["LLMError", "LLMProvider", "LLMResponse", "RateLimitedError", "ToolCall", "convert_schema",
           "function_declarations", "make_provider", "provider_config", "fallback_config", "DEFAULT_MODELS"]
