"""Gemma tool loop: read tools run, write tools become proposals the user approves (build brief, Agent loop)."""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field

from mixassist import agent_tools
from mixassist.errors import ToolError, error_dict, is_error
from mixassist.fl import get_fl
from mixassist.fl.tracks import resolve_track
from mixassist.llm.base import LLMProvider, LLMResponse, ToolCall, function_declarations, parse_text_tool_calls
from mixassist.logctx import clip, log_context
from mixassist.schemas import TOOL_NAMES
from mixassist.tools import call_tool, is_write, summarize_result, validate_args

log = logging.getLogger(__name__)

MAX_TOOL_CALLS = 6
MAX_MODEL_TURNS = 8
MAX_RESULT_CHARS = 6000

SYSTEM_PROMPT = """You are SoundPilot, a mixing assistant for FL Studio. You act only through the provided tools.

Rules:
- Talk in units only: dB, dBFS, dBTP, LUFS, LU, Hz, beats, seconds, percent. Pan is -100 (left) .. 100 (right).
- Writes (fl_set_track, fl_undo, fix_midi, fl_play_midi with record=true) are NOT applied by you: each call becomes a proposal that the user approves in the SoundPilot panel. Never say a change was applied. For a clear request call the write tool directly, e.g. "turn the kick down 2 dB" -> fl_set_track(track="Kick", volume_change_db=-2). You don't need to read the mixer first.
- For vague complaints about the sound ("too bright", "muddy", "harsh", "sounds bad"), measure first: analyze_audio on the exported mix WAV (absolute path), then diagnose_complaint with its analysis_id. If you don't know the WAV path, ask for it.
- If diagnose_complaint returns needs_clarification=true, ask the user its question with its options and do NOT call any write tool in that reply. Never propose writes alongside a clarification question.
- If a track name is ambiguous (AMBIGUOUS_TARGET), ask which track; never guess.
- If a tool returns an error, fix the arguments and retry once, or explain the problem.
- Use only the argument names in each tool's schema; numbers must be numbers.
- Reply in 1-3 short sentences and quote measured numbers with their units."""


# ------------------------------------------------------------------ proposals
_proposals: dict[str, dict] = {}
_plock = threading.RLock()


def _new_id() -> str:
    return "p_" + uuid.uuid4().hex[:6]


def _fmt(value, unit: str) -> str:
    if value is None:
        return "-"
    if unit == "Hz":
        return f"{value:.0f} Hz"
    if unit == "%":
        return f"{value:.0f}%"
    return f"{value:.1f} {unit}"


_FIELD_WORDS = {"volume_db": "volume", "pan_pct": "pan", "stereo_width_pct": "width",
                "eq_gain_db": "EQ gain", "eq_freq_hz": "EQ freq"}


