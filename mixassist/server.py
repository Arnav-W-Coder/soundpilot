"""MCP stdio server exposing the 10 spec tools (official `mcp` SDK, low-level Server)."""
from __future__ import annotations

import json
import logging

import anyio
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from mixassist import __version__
from mixassist.errors import is_error
from mixassist.schemas import DESCRIPTIONS, SCHEMAS, TOOL_NAMES
from mixassist.tools import call_tool

log = logging.getLogger(__name__)

INSTRUCTIONS = (
    "MixAssist controls FL Studio's mixer and analyzes exported audio and MIDI. Values are in dB, dBFS, dBTP, "
    "LUFS, LU, Hz, beats, seconds or percent. Write tools (fl_set_track, fl_undo, fix_midi, fl_play_midi with "
    "record=true) need human approval. For vague complaints call diagnose_complaint; if it returns "
    "needs_clarification, ask the question and do not write."
)


def list_tools() -> list[types.Tool]:
    return [types.Tool(name=name, description=DESCRIPTIONS[name], input_schema=SCHEMAS[name]) for name in TOOL_NAMES]


def tool_result(result: dict) -> types.CallToolResult:
    text = json.dumps(result, ensure_ascii=False)
    if is_error(result):
        return types.CallToolResult(content=[types.TextContent(text=text)], is_error=True)
    return types.CallToolResult(content=[types.TextContent(text=text)], structured_content=result, is_error=False)


async def _on_list_tools(ctx, params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=list_tools())


async def _on_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
    result = await anyio.to_thread.run_sync(call_tool, params.name, dict(params.arguments or {}))
    return tool_result(result)


def build_server() -> Server:
    return Server(
        "mixassist",
        version=__version__,
        instructions=INSTRUCTIONS,
        on_list_tools=_on_list_tools,
        on_call_tool=_on_call_tool,
    )


async def serve_stdio() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    anyio.run(serve_stdio)
