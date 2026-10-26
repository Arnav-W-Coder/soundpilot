# SoundPilot x MixAssist

SoundPilot is an AI mixing assistant for FL Studio. You describe what you hear ("turn the kick down 2 dB", "my mix is too bright"). Gemma 4 measures the mix through 10 MixAssist tools and proposes concrete changes. Nothing is written to FL until you approve a change in the SoundPilot UI. Every write is then read back from FL and checked.

- **Backend** (`mixassist/`, Python 3.11+): the 10 MCP tools from the [MixAssist v1 spec](docs/mixassist-v1-spec.md), an MCP stdio server, a FastAPI HTTP API, and a Gemma agent loop.
- **FL bridge**: the SysEx device script `fl_script/MixAssist/device_MixAssist.py`, connected over two virtual MIDI ports. **FakeFL** is an in-memory stand-in used by default and by every test.
- **Frontend** (`web/`): the React UI. It shows the live mixer and turns the agent's proposals into approve/undo cards.

Design docs: [build brief](docs/build-brief.md) (integration decisions, HTTP API), [spec](docs/mixassist-v1-spec.md) (frozen tool contracts), [interfaces](docs/interfaces.md) (internal seams). Eval results: [evals/report.md](evals/report.md).

> **Status:** everything has been tested against FakeFL and synthesized fixtures. Nothing has been run on real FL Studio yet. Real-FL behavior (fader curve, EQ tables, SysEx size limit, MIDI routing) is marked PROBE in the code and checked by the human-run checklist in [internal/probes.md](internal/probes.md).

## Tools

