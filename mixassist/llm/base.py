"""Provider-neutral chat types, the Gemini-safe schema converter and text tool-call parsing."""
from __future__ import annotations

import ast
import copy
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from mixassist.schemas import DESCRIPTIONS, SCHEMAS, TOOL_NAMES

# Messages are plain dicts:
#   {"role": "system"|"user", "content": str}
#   {"role": "assistant", "content": str, "tool_calls": [{"name": str, "args": dict}]}
#   {"role": "tool", "name": str, "content": str (JSON)}


@dataclass
class ToolCall:
    name: str
    args: dict = field(default_factory=dict)


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: object = None


class LLMError(Exception):
    """The provider failed after retries (network, auth, quota, unknown model)."""


class LLMProvider(ABC):
    name: str = "base"
    model: str = ""

    @abstractmethod
    def chat(self, messages: list[dict], tools: list[dict] | None, temperature: float = 0.0) -> LLMResponse:
        """One model turn. ``tools`` are ``{"name", "description", "parameters"}`` dicts (converted schemas)."""

    @abstractmethod
    def check_model(self) -> tuple[bool | None, str]:
        """Whether the configured model ID exists (None if unknown) and a log message."""


# ---------------------------------------------------------- schema conversion
_KEEP = {"type", "properties", "required", "items", "enum", "minimum", "maximum", "description", "maxItems"}


def _is_track_ref(prop: dict) -> bool:
    alts = prop.get("oneOf") or []
    types_ = {a.get("type") for a in alts}
    return types_ == {"integer", "string"}


def _describe(extra: list[str], base: str | None) -> str | None:
    parts = [p for p in [base, *extra] if p]
    return "; ".join(parts) if parts else None


def convert_property(prop: dict) -> dict:
    """Make one property schema Gemini-safe: no oneOf, no exclusiveMinimum, string-only enums, no defaults."""
    if _is_track_ref(prop):
        return {"type": "string", "description": "track index or name"}
    prop = copy.deepcopy(prop)
    notes: list[str] = []
    out: dict = {}
    if "enum" in prop:
        values = prop["enum"]
        if all(isinstance(v, str) for v in values):
            out["type"] = "string"
            out["enum"] = values
        else:
            out["type"] = "number"
            notes.append("one of " + ", ".join(str(v) for v in values))
    if "exclusiveMinimum" in prop:
        notes.append(f"must be greater than {prop['exclusiveMinimum']}")
    if "exclusiveMaximum" in prop:
        notes.append(f"must be less than {prop['exclusiveMaximum']}")
    if "pattern" in prop:
        notes.append(f"format {prop['pattern']}")
    if "default" in prop:
        notes.append(f"default {json.dumps(prop['default'])}")
    if "minLength" in prop:
        notes.append("non-empty")
    for key in ("type", "minimum", "maximum", "maxItems"):
        if key in prop and key not in out:
            out[key] = prop[key]
    if prop.get("type") == "object":
        out["properties"] = {k: convert_property(v) for k, v in prop.get("properties", {}).items()}
        if prop.get("required"):
            out["required"] = list(prop["required"])
    if prop.get("type") == "array" and "items" in prop:
        out["items"] = convert_property(prop["items"])
    desc = _describe(notes, prop.get("description"))
    if desc:
        out["description"] = desc
    assert set(out) <= _KEEP
    return out


def convert_schema(name: str, schema: dict | None = None) -> dict:
    """Convert a §3.2 input schema into a Gemini function-declaration parameter schema."""
    schema = schema if schema is not None else SCHEMAS[name]
    out = {"type": "object", "properties": {k: convert_property(v) for k, v in schema.get("properties", {}).items()}}
    if schema.get("required"):
        out["required"] = list(schema["required"])
    return out


def function_declarations(names: tuple[str, ...] = TOOL_NAMES) -> list[dict]:
    decls = []
    for name in names:
        description = DESCRIPTIONS[name]
        if "oneOf" in SCHEMAS[name]:
            description += " Exactly one of: " + " or ".join(
                "/".join(alt.get("required", [])) for alt in SCHEMAS[name]["oneOf"]) + "."
        decls.append({"name": name, "description": description, "parameters": convert_schema(name)})
    return decls


# ------------------------------------------------------ text tool-call fallback
_JSON_BLOCK = re.compile(r"```(?:json|tool_code|tool_call|python)?\s*(.*?)```", re.S)
_TAGGED = re.compile(r"<(?:tool_call|function_call)>\s*(.*?)\s*</(?:tool_call|function_call)>", re.S)


def _from_obj(obj, known: set[str]) -> list[ToolCall]:
    if isinstance(obj, list):
        out = []
        for item in obj:
            out += _from_obj(item, known)
        return out
    if not isinstance(obj, dict):
        return []
    if "function" in obj and isinstance(obj["function"], dict):
        obj = obj["function"]
    name = obj.get("name") or obj.get("tool") or obj.get("tool_name")
    args = obj.get("arguments", obj.get("args", obj.get("parameters", {})))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    if isinstance(name, str) and name in known and isinstance(args, dict):
        return [ToolCall(name, args)]
    return []


def _from_python_call(text: str, known: set[str]) -> list[ToolCall]:
    out = []
    for m in re.finditer(r"\b(" + "|".join(map(re.escape, sorted(known))) + r")\s*\(", text):
        start = m.start()
        depth = 0
        for i in range(m.end() - 1, len(text)):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    snippet = text[start : i + 1]
                    try:
                        call = ast.parse(snippet, mode="eval").body
                        if isinstance(call, ast.Call) and not call.args:
                            args = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords if kw.arg}
                            out.append(ToolCall(m.group(1), args))
                    except (SyntaxError, ValueError):
                        pass
                    break
    return out


def parse_text_tool_calls(text: str, known: set[str] | None = None) -> list[ToolCall]:
    """Find tool calls a model wrote as text (JSON, <tool_call> tags, or name(k=v) code)."""
    if not text:
        return []
    known = set(known or TOOL_NAMES)
    candidates = _TAGGED.findall(text) + _JSON_BLOCK.findall(text)
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        candidates.append(stripped)
    for chunk in candidates:
        try:
            calls = _from_obj(json.loads(chunk), known)
        except ValueError:
            calls = _from_python_call(chunk, known)
        if calls:
            return calls
    return _from_python_call(text, known) if "(" in text else []
