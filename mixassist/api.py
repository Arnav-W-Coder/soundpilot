"""SoundPilot HTTP API (build brief, HTTP API). Run: uvicorn mixassist.api:app --port 8000"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from mixassist import agent as agent_mod
from mixassist.errors import ToolError, is_error
from mixassist.fl import get_fl
from mixassist.llm import LLMError, provider_config
from mixassist.tools import call_tool

log = logging.getLogger("mixassist.api")

_llm_state: dict = {"provider": None, "model": None, "available": None, "message": "not checked yet"}


def _check_model() -> None:
    try:
        kind, model = provider_config()
        _llm_state.update(provider=kind, model=model)
        available, message = agent_mod.get_agent().provider.check_model()
    except (LLMError, Exception) as exc:  # noqa: BLE001 - status must never crash startup
        available, message = False, str(exc)
    _llm_state.update(available=available, message=message)
    (log.info if available else log.warning)("LLM check: %s", message)


def start_model_check() -> threading.Thread:
    try:
        kind, model = provider_config()
        _llm_state.update(provider=kind, model=model)
    except LLMError as exc:
        _llm_state.update(available=False, message=str(exc))
    thread = threading.Thread(target=_check_model, name="model-check", daemon=True)
    thread.start()
    return thread


@asynccontextmanager
async def lifespan(app: FastAPI):
    fl = get_fl()
    log.info("FL backend: %s (connected=%s)", fl.backend, fl.connected())
    start_model_check()
    yield


app = FastAPI(title="SoundPilot x MixAssist", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------- bodies
class ChatBody(BaseModel):
    session_id: str
    message: str


class UndoBody(BaseModel):
    change_id: str | None = None


class AudioBody(BaseModel):
    path: str
    reference_path: str | None = None


class MidiBody(BaseModel):
    path: str


class DiagnoseBody(BaseModel):
    complaint: str
    analysis_id: str
    track: int | str | None = None


class VerifyBody(BaseModel):
    complaint: str
    before_analysis_id: str
    after_path: str


class PeaksBody(BaseModel):
    seconds: float | None = None
    from_bar: int | None = None


class MidiFixBody(BaseModel):
    path: str
    fix: str
    key: str | None = None
    grid_beats: float | None = None
    strength_pct: float | None = None


class MidiPlayBody(BaseModel):
    path: str
    channel: int | None = None
    record: bool = False
    start_bar: int | None = None


def _args(body: BaseModel) -> dict:
    return {k: v for k, v in body.model_dump().items() if v is not None}


# ---------------------------------------------------------------- endpoints
@app.get("/api/status")
def status() -> dict:
    fl = get_fl()
    connected = fl.connected()
    out = {
        "fl_backend": fl.backend,
        "fl_connected": connected,
        "llm_provider": _llm_state["provider"],
        "model": _llm_state["model"],
        "model_available": _llm_state["available"],
        "model_message": _llm_state["message"],
    }
    if connected:
        try:
            out["fl_api"] = int(fl.request("hello").get("api") or 0)
        except ToolError:
            pass
    return out


@app.get("/api/mix")
def mix(include_empty: bool = False) -> dict:
    return call_tool("fl_get_mix_overview", {"include_empty": include_empty})


@app.post("/api/chat")
def chat(body: ChatBody):
    try:
        return agent_mod.get_agent().chat(body.session_id, body.message)
    except LLMError as exc:
        log.error("chat failed: %s", exc)
        return JSONResponse(status_code=502, content={"error": {"code": "LLM_ERROR", "message": str(exc)}})


@app.get("/api/proposals")
def proposals(session_id: str | None = None) -> dict:
    return {"proposals": agent_mod.list_proposals(session_id)}


@app.post("/api/proposals/{proposal_id}/apply")
def apply(proposal_id: str) -> dict:
    try:
        return agent_mod.apply_proposal(proposal_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown proposal {proposal_id}")


@app.post("/api/proposals/{proposal_id}/discard")
def discard(proposal_id: str) -> dict:
    try:
        return agent_mod.discard_proposal(proposal_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown proposal {proposal_id}")


@app.post("/api/undo")
def undo(body: UndoBody | None = None) -> dict:
    return call_tool("fl_undo", _args(body) if body else {})


@app.post("/api/analyze/audio")
def analyze_audio(body: AudioBody) -> dict:
    return call_tool("analyze_audio", _args(body))


@app.post("/api/analyze/midi")
def analyze_midi(body: MidiBody) -> dict:
    return call_tool("analyze_midi", _args(body))


@app.post("/api/diagnose")
def diagnose(body: DiagnoseBody) -> dict:
    result = call_tool("diagnose_complaint", _args(body))
    if is_error(result):
        return result
    return {**result, "proposals": agent_mod.proposals_from_diagnosis(result)}


@app.post("/api/verify")
def verify(body: VerifyBody) -> dict:
    return call_tool("verify_fix", _args(body))


@app.post("/api/watch_peaks")
def watch_peaks(body: PeaksBody | None = None) -> dict:
    result = call_tool("fl_watch_peaks", _args(body) if body else {})
    if is_error(result):
        return result
    return {**result, "proposals": agent_mod.proposals_from_peaks(result)}


@app.post("/api/midi/fix")
def midi_fix(body: MidiFixBody) -> dict:
    try:
        proposal = agent_mod.create_proposal("fix_midi", _args(body), "")
    except ToolError as exc:
        return exc.to_dict()
    return {"proposals": [proposal]}


@app.post("/api/midi/play")
def midi_play(body: MidiPlayBody) -> dict:
    args = _args(body)
    if not body.record:
        return call_tool("fl_play_midi", args)
    try:
        proposal = agent_mod.create_proposal("fl_play_midi", args, "")
    except ToolError as exc:
        return exc.to_dict()
    return {"proposals": [proposal]}
