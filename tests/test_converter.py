"""Gemini schema converter on all 10 schemas, and the text tool-call fallback parser."""
import json

import jsonschema
import pytest

from mixassist.llm.base import convert_schema, function_declarations, parse_text_tool_calls
from mixassist.schemas import SCHEMAS, TOOL_NAMES
from mixassist.tools import validate_args

FORBIDDEN = {"oneOf", "anyOf", "allOf", "exclusiveMinimum", "exclusiveMaximum", "default", "pattern", "minLength"}


def _walk(schema):
    yield schema
    for v in schema.get("properties", {}).values():
        yield from _walk(v)
    if isinstance(schema.get("items"), dict):
        yield from _walk(schema["items"])


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_converted_schema_is_gemini_safe(name):
    conv = convert_schema(name)
    jsonschema.Draft202012Validator.check_schema(conv)
    assert conv["type"] == "object"
    assert set(conv["properties"]) == set(SCHEMAS[name].get("properties", {}))
    assert conv.get("required", []) == SCHEMAS[name].get("required", [])
    for node in _walk(conv):
        assert not (FORBIDDEN & set(node)), (name, node)
        assert "type" in node, (name, node)
        if "enum" in node:
            assert node["type"] == "string" and all(isinstance(v, str) for v in node["enum"])


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_original_schema_untouched(name):
    before = json.dumps(SCHEMAS[name], sort_keys=True)
    convert_schema(name)
    assert json.dumps(SCHEMAS[name], sort_keys=True) == before


def test_track_reference_becomes_string():
    for name in ("fl_set_track", "diagnose_complaint"):
        assert convert_schema(name)["properties"]["track"] == {"type": "string", "description": "track index or name"}
    assert validate_args("fl_set_track", {"track": "2", "pan_pct": 0})["track"] == 2
    assert validate_args("fl_set_track", {"track": "Kick", "pan_pct": "-30"})["pan_pct"] == -30.0


def test_play_midi_oneof_dropped_but_described_and_enforced():
    decl = next(d for d in function_declarations() if d["name"] == "fl_play_midi")
    assert "oneOf" not in decl["parameters"]
    assert "path or notes" in decl["description"]
    length = decl["parameters"]["properties"]["notes"]["items"]["properties"]["length_beats"]
    assert length["type"] == "number" and "greater than 0" in length["description"]
    from mixassist.tools import call_tool

    assert call_tool("fl_play_midi", {"path": "/a.mid", "notes": [{"pitch": 60, "start_beat": 0, "length_beats": 1}]})["error"]["code"] == "OUT_OF_RANGE"


def test_numeric_enum_becomes_number_with_description():
    grid = convert_schema("fix_midi")["properties"]["grid_beats"]
    assert grid["type"] == "number" and "0.125" in grid["description"]
    assert convert_schema("fl_set_track")["properties"]["eq_band"] == {"type": "string", "enum": ["low", "mid", "high"]}
    assert "default 30" in convert_schema("fl_watch_peaks")["properties"]["seconds"]["description"]


def test_ten_declarations():
    decls = function_declarations()
    assert [d["name"] for d in decls] == list(TOOL_NAMES)
    assert all(d["description"] for d in decls)


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"name": "fl_set_track", "arguments": {"track": "Kick", "volume_change_db": -2}}',
         [("fl_set_track", {"track": "Kick", "volume_change_db": -2})]),
        ('Sure!\n```json\n{"tool": "fl_get_mix_overview", "args": {}}\n```', [("fl_get_mix_overview", {})]),
        ('<tool_call>{"name": "fl_undo", "arguments": {}}</tool_call>', [("fl_undo", {})]),
        ("```tool_code\nfl_set_track(track='Kick', volume_change_db=-2)\n```",
         [("fl_set_track", {"track": "Kick", "volume_change_db": -2})]),
        ("I'll call fl_watch_peaks(seconds=10) now.", [("fl_watch_peaks", {"seconds": 10})]),
        ('{"name": "make_coffee", "arguments": {}}', []),
        ("The kick is at 0 dB.", []),
    ],
)
def test_text_tool_call_fallback(text, expected):
    assert [(c.name, c.args) for c in parse_text_tool_calls(text)] == expected
