"""Drive `python -m mixassist --fake` over stdio with the official MCP client SDK (spec §8.1 equivalent):
all 10 tools list with valid schemas, and each tool is called once with valid and once with invalid arguments."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import anyio
import jsonschema
import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from mixassist.errors import ERROR_CODES

ROOT = Path(__file__).resolve().parents[1]
HAS_AUDIO = (ROOT / "mixassist" / "tools" / "audio_tools.py").exists()


def _text(result) -> dict:
    return json.loads(result.content[0].text)


async def _session(calls, tmp: Path) -> dict:
    env = {"SOUNDPILOT_PYSCRIPT_DIR": str(tmp), "PYTHONPATH": str(ROOT), "SOUNDPILOT_LOG": "WARNING"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "mixassist", "--fake"], env=env, cwd=str(ROOT))
    out: dict = {"calls": {}}
    with open(os.devnull, "w") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                out["tools"] = (await session.list_tools()).tools
                for key, name, args in calls:
                    if callable(args):
                        args = args(out["calls"])
                    result = await session.call_tool(name, args)
                    out["calls"][key] = (result.is_error, _text(result))
    return out


@pytest.fixture(scope="module")
def mcp_run(tmp_path_factory, fixtures_dir_or_none):
    tmp = tmp_path_factory.mktemp("mcp")
    midi_src = ROOT / "fixtures" / "midi" / "melody_c_major_3wrong.mid"
    midi = tmp / "melody.mid"
    shutil.copy(midi_src, midi)
    audio = (fixtures_dir_or_none / "audio") if fixtures_dir_or_none else None
    calls = [
        ("fl_get_mix_overview", "fl_get_mix_overview", {}),
        ("fl_set_track", "fl_set_track", {"track": "Kick", "volume_change_db": -2}),
        ("fl_undo", "fl_undo", {}),
        ("fl_watch_peaks", "fl_watch_peaks", {"seconds": 2}),
        ("analyze_midi", "analyze_midi", {"path": str(midi)}),
        ("fix_midi", "fix_midi", {"path": str(midi), "fix": "snap_to_scale"}),
        ("fl_play_midi", "fl_play_midi", {"notes": [{"pitch": 60, "start_beat": 0, "length_beats": 0.25}]}),
    ]
    if audio is not None:
        calls += [
            ("analyze_audio", "analyze_audio", {"path": str(audio / "bright.wav"), "reference_path": str(audio / "pink.wav")}),
            ("diagnose_complaint", "diagnose_complaint",
             lambda done: {"complaint": "it's too bright", "analysis_id": done["analyze_audio"][1]["analysis_id"]}),
            ("verify_fix", "verify_fix",
             lambda done: {"complaint": "too bright", "before_analysis_id": done["analyze_audio"][1]["analysis_id"],
                           "after_path": str(audio / "bright_fixed.wav")}),
        ]
    invalid = {
        "fl_get_mix_overview": {"include_empty": "maybe"},
        "fl_set_track": {"track": "Kick", "volume_db": 9},
        "fl_undo": {"change_id": "c_missing"},
        "fl_watch_peaks": {"seconds": 1},
        "analyze_audio": {"path": "/nonexistent/mix.wav"},
        "diagnose_complaint": {"complaint": "too bright"},
        "verify_fix": {"complaint": "too bright"},
        "analyze_midi": {"path": "/nonexistent/x.mid"},
        "fix_midi": {"path": str(midi), "fix": "explode"},
        "fl_play_midi": {},
    }
    calls += [(f"invalid:{name}", name, args) for name, args in invalid.items()]
    calls.append(("unknown", "not_a_tool", {}))
    return anyio.run(_session, calls, tmp)


@pytest.fixture(scope="module")
def fixtures_dir_or_none(request):
    if not HAS_AUDIO:
        return None
    return request.getfixturevalue("fixtures_dir")


def test_lists_all_ten_tools_with_valid_schemas(mcp_run):
    tools = {t.name: t for t in mcp_run["tools"]}
    assert len(tools) == 10
    from mixassist.schemas import SCHEMAS

    for name, tool in tools.items():
        jsonschema.Draft202012Validator.check_schema(tool.input_schema)
        assert tool.input_schema == SCHEMAS[name]
        assert tool.description


VALID = ["fl_get_mix_overview", "fl_set_track", "fl_undo", "fl_watch_peaks", "analyze_midi", "fix_midi",
         "fl_play_midi", "analyze_audio", "diagnose_complaint", "verify_fix"]


@pytest.mark.parametrize("name", VALID)
def test_valid_call(mcp_run, name):
    if name not in mcp_run["calls"]:
        pytest.skip("audio tools not available yet")
    is_err, body = mcp_run["calls"][name]
    assert not is_err, body
    assert "error" not in body


def test_valid_call_results(mcp_run):
    calls = mcp_run["calls"]
    assert calls["fl_set_track"][1]["after"]["volume_db"] == pytest.approx(-2.0, abs=0.25)
    assert calls["fl_undo"][1]["restored"]["volume_db"] == pytest.approx(0.0, abs=0.1)
    assert len(calls["fl_get_mix_overview"][1]["tracks"]) == 13


@pytest.mark.parametrize("name", list(VALID) + ["unknown"])
def test_invalid_call_returns_spec_error(mcp_run, name):
    key = "unknown" if name == "unknown" else f"invalid:{name}"
    is_err, body = mcp_run["calls"][key]
    assert is_err is True
    assert set(body) == {"error"}
    assert body["error"]["code"] in ERROR_CODES and body["error"]["message"]
