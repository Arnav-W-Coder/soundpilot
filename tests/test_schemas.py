"""§3.2 schemas, the registry and the dispatcher."""
import jsonschema
import pytest

from mixassist.errors import ERROR_CODES, is_error
from mixassist.schemas import DESCRIPTIONS, SCHEMAS, T
from mixassist.tools import READ_TOOLS, TOOLS, WRITE_TOOLS, call_tool, is_write, validate_args

SPEC_TOOLS = [
    "fl_get_mix_overview", "fl_set_track", "fl_undo", "fl_watch_peaks", "analyze_audio",
    "diagnose_complaint", "verify_fix", "analyze_midi", "fix_midi", "fl_play_midi",
]


def test_ten_tools_registered():
    assert list(SCHEMAS) == SPEC_TOOLS
    assert set(TOOLS) == set(SPEC_TOOLS) == set(DESCRIPTIONS)
    assert WRITE_TOOLS == {"fl_set_track", "fl_undo", "fix_midi"}
    assert READ_TOOLS | WRITE_TOOLS == set(SPEC_TOOLS)


@pytest.mark.parametrize("name", SPEC_TOOLS)
def test_schema_is_valid_json_schema(name):
    jsonschema.Draft202012Validator.check_schema(SCHEMAS[name])
    assert SCHEMAS[name]["type"] == "object"
    assert len(DESCRIPTIONS[name]) > 40


def test_track_reference_shape():
    assert T == {"oneOf": [{"type": "integer", "minimum": 0}, {"type": "string", "minLength": 1}]}
    assert SCHEMAS["fl_set_track"]["properties"]["track"] == T
    assert SCHEMAS["diagnose_complaint"]["properties"]["track"] == T


def test_spec_bounds_verbatim():
    p = SCHEMAS["fl_set_track"]["properties"]
    assert (p["volume_db"]["minimum"], p["volume_db"]["maximum"]) == (-60, 5)
    assert (p["volume_change_db"]["minimum"], p["volume_change_db"]["maximum"]) == (-24, 12)
    assert p["eq_band"]["enum"] == ["low", "mid", "high"]
    assert SCHEMAS["fl_play_midi"]["oneOf"] == [{"required": ["path"]}, {"required": ["notes"]}]
    assert SCHEMAS["fl_play_midi"]["properties"]["notes"]["items"]["properties"]["length_beats"] == {
        "type": "number", "exclusiveMinimum": 0}
    assert SCHEMAS["fix_midi"]["properties"]["grid_beats"]["enum"] == [0.125, 0.25, 0.5, 1]


def test_is_write():
    assert is_write("fl_set_track", {}) and is_write("fix_midi", {}) and is_write("fl_undo", {})
    assert is_write("fl_play_midi", {"record": True}) and not is_write("fl_play_midi", {"record": False})
    assert not is_write("analyze_audio", {})


def test_coercion_and_defaults():
    args = validate_args("fl_set_track", {"track": "3", "volume_change_db": "-2"})
    assert args == {"track": 3, "volume_change_db": -2.0}
    assert validate_args("fl_watch_peaks", {}) == {"seconds": 30, "start_playback": True, "from_bar": 1}
    assert validate_args("fl_watch_peaks", {"seconds": "10", "start_playback": "false"})["start_playback"] is False
    notes = validate_args("fl_play_midi", {"notes": [{"pitch": "60", "start_beat": 0, "length_beats": "0.5"}]})
    assert notes["notes"][0] == {"pitch": 60, "start_beat": 0, "length_beats": 0.5, "velocity": 100}
    assert notes["record"] is False and notes["start_bar"] == 2
    assert validate_args("fix_midi", {"path": "/x.mid", "fix": "quantize", "grid_beats": "0.25"})["grid_beats"] == 0.25


@pytest.mark.parametrize(
    "name,args,code",
    [
        ("nope", {}, "NOT_FOUND"),
        ("fl_set_track", {}, "OUT_OF_RANGE"),
        ("fl_set_track", {"track": "Kick", "volume_db": 9}, "OUT_OF_RANGE"),
        ("fl_set_track", {"track": -1}, "OUT_OF_RANGE"),
        ("fl_set_track", {"track": "Kick", "eq_band": "air"}, "OUT_OF_RANGE"),
        ("fl_watch_peaks", {"seconds": 1}, "OUT_OF_RANGE"),
        ("fix_midi", {"path": "/x.mid", "fix": "quantize", "key": "H major"}, "OUT_OF_RANGE"),
        ("fl_play_midi", {}, "OUT_OF_RANGE"),
        ("fl_play_midi", {"path": "/x.mid", "notes": []}, "OUT_OF_RANGE"),
        ("fl_play_midi", {"notes": [{"pitch": 60, "start_beat": 0, "length_beats": 0}]}, "OUT_OF_RANGE"),
        ("analyze_audio", {"path": 3}, "OUT_OF_RANGE"),
    ],
)
def test_invalid_calls_return_spec_errors(name, args, code):
    out = call_tool(name, args)
    assert is_error(out) and out["error"]["code"] == code and out["error"]["message"]
    assert out["error"]["code"] in ERROR_CODES
    jsonschema.validate(out, ERROR_SCHEMA)


def test_stray_exceptions_become_errors(monkeypatch):
    import mixassist.tools as tools

    monkeypatch.setitem(tools.TOOLS, "fl_undo", lambda a: 1 / 0)
    assert call_tool("fl_undo", {})["error"]["code"] == "FL_ERROR"
    monkeypatch.setitem(tools.TOOLS, "fl_undo", lambda a: open("/nonexistent/x"))
    assert call_tool("fl_undo", {})["error"]["code"] == "FILE_ERROR"


ERROR_SCHEMA = {
    "type": "object", "required": ["error"],
    "properties": {"error": {"type": "object", "required": ["code", "message"], "properties": {
        "code": {"enum": list(ERROR_CODES)}, "message": {"type": "string"},
        "candidates": {"type": "array", "items": {"type": "string"}}, "hint": {"type": "string"}}}},
}