| Tool | What it does | Write? |
| --- | --- | --- |
| `fl_get_mix_overview` | tempo, and each used track's level (dB), pan, width, 3-band EQ and plugins | read |
| `fl_set_track` | change one track's volume, pan, width or built-in EQ, with readback | **write** |
| `fl_undo` | undo a `fl_set_track` change | **write** |
| `fl_watch_peaks` | play, poll peak meters, report clipping and suggest trims | read |
| `analyze_audio` | LUFS, LRA, true peak, crest, correlation and bands of an exported WAV, with an optional reference | read |
| `diagnose_complaint` | turn "too bright" or "muddy" into measured findings and proposed changes, or a clarification question | read |
| `verify_fix` | pass/fail on a re-exported WAV against the earlier analysis | read |
| `analyze_midi` | key, out-of-key notes, timing and velocity of an exported .mid | read |
| `fix_midi` | snap to scale, quantize or humanize; writes a .mid and a piano-roll `MCP_Apply.pyscript` | **write** |
| `fl_play_midi` | play a .mid or notes on the selected instrument (live), or record it into the piano roll (`record=true`) | **write** when recording |

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q                # FakeFL + generated fixtures; no FL, no network
```

The test session generates the audio fixtures (`fixtures/audio/*.wav`, git-ignored) on first run. To generate them by hand, run `.venv/bin/python -m fixtures.make`. The MIDI fixtures in `fixtures/midi/` are committed.

## Choosing the LLM

`SOUNDPILOT_LLM` picks the provider and `SOUNDPILOT_MODEL` the model ID. The server reads both from its own environment. Credentials stay server-side and never go into Vite env vars.

**`.env`:** copy [`.env.example`](.env.example) to `.env` at the repo root and fill it in. `.env` is git-ignored. The API (`uvicorn mixassist.api:app`), `python -m mixassist` and the evals load it at startup. They only set variables that aren't already in the environment, so a shell `export` always wins.

| `SOUNDPILOT_LLM` | Default `SOUNDPILOT_MODEL` | Needs |
| --- | --- | --- |
| `vertex` (default) | `gemma-4-26b-a4b-it-maas` | Google Cloud project with Vertex AI, ADC, location `global` |
| `aistudio` | `gemma-4-26b-a4b-it` (also listed: `gemma-4-31b-it`) | `GEMINI_API_KEY` from [AI Studio](https://aistudio.google.com/apikey) |
| `ollama` | `gemma4:31b-cloud` | Ollama signed in to its cloud (`ollama signin`); runs on Ollama's servers, no local download |

At startup the API checks the configured model and reports the result in `GET /api/status` (`model_available`, `model_message`). Vertex doesn't list Gemma MaaS models, so the check falls back to a 1-token probe. If the model is missing, the message suggests the IDs above.

### Vertex AI (default)

```bash
gcloud auth application-default login
gcloud services enable aiplatform.googleapis.com
export GOOGLE_CLOUD_PROJECT=your-project      # default: `gcloud config get-value project`
export GOOGLE_CLOUD_LOCATION=global           # the Gemma 4 MaaS model only works at `global`
export SOUNDPILOT_LLM=vertex SOUNDPILOT_MODEL=gemma-4-26b-a4b-it-maas
```

Vertex often answers `429 RESOURCE_EXHAUSTED` for this model. The provider retries 429 and 503 with exponential backoff (1, 2, 4, 8, 16 s) before it gives up.

### AI Studio

Put `GEMINI_API_KEY=...` in `.env`, then pick the provider:

```bash
export SOUNDPILOT_LLM=aistudio      # model gemma-4-26b-a4b-it; or SOUNDPILOT_MODEL=gemma-4-31b-it
```

AI Studio keys may start with `AQ.`. The provider passes `GEMINI_API_KEY` to the client explicitly, so an unrelated `GOOGLE_API_KEY` in the shell is ignored. The free tier answers `429` under load, and the same backoff as Vertex handles it.

### Ollama cloud (fallback)

Use the cloud model `gemma4:31b-cloud`. It runs on Ollama's servers, so it needs no local RAM or GPU. Don't substitute a local model.

```bash
ollama signin                       # once; cloud models need an ollama.com account
ollama pull gemma4:31b-cloud        # registers the cloud model (a few hundred bytes, no weights)
ollama serve                        # if the Ollama app isn't already running
export SOUNDPILOT_LLM=ollama        # SOUNDPILOT_MODEL defaults to gemma4:31b-cloud
# OLLAMA_HOST defaults to http://127.0.0.1:11434
```

### Speed and rate limits

Vertex Gemma often answers `429 RESOURCE_EXHAUSTED`, and AI Studio's free tier allows 16k input tokens per minute. Retries wait as long as the server asks (RetryInfo, `Retry-After`, or "retry in N s"), up to 60 s. Without a server hint they back off 1, 2, 4, 8 and 16 s. To avoid long waits, add a fallback provider:

```bash
export SOUNDPILOT_LLM=vertex SOUNDPILOT_LLM_FALLBACK=ollama   # recommended: switch to gemma4:31b-cloud on 429s
```

| Variable | Default | Effect |
| --- | --- | --- |
| `SOUNDPILOT_LLM_FALLBACK` | none | Providers to switch to (in order, e.g. `ollama` or `ollama,aistudio`) when the primary stays rate limited or unreachable. The switch is logged and shown in the chat. |
| `SOUNDPILOT_LLM_FALLBACK_AFTER_S` | `4` | With a fallback set, how many seconds the primary may spend waiting on retries before the turn moves on. |
| `SOUNDPILOT_LLM_FALLBACK_COOLDOWN_S` | `120` | How long calls stay on the fallback before the primary is tried again. |
| `SOUNDPILOT_THINKING` | `minimal` on AI Studio, `default` on Vertex | On AI Studio, `minimal` sends `thinking_level=MINIMAL`; otherwise Gemma 4 thinks for 50–450 tokens per call. Vertex MaaS reports no thinking, so it is left alone unless set to `minimal`. Ollama always sends `think: false`. |
| `SOUNDPILOT_MAX_OUTPUT_TOKENS` | `2048` | Cap on generated tokens per call. It stops runaway replies; tool calls are far shorter. |
| `SOUNDPILOT_TOOL_REPLIES` | `1` | If a turn ends with proposals, a clarification question, a peak report or a live playback, the reply is built from the tool results and the summary LLM call is skipped. Set `0` to always ask the model. |
| `SOUNDPILOT_FASTPATH` | `0` | Set `1` to turn "turn X down N dB", "set X to N dB" and "pan X N% left" into the same pending proposal without any LLM call. Anything ambiguous goes to the model. |

`.venv/bin/python -m evals.latency_bench --llm vertex --label mine` times six representative turns. It reports wall time, LLM calls, LLM time vs tool time vs retry waits, tokens, and FL round trips. Results are in [evals/report.md](evals/report.md#latency).

## Running

### Backend HTTP API (for the UI)

```bash
.venv/bin/uvicorn mixassist.api:app --port 8000                 # FakeFL (default)
SOUNDPILOT_FL=bridge .venv/bin/uvicorn mixassist.api:app --port 8000   # real FL over the bridge
```

`SOUNDPILOT_FL` chooses the FL backend: `fake` (default) or `bridge`. Running `.venv/bin/python -m mixassist --http [--bridge]` starts the API too.

The terminal logs one line per chat event. Each line carries a tag `[s=<session>#<turn> +<elapsed>s]` and covers: chat start and end, each LLM call, provider retries with the wait before the next try, each tool call with compact arguments and result, and proposals being created, applied or discarded. A turn that keeps printing retry lines is waiting on quota, not stuck. Set `SOUNDPILOT_LOG=DEBUG` or `WARNING` to change the level (default `INFO`).

### Frontend

`cd web && npm install && npm run dev`, then open http://127.0.0.1:5173. Start the API first with `.venv/bin/uvicorn mixassist.api:app --port 8000`. Set the API URL with `VITE_API_BASE_URL` (see `web/.env.example`). File inputs take absolute paths on the server machine. The dev server is pinned to port 5173 because that's the origin CORS allows.

### MCP server (stdio)

```bash
.venv/bin/python -m mixassist --fake      # FakeFL
.venv/bin/python -m mixassist --bridge    # real FL via the SysEx bridge
npx -y @modelcontextprotocol/inspector --cli .venv/bin/python -m mixassist --fake --method tools/list
```

Tool errors come back as MCP results with `isError: true` and the spec §3.1 JSON (`{"error": {"code", "message", "candidates?", "hint?"}}`). MCP clients see the write tools directly; human approval happens only in the SoundPilot UI.

## Connecting real FL Studio

Run every step from a normal Terminal; the Cursor agent sandbox blocks CoreMIDI. [internal/probes.md](internal/probes.md) has the full walkthrough and the probe checklist (P1–P8).

1. **Virtual MIDI ports.**
   - macOS: open Audio MIDI Setup > Window > Show MIDI Studio > IAC Driver. Tick "Device is online" and add two ports named exactly `MA to FL` and `MA from FL`.
   - Windows: in loopMIDI, create ports `MA to FL` and `MA from FL`. Port names are matched by substring, so `IAC Driver MA to FL` and `MA to FL 1` both work.
2. **Install the device script.** Copy it into FL's user hardware folder:

   ```bash
   mkdir -p ~/Documents/Image-Line/FL\ Studio/Settings/Hardware/MixAssist
   cp fl_script/MixAssist/device_MixAssist.py ~/Documents/Image-Line/FL\ Studio/Settings/Hardware/MixAssist/
   ```

   On Windows the folder is `%USERPROFILE%\Documents\Image-Line\FL Studio\Settings\Hardware\MixAssist\`.
3. **Enable it in FL.** Open Options > MIDI Settings.
   - Input `MA to FL`: Enable, controller type `MixAssist`, port 10.
   - Output `MA from FL`: port 10.
   - Leave input `MA from FL` disabled.
   - Check View > Script output for `MixAssist: loaded`.
4. **Check the link.** List the ports and send one `hello` round trip:

   ```bash
   .venv/bin/python -m mixassist.fl.bridge --list
   .venv/bin/python -m mixassist.fl.bridge --check
   ```

   To check you can hear notes without Gemma, select an instrument in the Channel Rack and run `.venv/bin/python -m mixassist.fl.bridge --test-note`. It plays middle C for 1 s and prints FL's tempo. After updating the repo, restart the backend and re-copy the device script.

5. Run the probes in [internal/probes.md](internal/probes.md) and paste the measured fader and EQ tables into `mixassist/fl/curves.py`. Then start the API or MCP server with the bridge backend.

`fix_midi` writes `MCP_Apply.pyscript` to `~/Documents/Image-Line/FL Studio/Settings/Piano roll scripts/`, or next to the .mid if that folder is missing. Set `SOUNDPILOT_PYSCRIPT_DIR` to override the location. To apply it in FL, open the channel's piano roll and choose Scripting > MCP Apply. After the first time in a session, press Ctrl+Alt+Y (PROBE P7).

## HTTP API (summary)

The full contract is in the [build brief](docs/build-brief.md) (section "HTTP API"). Tool errors come back as HTTP 200 with the §3.1 body. Malformed requests get 422. LLM failures on `/api/chat` get 502 with `{"error": {"code": "LLM_ERROR", ...}}`.

`GET /api/status`, `GET /api/mix`, `POST /api/chat`, `POST /api/chat/stream`, `POST /api/proposals/{id}/apply`, `POST /api/proposals/{id}/discard`, `GET /api/proposals`, `POST /api/undo`, `POST /api/analyze/audio`, `POST /api/analyze/midi`, `POST /api/diagnose`, `POST /api/verify`, `POST /api/watch_peaks`, `POST /api/midi/fix`, `POST /api/midi/play`.

`POST /api/chat/stream` takes the same body as `/api/chat` and answers with server-sent events. While the turn runs, it sends `step` events (`{"type": "llm"|"tool"|"retry"|"fallback"|"fastpath", "text": ...}`). Then it sends one `result` event with the `/api/chat` payload, or an `error` event. The UI uses it to show what the assistant is doing, including rate-limit waits.

Write calls from the agent become **proposals**:

```json
{"proposal_id": "p_ab12cd", "tool": "fl_set_track", "args": {"track": "Kick", "volume_change_db": -2},
 "why": "You asked: \"turn the kick down 2 dB\"", "status": "pending",
 "preview": {"track": {"index": 1, "name": "Kick"},
             "changes": [{"field": "volume_db", "before": 0.0, "after": -2.0, "unit": "dB"}],
             "summary": "Kick: volume 0.0 dB -> -2.0 dB"}}
```

## Agent rules

The agent loop lives in `mixassist/agent.py`. Read tools run automatically. These calls become proposals instead of running: `fl_set_track`, `fl_undo`, `fix_midi`, and `fl_play_midi` with `record=true`. `diagnose_complaint.proposed_changes` and `fl_watch_peaks` suggestions become proposals too.

- Each turn is capped at 6 tool calls.
- Tool calls the model writes as text are parsed as a fallback, and the fallback is logged.
- Schema-validation errors go back to the model so it can retry.
- When `diagnose_complaint` asks a clarification question, the agent proposes no writes.
- If a single request ends in proposals or a question, the reply comes from the tool results and the model isn't asked to summarize. Questions, errors and multi-part requests still go back to the model.
- Sessions live in memory, keyed by `session_id`.

## Web access (agent-only tools)

The assistant can look things up: a song's key, tempo or chords ("what key and BPM is Blinding Lights?", "play the melody of …"), how a record was mixed, or platform loudness targets ("what LUFS does Spotify normalize to?"). Two read tools exist only in the agent ([`mixassist/agent_tools.py`](mixassist/agent_tools.py), [`mixassist/web/`](mixassist/web/)). The MCP server still lists exactly the 10 spec tools.

| Tool | Arguments | Returns |
| --- | --- | --- |
| `web_search` | `query`, `max_results` 1–5 (default 4) | `{query, backend, results: [{title, url, snippet}]}` |
| `web_fetch` | `url`, `max_chars` 200–6000 (default 3000) | `{url, title, text, truncated}`: readable text with scripts, styles, navigation, headers, footers and forms stripped |

Like the other read tools, they run without approval, count toward the 6-call cap, and are cached per turn. The UI lists their sources as links under "What I measured & looked up" and turns URLs in replies into links.

| Variable | Default | Meaning |
| --- | --- | --- |
| `SOUNDPILOT_WEB` | `1` | `0` removes both tools and the prompt rule |
| `SOUNDPILOT_WEB_BACKEND` | `auto` | `ddgs` (keyless metasearch via the `ddgs` package), `ollama`, `brave`, `tavily`. `auto` picks the first key it finds, else `ddgs` |
| `OLLAMA_API_KEY` | – | [Ollama web search API](https://docs.ollama.com/capabilities/web-search) (free account). It also powers a remote fetch fallback when a site blocks the direct fetch |
| `BRAVE_API_KEY` / `TAVILY_API_KEY` | – | Brave Search API / Tavily |

Keyless search works but is flaky: engines sometimes answer "no results" to scrapers, so the backend retries within the time budget. A keyed backend fails over to `ddgs`. Searches and fetches time out after 8 s.

Safety:

- Web text is untrusted. Results reach the model between `<<<UNTRUSTED_WEB_CONTENT` markers, with a note to ignore any instructions inside, and the system prompt says a page can never trigger a tool call. Writes still only become proposals that you approve.
- `web_fetch` only fetches `http(s)` on ports 80 and 443. It refuses credentials in URLs, `localhost`, `*.local` and similar names, and any host that resolves to a private, loopback, link-local, CGNAT or reserved address, checked again on the connected socket.
- Redirects are followed by hand (at most 4), and each hop gets the same checks. Responses are capped at 1.5 MB and must be text.
- Search queries are stripped of absolute file paths, secret-looking tokens and the values of `*KEY*`, `*TOKEN*` and `*SECRET*` environment variables before they leave the machine.

Gemini-style Google Search grounding was tested and is not used. On Vertex, Gemma ignores it. On AI Studio, it can't be combined with function calling without an AI Studio-only flag, and that combination returned intermittent 500s. Details are in [evals/report.md](evals/report.md).

## Evals

```bash
.venv/bin/python -m evals.gemma_suite --llm vertex                  # 20 prompts, temperature 0, FakeFL
.venv/bin/python -m evals.gemma_suite --llm ollama                  # gemma4:31b-cloud
.venv/bin/python -m evals.gemma_suite --llm aistudio                # gemma-4-26b-a4b-it, GEMINI_API_KEY from .env
.venv/bin/python -m evals.run_demo tracer --llm vertex              # end-to-end through the HTTP API
.venv/bin/python -m evals.run_demo bright --llm ollama
.venv/bin/python -m evals.web_demo --llm vertex                     # live web search: song key/tempo + Spotify LUFS
SOUNDPILOT_LIVE_WEB=1 .venv/bin/pytest -q -m web                    # live smoke test of the default search backend
```

Scores and transcripts are in [evals/report.md](evals/report.md) and [evals/demos/](evals/demos/).

## Repository layout

```
mixassist/            backend package
  tools/              the 10 tools (fl_tools, audio_tools, midi_tools) + dispatcher (validation, coercion, §3.1 errors)
  fl/                 FLClient, SysEx codec, FakeFL, bridge, curve tables, track-name resolution
  analysis/ complaints/ midi/
  llm/                Vertex / AI Studio / Ollama providers, Gemini-safe schema converter
  web/                agent-only web_search / web_fetch (search backends, SSRF-guarded fetch)
  agent.py agent_tools.py api.py server.py
fl_script/MixAssist/  FL device script
fixtures/             fixture generators (+ committed MIDI)
tests/  evals/  internal/probes.md  docs/  web/
```
