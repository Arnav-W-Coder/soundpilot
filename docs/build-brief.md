# SoundPilot x MixAssist v1: build brief

Frozen tool contracts live in [mixassist-v1-spec.md](mixassist-v1-spec.md) (§2–§3 are frozen: do not change tool names, schemas, units, or error codes). This brief adds the integration decisions, the HTTP API between the UI and the backend, and the definition of done.

SoundPilot combines:

- The UI from https://github.com/Arnav-W-Coder/soundpilot (React 18 + Vite 6 + lucide-react). Upstream `main` is already checked out in this repo (`index.html`, `package.json`, `src/main.jsx`, `src/StudioTools.jsx`, `src/style.css`, `.env.example` at the repo root). It is a demo with no real backend.
- The backend defined by the MixAssist v1 spec: 10 MCP tools, a SysEx bridge to FL, FakeFL, audio and MIDI analysis, and complaint rules.

## Decisions (already made; do not re-ask)

### LLM

Gemma 4 through the `google-genai` Python SDK. One client supports these providers, chosen by `SOUNDPILOT_LLM`:

- `vertex` (default): `genai.Client(vertexai=True, project=$GOOGLE_CLOUD_PROJECT, location=$GOOGLE_CLOUD_LOCATION)` with Application Default Credentials.
- `aistudio`: `genai.Client(api_key=$GEMINI_API_KEY)`.
- `ollama`: Ollama `/api/chat` with native tools at `http://127.0.0.1:11434` (override with `OLLAMA_HOST`). Dev/fallback provider.

The model ID always comes from `SOUNDPILOT_MODEL`. At startup, list or probe the available models and log whether the configured ID exists. If it doesn't, fail with a clear message suggesting the documented IDs: `gemma-4-26b-a4b-it-maas` on Vertex (location `global`), `gemma-4-26b-a4b-it` (or `gemma-4-31b-it`) on AI Studio, and `gemma4:31b-cloud` on Ollama.

Credentials stay server-side only. Never put them in Vite env vars.

#### Verified facts about this machine (Oct 25, 2026)

- Vertex: project `YOUR_PROJECT_ID` (from `gcloud config`), ADC present. `gemma-4-26b-a4b-it-maas` works **only at location `global`**. `gemma-4-e4b-it`, `gemma-4-e4b-it-maas`, `gemma-4-31b-it-maas` return 404 on Vertex. `client.models.list()` on Vertex does NOT list Gemma MaaS models, so the startup check must fall back to a tiny probe call (e.g. `count_tokens` or a 1-token generate) when the list doesn't contain the ID.
- Vertex returns frequent `429 RESOURCE_EXHAUSTED` for this model. The provider MUST retry 429/503 with exponential backoff (e.g. 1, 2, 4, 8, 16 s; ~6 attempts) before failing.
- Native function calling works on Vertex Gemma (`types.FunctionDeclaration(parameters_json_schema=...)`, `automatic_function_calling` disabled). In a quick test the model called `fl_set_track` with `{"track":"kick","volume":"-2"}`: it invented an argument name and used a string number. Schema descriptions must be explicit, numeric strings must be coerced, and schema-validation errors must be returned to the model as tool results so it can retry.
- AI Studio: the `GOOGLE_API_KEY` in the shell environment belongs to a project where the Gemini API is disabled (403); don't use it. The user's AI Studio key lives in the git-ignored `.env` as `GEMINI_API_KEY` (also `AQ.`-prefixed, but a Gemini API key, not a Vertex express key: Vertex express calls with it return 403 SERVICE_DISABLED). With it, `client.models.list()` lists `gemma-4-26b-a4b-it` and `gemma-4-31b-it`, native function calling works, and Gemma 4 returns thought parts before the answer. The free tier answers `429` under load; the same backoff handles it.
- Ollama is installed with the cloud model `gemma4:31b-cloud`. Don't use or download a local Ollama model (it costs RAM).
- **User directive: if Vertex doesn't work, use Ollama with Gemma 4 31B (`gemma4:31b-cloud`).** The Ollama provider must be fully functional, and evals/demos fall back to it if Vertex keeps failing.