def _set_track_preview(args: dict) -> dict:
    overview = call_tool("fl_get_mix_overview", {"include_empty": True})
    if is_error(overview):
        e = overview["error"]
        raise ToolError(e["code"], e["message"], e.get("candidates"), e.get("hint"))
    track = resolve_track(args["track"], overview["tracks"])
    changes = []
    if "volume_db" in args and "volume_change_db" in args:
        raise ToolError("OUT_OF_RANGE", "give volume_db or volume_change_db, not both")
    if "volume_change_db" in args:
        after = round(track["volume_db"] + float(args["volume_change_db"]), 2)
        changes.append({"field": "volume_db", "before": track["volume_db"], "after": after, "unit": "dB"})
    if "volume_db" in args:
        changes.append({"field": "volume_db", "before": track["volume_db"], "after": float(args["volume_db"]), "unit": "dB"})
    for key in ("pan_pct", "stereo_width_pct"):
        if key in args:
            changes.append({"field": key, "before": track[key], "after": float(args[key]), "unit": "%"})
    band_name = args.get("eq_band")
    if band_name:
        if "eq_gain_db" not in args and "eq_freq_hz" not in args:
            raise ToolError("OUT_OF_RANGE", "eq_band needs eq_gain_db and/or eq_freq_hz")
        band = next((b for b in track["eq"] if b["band"] == band_name), None)
        if band is None:
            raise ToolError("UNSUPPORTED", "this FL build exposes no built-in EQ (API < 35)")
        if "eq_gain_db" in args:
            changes.append({"field": "eq_gain_db", "before": band["gain_db"], "after": float(args["eq_gain_db"]), "unit": "dB"})
        if "eq_freq_hz" in args:
            changes.append({"field": "eq_freq_hz", "before": band["freq_hz"], "after": float(args["eq_freq_hz"]), "unit": "Hz"})
    elif "eq_gain_db" in args or "eq_freq_hz" in args:
        raise ToolError("OUT_OF_RANGE", "eq_gain_db / eq_freq_hz need eq_band ('low', 'mid' or 'high')")
    if not changes:
        raise ToolError("OUT_OF_RANGE", "nothing to change",
                        hint="give volume_change_db, volume_db, pan_pct, stereo_width_pct or eq_band with eq_gain_db")
    for c in changes:
        if c["field"] == "volume_db" and not (-60.0 <= c["after"] <= 5.6):
            raise ToolError("OUT_OF_RANGE", f"{track['name']} would end at {c['after']:.1f} dB (fader range -60..5.6 dB)")
    parts = []
    for c in changes:
        word = _FIELD_WORDS[c["field"]]
        if band_name and c["field"].startswith("eq_"):
            word = f"{band_name} {word}"
        parts.append(f"{word} {_fmt(c['before'], c['unit'])} -> {_fmt(c['after'], c['unit'])}")
    return {"track": {"index": track["index"], "name": track["name"]}, "changes": changes,
            "summary": f"{track['name']}: " + ", ".join(parts)}


def _undo_preview(args: dict) -> dict:
    history = get_fl().undo_history
    if args.get("change_id"):
        entry = next((h for h in history if h["change_id"] == args["change_id"] and not h["undone"]), None)
    else:
        entry = next((h for h in reversed(history) if not h["undone"]), None)
    if entry is None:
        raise ToolError("NOT_FOUND", "nothing to undo" if not args.get("change_id") else f"no undoable change {args['change_id']!r}")
    overview = call_tool("fl_get_mix_overview", {"include_empty": True})
    current = next((t for t in overview.get("tracks", []) if t["index"] == entry["index"]), None)
    changes = []
    if current is not None:
        for key, unit in (("volume_db", "dB"), ("pan_pct", "%"), ("stereo_width_pct", "%")):
            before, after = current[key], entry["before"][key]
            if abs(before - after) > 1e-6:
                changes.append({"field": key, "before": before, "after": after, "unit": unit})
        if "eq_band" in entry["before"]:
            band = next((b for b in current["eq"] if b["band"] == entry["before"]["eq_band"]), None)
            if band and abs(band["gain_db"] - entry["before"]["eq_gain_db"]) > 1e-6:
                changes.append({"field": "eq_gain_db", "before": band["gain_db"],
                                "after": entry["before"]["eq_gain_db"], "unit": "dB"})
    what = ", ".join(f"{_FIELD_WORDS[c['field']]} back to {_fmt(c['after'], c['unit'])}" for c in changes)
    return {"track": None, "changes": changes,
            "summary": f"Undo change {entry['change_id']} on {entry['name']}" + (f" ({what})" if what else "")}


_FIX_WORDS = {"snap_to_scale": "Snap out-of-key notes to the scale", "quantize": "Quantize note timing",
              "humanize": "Humanize timing and velocity"}


def _fix_midi_preview(args: dict) -> dict:
    path = args["path"]
    if not os.path.isfile(path):
        raise ToolError("FILE_ERROR", f"MIDI file not found: {path}", hint="use an absolute path to an exported .mid")
    detail = []
    if args.get("key"):
        detail.append(f"key {args['key']}")
    if args.get("grid_beats") is not None:
        detail.append(f"grid {args['grid_beats']} beats")
    if args.get("fix") != "snap_to_scale" and args.get("strength_pct", 100) != 100:
        detail.append(f"strength {args['strength_pct']:.0f}%")
    extra = f" ({', '.join(detail)})" if detail else ""
    return {"track": None, "changes": [],
            "summary": f"{_FIX_WORDS[args['fix']]} in {os.path.basename(path)}{extra}; writes a fixed .mid and "
                       "MCP_Apply.pyscript for the piano roll"}


