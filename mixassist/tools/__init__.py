"""Tool registry and the single dispatcher used by MCP (server.py) and the agent/API."""
from __future__ import annotations

import copy
import importlib
import logging
from typing import Callable

import jsonschema

from mixassist.errors import ToolError, error_dict
from mixassist.schemas import SCHEMAS, TOOL_NAMES

log = logging.getLogger(__name__)

_MODULES = {
    "fl_get_mix_overview": "mixassist.tools.fl_tools",
    "fl_set_track": "mixassist.tools.fl_tools",
    "fl_undo": "mixassist.tools.fl_tools",
    "fl_watch_peaks": "mixassist.tools.fl_tools",
    "analyze_audio": "mixassist.tools.audio_tools",
    "diagnose_complaint": "mixassist.tools.audio_tools",
    "verify_fix": "mixassist.tools.audio_tools",
    "analyze_midi": "mixassist.tools.midi_tools",
    "fix_midi": "mixassist.tools.midi_tools",
    "fl_play_midi": "mixassist.tools.midi_tools",
}

WRITE_TOOLS = frozenset({"fl_set_track", "fl_undo", "fix_midi"})
READ_TOOLS = frozenset(set(TOOL_NAMES) - WRITE_TOOLS)  # fl_play_midi is a read only when record is false

_RANGE_VALIDATORS = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "maxItems", "minItems", "minLength"}


def is_write(name: str, args: dict | None) -> bool:
    """True when the call changes FL or files and therefore needs user approval."""
    if name in WRITE_TOOLS:
        return True
    return name == "fl_play_midi" and _truthy((args or {}).get("record"))


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def _lazy(name: str) -> Callable[[dict], dict]:
    module_name = _MODULES[name]

    def call(args: dict) -> dict:
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise ToolError("UNSUPPORTED", f"{name} is not available yet ({module_name}: {exc})")
        fn = getattr(module, name, None)
        if fn is None:
            raise ToolError("UNSUPPORTED", f"{name} is not implemented in {module_name}")
        return fn(args)

    call.__name__ = name
    return call


TOOLS: dict[str, Callable[[dict], dict]] = {name: _lazy(name) for name in TOOL_NAMES}


# ---------------------------------------------------------------- coercion
def _coerce_scalar(value, prop: dict):
    """Coerce LLM-style strings ("-2", "true") to the type the property schema expects."""
    if not isinstance(value, str):
        if isinstance(value, float) and value.is_integer() and _accepts(prop, "integer") and not _accepts(prop, "number"):
            return int(value)
        return value
    text = value.strip()
    if _accepts(prop, "integer"):
        try:
            return int(text)
        except ValueError:
            try:
                f = float(text)
                if f.is_integer():
                    return int(f)
            except ValueError:
                pass
    if _accepts(prop, "number"):
        try:
            return float(text.replace("dB", "").replace("db", "").strip()) if text else value
        except ValueError:
            return value
    if _accepts(prop, "boolean") and text.lower() in ("true", "false"):
        return text.lower() == "true"
    if "enum" in prop and not any(isinstance(e, str) for e in prop["enum"]):
        try:
            f = float(text)
            return int(f) if f.is_integer() and int(f) in prop["enum"] else f
        except ValueError:
            return value
    return value


def _accepts(prop: dict, type_name: str) -> bool:
    if prop.get("type") == type_name:
        return True
    return any(alt.get("type") == type_name for alt in prop.get("oneOf", []))


def coerce_args(name: str, args: dict) -> dict:
    schema = SCHEMAS[name]
    out = {}
    for key, value in args.items():
        prop = schema.get("properties", {}).get(key)
        if prop is None:
            out[key] = value
            continue
        if prop.get("type") == "array" and isinstance(value, list):
            item_props = prop.get("items", {}).get("properties", {})
            items = []
            for item in value:
                if isinstance(item, dict):
                    item = {k: _coerce_scalar(v, item_props.get(k, {})) for k, v in item.items()}
                items.append(item)
            out[key] = items
        else:
            out[key] = _coerce_scalar(value, prop)
    return out


def apply_defaults(name: str, args: dict) -> dict:
    schema = SCHEMAS[name]
    out = dict(args)
    for key, prop in schema.get("properties", {}).items():
        if key not in out and "default" in prop:
            out[key] = copy.deepcopy(prop["default"])
        if prop.get("type") == "array" and isinstance(out.get(key), list):
            item_props = prop.get("items", {}).get("properties", {})
            filled = []
            for item in out[key]:
                if isinstance(item, dict):
                    item = dict(item)
                    for k, p in item_props.items():
                        if k not in item and "default" in p:
                            item[k] = copy.deepcopy(p["default"])
                filled.append(item)
            out[key] = filled
    return out


def validate_args(name: str, args: dict) -> dict:
    """Coerce, check unknown keys, validate against the §3.2 schema and fill defaults. Raises ToolError."""
    if name not in SCHEMAS:
        raise ToolError("NOT_FOUND", f"unknown tool {name!r}", candidates=list(TOOL_NAMES))
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolError("OUT_OF_RANGE", f"{name} arguments must be a JSON object")
    schema = SCHEMAS[name]
    args = coerce_args(name, args)
    allowed = set(schema.get("properties", {}))
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise ToolError(
            "OUT_OF_RANGE",
            f"{name} has no argument(s) {', '.join(unknown)}",
            hint=f"valid arguments: {', '.join(sorted(allowed)) or '(none)'}",
        )
    validator = jsonschema.Draft202012Validator(schema)
    errors = list(validator.iter_errors(args))
    if errors:
        err = jsonschema.exceptions.best_match(errors)
        where = ".".join(str(p) for p in err.absolute_path) or "(arguments)"
        if err.validator == "oneOf" and not err.absolute_path and name == "fl_play_midi":
            message = "fl_play_midi needs exactly one of path or notes"
        else:
            message = f"{name}: {where}: {err.message}"
        hint = None
        if err.validator in _RANGE_VALIDATORS:
            hint = f"allowed: {err.schema}" if isinstance(err.schema, dict) else f"limit {err.validator}={err.validator_value}"
        elif err.validator == "enum":
            hint = f"one of {err.validator_value}"
        elif err.validator == "required":
            hint = f"required arguments: {', '.join(schema.get('required', []))}; you gave: {', '.join(args) or 'none'}"
        raise ToolError("OUT_OF_RANGE", message, hint=hint)
    return apply_defaults(name, args)


def call_tool(name: str, args: dict | None) -> dict:
    """Validate and run one tool. Never raises: failures come back as the §3.1 error dict."""
    try:
        clean = validate_args(name, args or {})
        result = TOOLS[name](clean)
        if not isinstance(result, dict):
            raise ToolError("FL_ERROR", f"{name} returned {type(result).__name__}, not an object")
        return result
    except ToolError as exc:
        return exc.to_dict()
    except Exception as exc:  # noqa: BLE001 - the dispatcher must never raise
        code = "FILE_ERROR" if _is_file_error(exc) else "FL_ERROR"
        log.exception("tool %s failed", name)
        return error_dict(code, f"{name} failed: {type(exc).__name__}: {exc}")


def _is_file_error(exc: Exception) -> bool:
    if isinstance(exc, OSError):
        return True
    mod = type(exc).__module__ or ""
    return mod.startswith("soundfile") or type(exc).__name__ in ("LibsndfileError", "SoundFileError")


__all__ = ["TOOLS", "READ_TOOLS", "WRITE_TOOLS", "call_tool", "validate_args", "is_write", "coerce_args"]