Defaults: `SOUNDPILOT_LLM=vertex`, `SOUNDPILOT_MODEL=gemma-4-26b-a4b-it-maas`, `GOOGLE_CLOUD_LOCATION=global`, `GOOGLE_CLOUD_PROJECT` from env, else from `gcloud config get-value project`.

### FL

FakeFL is the default backend (`--fake` / `SOUNDPILOT_FL=fake`). Build the real bridge (`mixassist/fl/bridge.py` + `fl_script/MixAssist/device_MixAssist.py`) exactly as the spec says, using macOS IAC Driver port names "MA to FL" and "MA from FL", with loopMIDI names on Windows. Write `internal/probes.md` as a checklist for P1–P8 that a human runs on real FL. Do not claim probe results.

### Human-in-the-loop

The spec's ollmcp HIL approval is replaced by SoundPilot's proposal panel. Nothing writes to FL until the user approves it in the UI.

## Architecture

```
SoundPilot React UI --/api/*--> FastAPI mixassist/api.py --> agent.py tool loop --> Gemma (vertex | aistudio | ollama)
                                                                   \--> mixassist/tools (10 spec tools)
server.py (MCP stdio) ---------------------------------------------/        |--> fl/fake.py or fl/bridge.py
                                                                            |--> analysis, complaints, midi
```

- One tool implementation: the 10 tools live in `mixassist/tools/` as plain Python functions `dict -> dict`, validated against the spec's §3.2 JSON Schemas. `server.py` (MCP stdio, official `mcp` Python SDK) and the FastAPI agent call the same functions. Errors always use the §3.1 JSON format. MCP returns them with `isError: true`; the agent passes them back to the model as tool results.
- Repo layout follows spec §1, plus `mixassist/api.py`, `mixassist/agent.py`, `mixassist/llm/{base,genai_provider,ollama_provider}.py`, and `web/` for the UI. `pyproject.toml`, Python 3.11+, deps: numpy, scipy, soundfile, pyloudnorm, mido, python-rtmidi, mcp, fastapi, uvicorn, google-genai, jsonschema, pyyaml, pytest (+ httpx for tests).
- `python -m mixassist --fake` serves MCP over stdio.

## Agent loop (`mixassist/agent.py`)