def _play_preview(args: dict) -> dict:
    if args.get("path") and not os.path.isfile(args["path"]):
        raise ToolError("FILE_ERROR", f"MIDI file not found: {args['path']}")
    what = os.path.basename(args["path"]) if args.get("path") else f"{len(args.get('notes') or [])} notes"
    channel = f" on channel {args['channel']}" if args.get("channel") is not None else " on the selected channel"
    return {"track": None, "changes": [],
            "summary": f"Record {what} into FL{channel}, starting at bar {args.get('start_bar', 2)}"}


_PREVIEWS = {"fl_set_track": _set_track_preview, "fl_undo": _undo_preview, "fix_midi": _fix_midi_preview,
             "fl_play_midi": _play_preview}


def create_proposal(tool: str, args: dict, why: str, session_id: str | None = None) -> dict:
    """Validate a write call and register it as a pending proposal. Raises ToolError."""
    if tool not in _PREVIEWS:
        raise ToolError("UNSUPPORTED", f"{tool} is not a write tool")
    clean = validate_args(tool, args)
    if tool == "fl_play_midi" and not clean.get("record"):
        raise ToolError("UNSUPPORTED", "live fl_play_midi runs directly; only record=true is a proposal")
    preview = _PREVIEWS[tool](clean)
    proposal = {
        "proposal_id": _new_id(),
        "tool": tool,
        "args": {k: v for k, v in clean.items() if k in args or tool == "fl_play_midi"},
        "why": why or preview["summary"],
        "status": "pending",
        "preview": preview,
    }
    with _plock:
        _proposals[proposal["proposal_id"]] = {**proposal, "_session": session_id, "_result": None}
    log.info("proposal %s created for %s: %s", proposal["proposal_id"], tool, preview["summary"])
    return proposal


def public(proposal: dict) -> dict:
    return {k: copy.deepcopy(v) for k, v in proposal.items() if not k.startswith("_")}


def get_proposal(proposal_id: str) -> dict | None:
    with _plock:
        p = _proposals.get(proposal_id)
        return public(p) if p else None


def list_proposals(session_id: str | None = None) -> list[dict]:
    with _plock:
        return [public(p) for p in _proposals.values() if session_id is None or p["_session"] == session_id]


def apply_proposal(proposal_id: str) -> dict:
    """Run the proposal's write tool. Raises KeyError if the id is unknown."""
    with _plock:
        p = _proposals[proposal_id]
        if p["status"] != "pending":
            return {"proposal_id": proposal_id, "tool": p["tool"],
                    "result": error_dict("NOT_FOUND", f"proposal {proposal_id} is already {p['status']}")}
        p["status"] = "applying"
    log.info("proposal %s apply start: %s", proposal_id, p["preview"]["summary"])
    result = call_tool(p["tool"], p["args"])
    with _plock:
        p["status"] = "failed" if is_error(result) else "applied"
        p["_result"] = result
    log.info("proposal %s %s: %s", proposal_id, p["status"], summarize_result(p["tool"], result))
    return {"proposal_id": proposal_id, "tool": p["tool"], "result": result}


def discard_proposal(proposal_id: str) -> dict:
    with _plock:
        p = _proposals[proposal_id]
        if p["status"] == "pending":
            p["status"] = "discarded"
        log.info("proposal %s discard: status %s", proposal_id, p["status"])
        return {"proposal_id": proposal_id, "status": p["status"]}


def proposals_from_diagnosis(result: dict, session_id: str | None = None) -> list[dict]:
    out = []
    if is_error(result) or result.get("needs_clarification"):
        return out
    for change in result.get("proposed_changes") or []:
        try:
            out.append(create_proposal(change.get("tool", "fl_set_track"), change.get("args") or {},
                                       change.get("why", ""), session_id))
        except ToolError as exc:
            log.warning("skipping diagnose proposal %s: %s", change, exc.message)
    return out


