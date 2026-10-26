"""Agent-only read tools: offered to the LLM by agent.py, never listed by the MCP server (spec tools stay at 10).

Each tool auto-runs (no approval) and counts toward the agent's per-turn tool-call cap. Results are cached per turn.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

import jsonschema

from mixassist import progress
from mixassist.errors import ToolError, error_dict
from mixassist.logctx import clip, compact

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    schema: dict
    fn: Callable[[dict], dict]
    model_view: Callable[[str, dict], str]
    enabled: Callable[[], bool]
    prompt_rule: str = ""


def _web_tools() -> list[AgentTool]:
    from mixassist import web

    return [AgentTool(name, web.DESCRIPTIONS[name], web.SCHEMAS[name], web.TOOLS[name], web.model_view, web.enabled,
                      web.PROMPT_RULE if name == "web_search" else "")
            for name in ("web_search", "web_fetch")]


REGISTRY: dict[str, AgentTool] = {t.name: t for t in _web_tools()}


def active() -> list[AgentTool]:
    return [t for t in REGISTRY.values() if t.enabled()]


def names() -> set[str]:
    return {t.name for t in active()}


def declarations() -> list[dict]:
    """Function declarations in the provider-neutral {name, description, parameters} form."""
    return [{"name": t.name, "description": t.description, "parameters": t.schema} for t in active()]


def prompt_rules() -> str:
    rules = [t.prompt_rule for t in active() if t.prompt_rule]
    return "\n" + "\n".join(rules) if rules else ""


def _validate(tool: AgentTool, args) -> dict:
    if not isinstance(args, dict):
        raise ToolError("OUT_OF_RANGE", f"{tool.name} arguments must be a JSON object")
    props = tool.schema.get("properties", {})
    unknown = sorted(set(args) - set(props))
    if unknown:
        raise ToolError("OUT_OF_RANGE", f"{tool.name} has no argument(s) {', '.join(unknown)}",
                        hint=f"valid arguments: {', '.join(sorted(props))}")
    clean = {}
    for key, value in args.items():
        if props[key].get("type") == "integer" and isinstance(value, (str, float)):
            try:
                value = int(float(value))
            except ValueError:
                pass
        clean[key] = value
    err = jsonschema.exceptions.best_match(jsonschema.Draft202012Validator(tool.schema).iter_errors(clean))
    if err is not None:
        where = ".".join(str(p) for p in err.absolute_path) or "(arguments)"
        raise ToolError("OUT_OF_RANGE", f"{tool.name}: {where}: {err.message}")
    return clean


def _step_text(name: str, args: dict) -> str:
    if name == "web_search":
        return f"Searching the web: {clip(args.get('query', ''), 60)}"
    if name == "web_fetch":
        return f"Reading {urlsplit(str(args.get('url', ''))).hostname or 'a web page'}"
    return f"Running {name}"


def run(name: str, args, cache: dict | None = None) -> tuple[str, dict]:
    """Run one agent tool: (model_view JSON, full result). Never raises; failures are §3.1 error dicts."""
    tool = REGISTRY[name]
    log.info("tool %s start args=%s", name, compact(args if isinstance(args, dict) else {}))
    progress.emit("tool", _step_text(name, args if isinstance(args, dict) else {}), tool=name)
    t0 = time.perf_counter()
    try:
        clean = _validate(tool, args)
        key = (name, json.dumps(clean, sort_keys=True))
        if cache is not None and key in cache:
            log.info("tool %s: cached result for this turn", name)
            result = cache[key]
        else:
            result = tool.fn(clean)
            if cache is not None:
                cache[key] = result
    except ToolError as exc:
        result = exc.to_dict()
    except Exception as exc:  # noqa: BLE001 - agent tools must never break the turn
        log.exception("tool %s failed", name)
        result = error_dict("NOT_FOUND", f"{name} failed: {type(exc).__name__}: {exc}")
    failed = "error" in result and len(result) == 1
    what = (f"ERROR {result['error']['code']}: {clip(result['error']['message'], 160)}" if failed
            else f"{len(result.get('results', []))} result(s)" if name == "web_search"
            else f"{len(result.get('text', ''))} chars from {result.get('url')}")
    (log.warning if failed else log.info)("tool %s done in %.2f s: %s", name, time.perf_counter() - t0, what)
    return tool.model_view(name, result), result