- System prompt states the spec's rules: use units only, never propose writes alongside a clarification question, prefer `diagnose_complaint` for vague complaints.
- Convert schemas for Gemini function declarations (they don't reliably accept `oneOf` or `exclusiveMinimum`). Convert the track reference T to `string` with description "track index or name", then coerce numeric strings back to int before validation. Drop the top-level `oneOf` on `fl_play_midi` and enforce it inside the tool. Unit-test the converter on all 10 schemas.
- Read tools run automatically: `fl_get_mix_overview`, `fl_watch_peaks`, `analyze_audio`, `diagnose_complaint`, `verify_fix`, `analyze_midi`, and `fl_play_midi` in live mode.
- Write tools are intercepted and not executed: `fl_set_track`, `fl_undo`, `fix_midi`, and `fl_play_midi` with `record=true`. Each becomes a proposal (shape below) returned to the UI. `diagnose_complaint.proposed_changes` are turned into proposals the same way.
- Cap each turn at 6 tool calls. If the model emits text that looks like a tool call instead of a native function call, parse it as a fallback and log that it happened.
- Conversation state in memory, keyed by `session_id`.

## HTTP API (`mixassist/api.py`, port 8000, CORS for http://127.0.0.1:5173 and http://localhost:5173)

All bodies are JSON. Tool errors come back with HTTP 200 and the §3.1 `{"error":{...}}` body so the UI can show them; malformed HTTP requests get 422.

| Method + path | Body | Returns |
| --- | --- | --- |
| `GET /api/status` | – | `{fl_backend:"fake"\|"bridge", fl_connected:bool, llm_provider:"vertex"\|"aistudio"\|"ollama", model:str, model_available:bool\|null}` |
| `GET /api/mix` | – | `fl_get_mix_overview` output (`?include_empty=true` optional) |
| `POST /api/chat` | `{session_id, message}` | `{reply, tool_trace:[{tool,args,result}], proposals:[Proposal], needs_clarification:bool, question?:str, options?:[str]}` |
| `POST /api/proposals/{id}/apply` | – | `{proposal_id, tool, result}`; `result` is the write tool output (e.g. `change_id`, `verified`, `before`, `after`) or a §3.1 error |
| `POST /api/proposals/{id}/discard` | – | `{proposal_id, status:"discarded"}` |
| `POST /api/undo` | `{change_id?}` | `fl_undo` output |
| `POST /api/analyze/audio` | `{path, reference_path?}` | `analyze_audio` output |
| `POST /api/analyze/midi` | `{path}` | `analyze_midi` output |
| `POST /api/diagnose` | `{complaint, analysis_id, track?}` | `diagnose_complaint` output plus `proposals:[Proposal]` (registered server-side, so the UI can apply them) |
| `POST /api/verify` | `{complaint, before_analysis_id, after_path}` | `verify_fix` output |
| `POST /api/watch_peaks` | `{seconds?, from_bar?}` | `fl_watch_peaks` output (`suggestions` are also returned as `proposals:[Proposal]`) |
| `POST /api/midi/fix` | `{path, fix, key?, grid_beats?, strength_pct?}` | `{proposals:[Proposal]}`; fix_midi is a write and only runs on apply |
| `POST /api/midi/play` | `{path, channel?, record?}` | live: `fl_play_midi` output; record=true: `{proposals:[Proposal]}` |

Proposal shape:

```json
{
  "proposal_id": "p_ab12cd",
  "tool": "fl_set_track",
  "args": {"track": "Kick", "volume_change_db": -2},
  "why": "Kick down 2 dB as requested",
  "status": "pending",
  "preview": {
    "track": {"index": 1, "name": "Kick"},
    "changes": [{"field": "volume_db", "before": 0.0, "after": -2.0, "unit": "dB"}],
    "summary": "Kick: volume 0.0 dB -> -2.0 dB"
  }
}
```

`preview.track` is null for non-track tools (`fix_midi`, `fl_play_midi`, `fl_undo`); `preview.changes` may be empty, and `preview.summary` is always a human sentence. `before` comes from a fresh `fl_get_mix_overview` read when the proposal is created. For `fix_midi`, `preview.summary` names the fix and file, and the apply result includes `user_action`.

## UI changes (move the upstream files into `web/`)

- `git mv` `index.html`, `package.json`, `src/`, `.env.example` (`VITE_API_BASE_URL=http://127.0.0.1:8000`) into `web/`. Keep the espresso and amber visual style exactly as it is.
- Split the upstream UI (`main.jsx` + `StudioTools.jsx`) into `App`, `SessionView`, `TrackInspector`, `AssistantView`, `ProposalPanel`, `AnalysisPanel`, and `api.js` (plus small components for the Mix-intelligence and MIDI tabs if useful).
- Replace the hardcoded seed tracks with `/api/mix`, refreshed after every apply or undo. Show `volume_db` as dB; format `pan_pct` as `C`, `L n`, or `R n`; show `stereo_width_pct`; add a 3-band EQ row (gain and frequency); use `plugins[].name` as the FX chain; show muted and solo; header tempo = `bpm`, track count = real number of tracks.
- Delete `makePlan`. Chat calls `/api/chat`. Render clarification questions as option chips that send the chosen option back. Show the tool trace in a collapsible "what I measured" list.
- ProposalPanel shows backend proposals: track, before value, and target value from `preview`. "Approve & apply" calls the apply endpoint and shows `verified` and the readback. "Undo last plan" calls `/api/undo` with the stored `change_id`. Keep the activity log.
- For `fix_midi` results show the `user_action` text ("Scripting → MCP Apply, then Ctrl+Alt+Y").
- AnalysisPanel: absolute-path text inputs for a mix WAV, optional reference WAV, and a MIDI file (browsers can't give real paths, so drop the file pickers). Show LUFS, true peak, LRA, crest, correlation, a 7-band table, and the reference diff. Diagnose a complaint and verify an after-file using the real endpoints. The MIDI view shows key, out-of-key notes, and flags, and offers fixes as proposals.
- Replace every "REAPER", "simulated", "DEMO", "demo data", "illustrative", and "No DAW connected" string with live status from `/api/status` (e.g. "FakeFL" or "FL connected"), and REAPER plugin names are gone with the seed data.
- Replace the decorative arrangement clips with a neutral per-track level bar (the API provides no clip data).

## Build order (spec §9 still applies; this replaces its W4 column)

1. Scaffold: pyproject, the `web/` move, FastAPI with stub tools, UI rendering stub data through `/api`.
2. `fl/fake.py` with every quirk from §3.4, then F1 and F2. Tracer milestone: "turn the kick down 2 dB" goes through Gemma, becomes a proposal, gets approved, FakeFL readback shows −2.0 ±0.25 dB, and undo restores it.
3. `fixtures/make.py`, then F4 (EBU fixtures), then F5 and F6.
4. F3 on FakeFL with the peak script.
5. F7 (analysis, fixers, pyscript generator, fake `flpianoroll` test).
6. F8 scheduler on FakeFL.
7. `device_MixAssist.py`, `bridge.py`, `internal/probes.md`.
8. Evals: `evals/gemma_suite.py` calls the provider abstraction (temperature 0). Keep §8.2 pass criteria (≥16/20, 0 invented tools, 0 writes after a clarification). Record results for each model ID that is available (Vertex `gemma-4-26b-a4b-it-maas`, Ollama cloud `gemma4:31b-cloud`; no local Ollama models). Check MCP Inspector-equivalent: `npx @modelcontextprotocol/inspector --cli python -m mixassist --fake --method tools/list` (and a tools/call), or an equivalent scripted MCP client test.

Cut order if time runs out: spec §9. The UI analysis panel can be cut before F6. F6 is never cut.

## Definition of done

- `pytest` passes, including every acceptance criterion in spec §6 and every fixture expectation in §7, on FakeFL and fixtures.
- `python -m mixassist --fake` serves MCP. `uvicorn mixassist.api:app` and `npm run dev` in `web/` show the live FakeFL mix.
- The tracer flow and the demo "my mix is too bright" (analyze `bright.wav` against `pink.wav`, diagnose, approve the high-shelf cut, verify against `bright_fixed.wav`) both work end to end with real Gemma calls. Save transcripts to `evals/demos/`.
- `evals/report.md` contains the suite score, the model IDs used, and the probe checklist status (real-FL items marked "not run").
- README covers Vertex setup (`gcloud auth application-default login`, enabling the Vertex AI API, env vars), AI Studio (`GEMINI_API_KEY`), Ollama fallback, IAC Driver/loopMIDI, and installing the FL device script.

## Rules

- Spec §2–§3 contracts are frozen. If the UI needs more, extend the HTTP API, not the tools.
- Don't invent probe results or real-FL behavior. Anything unverified is marked PROBE.
- Commit in small, logical steps on `main`. Two workers share this working tree: only `git add` your own paths (never `git add -A` / `git add .`), and retry if `.git/index.lock` exists. Do not push.
- Shell commands that write `.venv`, `.git/config`, or call the network for Vertex/Ollama need `required_permissions: ["all"]` (the sandbox blocks them otherwise).
- Python env: `.venv/` (Python 3.12) already has all deps installed; use `.venv/bin/python` / `uv pip install --python .venv/bin/python ...`.