def proposals_from_peaks(result: dict, session_id: str | None = None) -> list[dict]:
    out = []
    if is_error(result):
        return out
    for s in result.get("suggestions") or []:
        try:
            out.append(create_proposal("fl_set_track", {"track": s["track"], "volume_change_db": s["volume_change_db"]},
                                       s.get("reason", ""), session_id))
        except ToolError as exc:
            log.warning("skipping peak suggestion %s: %s", s, exc.message)
    return out


def reset_proposals() -> None:
    with _plock:
        _proposals.clear()


# ---------------------------------------------------------------- the loop
def compact_for_model(result: dict) -> str:
    """JSON for the model: drop bulky arrays so small context windows keep the important numbers."""
    data = copy.deepcopy(result)

    def shrink(obj, depth=0):
        if isinstance(obj, dict):
            obj.pop("third_octave_db", None)
            return {k: shrink(v, depth + 1) for k, v in obj.items()}
        if isinstance(obj, list):
            if len(obj) > 24:
                return [shrink(v, depth + 1) for v in obj[:24]] + [f"... {len(obj) - 24} more"]
            return [shrink(v, depth + 1) for v in obj]
        if isinstance(obj, float):
            return round(obj, 3)
        return obj

    text = json.dumps(shrink(data), ensure_ascii=False)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "...(truncated)"
    return text


@dataclass
class Session:
    session_id: str
    messages: list[dict] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class TurnResult:
    reply: str
    tool_trace: list[dict]
    proposals: list[dict]
    needs_clarification: bool
    question: str | None = None
    options: list[str] | None = None
    attempted_calls: list[dict] = field(default_factory=list)  # every call the model made, in order
    invented_tools: list[str] = field(default_factory=list)
    writes_after_clarification: int = 0
    text_fallbacks: int = 0
    hit_call_cap: bool = False
    agent_tool_cache: dict = field(default_factory=dict, repr=False)  # agent-only tool results, per turn

    def to_api(self) -> dict:
        out = {"reply": self.reply, "tool_trace": self.tool_trace, "proposals": self.proposals,
               "needs_clarification": self.needs_clarification}
        if self.needs_clarification:
            out["question"] = self.question or self.reply
            out["options"] = self.options or []
        return out


class Agent:
    def __init__(self, provider: LLMProvider | None = None, max_tool_calls: int = MAX_TOOL_CALLS,
                 temperature: float = 0.0, system_prompt: str = SYSTEM_PROMPT):
        self._provider = provider
        self.max_tool_calls = max_tool_calls
        self.temperature = temperature
        self.system_prompt = system_prompt + agent_tools.prompt_rules()
        self.sessions: dict[str, Session] = {}
        self._slock = threading.Lock()
        self.tools = function_declarations() + agent_tools.declarations()
        self._local = threading.local()  # per-turn LLM call count; turns of different sessions run in parallel

    @property
    def provider(self) -> LLMProvider:
        if self._provider is None:
            from mixassist.llm import make_provider

            self._provider = make_provider()
        return self._provider

    def session(self, session_id: str) -> Session:
        with self._slock:
            if session_id not in self.sessions:
                self.sessions[session_id] = Session(session_id)
            return self.sessions[session_id]

    def reset(self, session_id: str) -> None:
        with self._slock:
            self.sessions.pop(session_id, None)

    def chat(self, session_id: str, message: str) -> dict:
        return self.run_turn(session_id, message).to_api()

    def run_turn(self, session_id: str, message: str) -> TurnResult:
        session = self.session(session_id)
        with session.lock:
            turn_no = 1 + sum(1 for m in session.messages if m["role"] == "user")
            with log_context(f"s={session_id[:8]}#{turn_no}"):
                return self._logged_turn(session, message)

    def _logged_turn(self, session: Session, message: str) -> TurnResult:
        t0 = time.monotonic()
        log.info("chat start: %r", clip(message, 80))
        self._local.llm_calls = 0
        try:
            turn = self._turn(session, message)
        except Exception as exc:
            log.error("chat failed after %.1f s, %d LLM call(s): %s: %s", time.monotonic() - t0, self._local.llm_calls,
                      type(exc).__name__, clip(exc, 200))
            raise
        log.info("chat end in %.1f s: %d LLM call(s), %d tool call(s), %d proposal(s), needs_clarification=%s",
                 time.monotonic() - t0, self._local.llm_calls, len(turn.tool_trace), len(turn.proposals),
                 turn.needs_clarification)
        return turn

    def _llm(self, prompt: list[dict]) -> LLMResponse:
        provider = self.provider
        self._local.llm_calls += 1
        n = self._local.llm_calls
        log.info("llm #%d start: %s %s (%d messages)", n, provider.name, provider.model, len(prompt))
        t0 = time.monotonic()
        try:
            response = provider.chat(prompt, self.tools, self.temperature)
        except Exception as exc:
            log.error("llm #%d failed after %.1f s: %s", n, time.monotonic() - t0, clip(exc, 200))
            raise
        if response.tool_calls:
            what = f"{len(response.tool_calls)} function call(s) {[c.name for c in response.tool_calls]}"
        else:
            what = f"text ({len(response.text or '')} chars)" if response.text else "empty response"
        log.info("llm #%d done in %.1f s: %s", n, time.monotonic() - t0, what)
        return response

    def _turn(self, session: Session, message: str) -> TurnResult:
        session.messages.append({"role": "user", "content": message})
        turn = TurnResult(reply="", tool_trace=[], proposals=[], needs_clarification=False)
        clarification: dict | None = None
        ambiguity: dict | None = None
        calls_made = 0
        final_text = ""
        failed: dict[tuple[str, str], dict] = {}

        for _ in range(MAX_MODEL_TURNS):
            prompt = [{"role": "system", "content": self.system_prompt}] + session.messages
            response = self._llm(prompt)
            if not response.text and not response.tool_calls:
                log.warning("model returned an empty response; retrying once")
                response = self._llm(prompt)
            calls = list(response.tool_calls)
            if not calls and response.text:
                calls = parse_text_tool_calls(response.text, set(TOOL_NAMES) | agent_tools.names())
                if calls:
                    turn.text_fallbacks += 1
                    log.warning("model wrote %d tool call(s) as text; using text fallback parser: %s",
                                len(calls), [c.name for c in calls])
            if not calls:
                final_text = response.text
                session.messages.append({"role": "assistant", "content": response.text})
                break
            if calls_made >= self.max_tool_calls:
                turn.hit_call_cap = True
                final_text = response.text
                session.messages.append({"role": "assistant", "content": response.text or ""})
                break
            session.messages.append({"role": "assistant", "content": response.text or "",
                                     "tool_calls": [{"name": c.name, "args": c.args} for c in calls]})
            for call in calls:
                turn.attempted_calls.append({"tool": call.name, "args": call.args})
                if calls_made >= self.max_tool_calls:
                    turn.hit_call_cap = True
                    result = error_dict("OUT_OF_RANGE", f"tool call limit ({self.max_tool_calls}) reached for this turn",
                                        hint="answer the user with what you have")
                    session.messages.append({"role": "tool", "name": call.name, "content": json.dumps(result)})
                    continue
                calls_made += 1
                key = (call.name, json.dumps(call.args, sort_keys=True, default=str))
                if key in failed:
                    prev = failed[key]["error"]
                    result = error_dict(prev["code"], "you already made this exact call and it failed: " + prev["message"],
                                        prev.get("candidates"), prev.get("hint") or "change the arguments")
                    model_view = json.dumps(result)
                else:
                    model_view, result = self._run_call(call, session, turn, clarification)
                    if is_error(result):
                        failed[key] = result
                if call.name == "diagnose_complaint" and not is_error(result) and result.get("needs_clarification"):
                    clarification = {"question": result.get("question"), "options": result.get("options") or []}
                if is_error(result) and result["error"]["code"] == "AMBIGUOUS_TARGET":
                    ambiguity = result["error"]
                turn.tool_trace.append({"tool": call.name, "args": call.args, "result": result})
                session.messages.append({"role": "tool", "name": call.name, "content": model_view})
            if turn.hit_call_cap and calls_made >= self.max_tool_calls:
                # one last chance to answer in words; further tool calls are refused above
                continue
        else:
            final_text = final_text or ""

        if clarification:
            for p in turn.proposals:
                discard_proposal(p["proposal_id"])
            turn.proposals = []
            turn.needs_clarification = True
            turn.question = clarification["question"]
            turn.options = clarification["options"]
        elif ambiguity and not turn.proposals:
            turn.needs_clarification = True
            turn.options = list(ambiguity.get("candidates") or [])
            turn.question = final_text or f"Which track do you mean: {' or '.join(turn.options)}?"

        turn.reply = final_text.strip() or self._fallback_reply(turn)
        return turn

    def _run_call(self, call: ToolCall, session: Session, turn: TurnResult, clarification: dict | None):
        name, args = call.name, call.args if isinstance(call.args, dict) else {}
        if name in agent_tools.names():
            return agent_tools.run(name, args, turn.agent_tool_cache)
        if name not in TOOL_NAMES:
            turn.invented_tools.append(name)
            result = error_dict("NOT_FOUND", f"there is no tool named {name!r}", candidates=list(TOOL_NAMES))
            return json.dumps(result), result
        if is_write(name, args):
            if clarification:
                turn.writes_after_clarification += 1
                result = error_dict("NEEDS_CLARIFICATION", "ask the user the clarification question before proposing changes",
                                    candidates=clarification.get("options") or None)
                return json.dumps(result), result
            existing = next((p for p in turn.proposals if p["tool"] == name and p["args"] == _normalized(name, args)), None)
            try:
                proposal = existing or create_proposal(name, args, _why(name, args, session), session.session_id)
            except ToolError as exc:
                result = exc.to_dict()
                return json.dumps(result), result
            if not existing:
                turn.proposals.append(proposal)
            result = {"status": "proposed", "proposal_id": proposal["proposal_id"], "summary": proposal["preview"]["summary"],
                      "note": "Not applied yet: the user approves it in the SoundPilot panel."}
            return json.dumps(result), result

        result = call_tool(name, args)
        extra: list[dict] = []
        if name == "diagnose_complaint" and not result.get("needs_clarification"):
            extra = proposals_from_diagnosis(result, session.session_id)
        elif name == "fl_watch_peaks":
            extra = proposals_from_peaks(result, session.session_id)
        if extra:
            turn.proposals.extend(extra)
            listing = [{"proposal_id": p["proposal_id"], "summary": p["preview"]["summary"]} for p in extra]
            note = {"proposals_created": listing,
                    "note": "These changes are already proposed to the user; don't call fl_set_track again for them."}
            return compact_for_model({**result, **note}), result
        return compact_for_model(result), result

    def _fallback_reply(self, turn: TurnResult) -> str:
        if turn.needs_clarification:
            return turn.question or "Can you tell me more about what you hear?"
        if turn.proposals:
            return "Proposed for your approval: " + "; ".join(p["preview"]["summary"] for p in turn.proposals) + "."
        if turn.tool_trace:
            last = turn.tool_trace[-1]
            if is_error(last["result"]):
                return f"{last['tool']} failed: {last['result']['error']['message']}"
            return f"Done: ran {', '.join(t['tool'] for t in turn.tool_trace)}."
        return "I couldn't produce an answer; please try rephrasing."


def _normalized(name: str, args: dict) -> dict:
    try:
        clean = validate_args(name, args)
    except ToolError:
        return args
    return {k: v for k, v in clean.items() if k in args or name == "fl_play_midi"}


def _why(name: str, args: dict, session: Session) -> str:
    last_user = next((m["content"] for m in reversed(session.messages) if m["role"] == "user"), "")
    return f"You asked: \"{last_user[:120]}\"" if last_user else name


_default_agent: Agent | None = None
_alock = threading.Lock()


def get_agent() -> Agent:
    global _default_agent
    with _alock:
        if _default_agent is None:
            _default_agent = Agent()
        return _default_agent


def set_agent(agent: Agent | None) -> None:
    global _default_agent
    with _alock:
        _default_agent = agent
