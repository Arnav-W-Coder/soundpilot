# MixAssist v1 evaluation report

Date: Oct 25, 2026. Machine: macOS (this repo's dev Mac). FL backend for every run here: **FakeFL** (`mixassist/fl/fake.py`). Nothing in this report was run on real FL Studio.

## Summary

| Check | Result |
| --- | --- |
| Gemma tool-call suite, Vertex `gemma-4-26b-a4b-it-maas` (location `global`) | **18/20**, 0 invented tools, 0 writes after clarification: **PASS** |
| Gemma tool-call suite, Ollama cloud `gemma4:31b-cloud` | **20/20**, 0 invented tools, 0 writes after clarification: **PASS** |
| Gemma tool-call suite, AI Studio `gemma-4-26b-a4b-it` (free tier) | **16/20**, 0 invented tools, 0 writes after clarification: **PASS**. All 4 misses were free-tier `429` quota errors, not wrong calls. The 4 passed on a re-run. |
| MCP Inspector CLI (`tools/list`, `tools/call`) | 10 tools listed; valid call returns readback; invalid call returns the §3.1 error with `isError: true` |
| Scripted MCP client over stdio (`tests/test_mcp_server.py`) | 23 passed: 10 tools, schemas validate, one valid and one invalid call per tool |
| Tracer demo ("turn the kick down 2 dB" → proposal → apply → readback → undo) | PASS on Vertex, Ollama cloud and AI Studio |
| "My mix is too bright" demo (analyze → diagnose → apply high shelf → verify) | PASS on Vertex, Ollama cloud and AI Studio |
| `pytest` (all workers) | 492 passed, 8 skipped (the skips are real-FL PROBE tests) |
| Real-FL probes P1–P8, §8.3, §8.4 | **not run** (see below) |

## Gemma tool-call suite (spec §8.2, adapted)

Harness: [`evals/gemma_suite.py`](gemma_suite.py). Each of the 20 prompts runs one turn of the real agent loop (`mixassist/agent.py`, temperature 0) with a fresh session and a fresh FakeFL: read tools execute, write tools become proposals, and nothing is applied. The model sees the 10 converted §3.2 schemas (Gemini-safe: no `oneOf`/`exclusiveMinimum`, track reference as a string).

The feasibility doc's prompt list (§4 item 7) wasn't available, so the 20 prompts were written for this suite. They cover all 10 tools, two clarification cases, and a "no file, vague complaint" case:

| # | Prompt (paths shortened) | Expected |
| --- | --- | --- |
| 1 | What's on my mixer? | `fl_get_mix_overview` |
| 2 | Turn the kick down 2 dB | `fl_set_track` Kick, −2 dB |
| 3 | Pan the snare 30% to the left | `fl_set_track` Snare, `pan_pct` −30 |
| 4 | Set the bass fader to exactly −6 dB | `fl_set_track` Bass, `volume_db` −6 |
| 5 | Cut 3 dB of low end on the Bass with the built-in EQ at 120 Hz | `fl_set_track` `eq_band` low, `eq_gain_db` −3 |
| 6 | Undo that last change (after a seeded change) | `fl_undo` |
| 7 | Is anything clipping? Listen for 10 seconds. | `fl_watch_peaks` 10 s |
| 8 | Analyze my mix at pink.wav | `analyze_audio` path |
| 9 | How loud is bright.wav compared with my reference pink.wav? | `analyze_audio` path + reference |
| 10 | My mix bright.wav is too bright compared to pink.wav | `diagnose_complaint` "bright" + analysis_id |
| 11 | The export at muddy.wav sounds boxy | `diagnose_complaint` |
| 12 | My mix at pink.wav sounds bad. Fix it. | clarification via `diagnose_complaint`, no proposals |
| 13 | The whole thing just sounds off. Make it better. (no file) | no write proposed |
| 14 | Exported bright.wav, re-exported bright_fixed.wav. Did the fix work? | `verify_fix` after_path + before_analysis_id |
| 15 | Does melody_c_major_3wrong.mid have wrong notes? | `analyze_midi` |
| 16 | Fix the out-of-key notes in melody_c_major_3wrong.mid | `fix_midi` snap_to_scale |
| 17 | Quantize sloppy.mid to 16th notes | `fix_midi` quantize, `grid_beats` 0.25 |
| 18 | robotic.mid sounds robotic and stiff, make it feel more human | `fix_midi` humanize |
| 19 | Play scale_8.mid on my selected synth | `fl_play_midi` live |
| 20 | Record scale_8.mid into the piano roll starting at bar 5 | `fl_play_midi` record, `start_bar` 5 |

### Results (final run)

| Model | Provider | Score | Invented tools | Writes after clarification | Text-call fallbacks | Time | Raw results |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `gemma-4-26b-a4b-it-maas` | Vertex AI, project `YOUR_PROJECT_ID`, location `global` | 18/20 | 0 | 0 | 0 | 29 s | [json](results/gemma_suite-vertex-gemma-4-26b-a4b-it-maas.json) |
| `gemma4:31b-cloud` | Ollama (cloud model, `ollama signin`) | 20/20 | 0 | 0 | 0 | 29 s | [json](results/gemma_suite-ollama-gemma4_31b-cloud.json) |
| `gemma-4-26b-a4b-it` | AI Studio (`GEMINI_API_KEY` from `.env`, free tier) | 16/20 | 0 | 0 | 0 | 349 s | [json](results/gemma_suite-aistudio-gemma-4-26b-a4b-it.json) |

Vertex misses: prompts 16 and 18. In both, Gemma called `analyze_midi` first, then asked "Would you like me to snap them to the scale?" instead of proposing `fix_midi`. That isn't unsafe, but it doesn't meet the predicate.

AI Studio misses: prompts 6, 10, 13 and 19. None was a model error. Each ended in `429 RESOURCE_EXHAUSTED` because the backoff (1, 2, 4, 8, 16 s, about 31 s in total) ran out before the free-tier quota window reset. The quota is `generate_content_free_tier_input_token_count`, 16,000 input tokens per minute for `gemma-4-26b`. Each agent call sends the instructions plus the 10 tool schemas, and the server asked for a ~42 s wait. After a 65 s pause, `--only 6 10 13 19` passed all 4: `fl_undo`, `diagnose_complaint` "too bright" with the analysis_id, no write for the vague prompt, and `fl_play_midi` live. The suite's 429s add most of the 349 s runtime. On AI Studio, Gemma passed prompts 16 and 18 (`fix_midi` snap_to_scale and humanize), which Vertex missed. The listed model `gemma-4-31b-it` wasn't evaluated. A paid tier, or a backoff that honors the server's retry delay, would avoid the misses.

Earlier run, before tool descriptions were tightened: Vertex scored 16/20 and Ollama cloud 19/20. On Vertex, Gemma sent `volume_change_db` for an EQ cut (prompt 5), omitted `grid_beats` for "16th notes" (17), and omitted `start_bar` for "bar 5" (20). Ollama returned one empty response (prompt 4) that didn't reproduce. Fixes between the two runs:

- The `fl_set_track`, `fix_midi` and `fl_play_midi` descriptions now spell out those argument meanings, and the analysis tools list their required args.
- The agent retries once on an empty model response.
- The dispatcher's "missing required argument" error now lists the required args.

The prompts and predicates didn't change between runs. Vertex returned frequent `429 RESOURCE_EXHAUSTED`, and the provider's exponential backoff (1, 2, 4, 8, 16 s) handled it in every run. Other models: no local Ollama model was evaluated. Per the user's directive, Ollama uses only the cloud model `gemma4:31b-cloud`.

## Demos with real Gemma (through the HTTP API)

Harness: [`evals/run_demo.py`](run_demo.py). It starts `uvicorn mixassist.api:app` on a free port with FakeFL and calls the same `/api/*` endpoints the UI uses. Transcripts (full JSON plus readable markdown) are in [`evals/demos/`](demos/).

| Demo | Vertex `gemma-4-26b-a4b-it-maas` | Ollama `gemma4:31b-cloud` | AI Studio `gemma-4-26b-a4b-it` |
| --- | --- | --- | --- |
| Tracer: "turn the kick down 2 dB" → `fl_set_track` proposal (`Kick: volume 0.0 dB -> -2.0 dB`) → FL unchanged before approval → apply → readback −2.0 dB, verified → "undo that" → `fl_undo` proposal → apply → 0.0 dB | PASS ([md](demos/tracer-vertex.md)) | PASS ([md](demos/tracer-ollama.md)) | PASS ([md](demos/tracer-aistudio.md)) |
| Bright: "my mix is too bright" with bright.wav and pink.wav → `analyze_audio` → `diagnose_complaint` (top finding bright, +5.4 dB at 5–20 kHz) → proposal `Master: high EQ gain 0.0 dB -> -5.4 dB` at 8000 Hz → apply (verified) → "did the fix work?" with bright_fixed.wav → `verify_fix` passed | PASS ([md](demos/bright-vertex.md)) | PASS ([md](demos/bright-ollama.md)) | PASS ([md](demos/bright-aistudio.md)) |

On AI Studio, the tracer took 11 s and bright 50 s. Bright hit five 429s in a row, and the last retry got through. Neither demo needed a fallback step.

During development, Vertex Gemma once dropped a required argument (`complaint`) and repeated the same failing `verify_fix` call until it hit the 6-call cap. The agent now answers an identical repeat of a failed call with a pointed error instead of re-running it, and the descriptions name the required args. The committed transcripts are from runs after those fixes, with no fallback steps.

## Web access (agent-only `web_search` / `web_fetch`)

Harness: [`evals/web_demo.py`](web_demo.py). Same setup as the demos above: the real HTTP API, FakeFL, live web search with the default keyless `ddgs` backend, and no provider fallback. Two prompts, each in a fresh session: "What key and tempo is 'Blinding Lights' by The Weeknd? Then play its main synth riff idea as a short arpeggio" and "What loudness does Spotify normalize to?".

| Check | Vertex `gemma-4-26b-a4b-it-maas` | Ollama `gemma4:31b-cloud` |
| --- | --- | --- |
| Key/tempo: `web_search` → live `fl_play_midi` arpeggio, all notes in the stated key, no proposals, ≤ 6 calls; reply states key and BPM, cites a site that actually gives the key | PASS, 7.4 s: "C# major at 171 BPM (Tunebat, songbpm.com)" | PASS, 8.1 s: "C# major at 171 BPM (Tunebat, songbpm.com)", notes 61-65-68-72 |
| Loudness: `web_search` → "-14 LUFS" with a source | PASS, 6.3 s: "-14 LUFS ... (Spotify Support, ...)", also Quiet = -19 LUFS | PASS, 5.7 s: "-14 LUFS (Loudness normalization on Spotify)" |

Transcripts: [web-vertex.md](demos/web-vertex.md), [web-ollama.md](demos/web-ollama.md) (full JSON next to them).

What earlier runs showed, and what was fixed:

- Tunebat's snippet says "C♯ major", and Vertex Gemma reported "C major": it dropped the Unicode sharp. Web text sent to the model now spells accidentals in ASCII (`C#`, `Bb`). The next run said "C#/Db major".
- Once, the final reply described only the arpeggio and left out the key and BPM it had looked up. The prompt rule now says the final reply must state each looked-up fact with the site that gives it.
- In one Ollama run, no search snippet stated the key (SongBPM renders it with JavaScript), yet the reply said "C# Major (SongBPM)". That answer came from the model, not the source. The harness now checks that the stated key appears in some source text. Sources and memory also disagree: Tunebat and SongBPM say C# major, while Gemma answering without search (the grounding probe below) said F minor. Treat a looked-up key as a hint.
- Keyless search is flaky from this machine. DuckDuckGo itself answered "no results" to every query, and single engines sometimes return nothing. `ddgs` shuffles engines, so the backend retries an empty answer within the 8 s budget. An `OLLAMA_API_KEY`, `BRAVE_API_KEY` or `TAVILY_API_KEY` makes search more reliable.

Regression check with web tools on: `evals/gemma_suite.py --llm vertex` scored **18/20, 0 invented tools, 0 writes after clarification** (PASS). That's the same score as the committed baseline. No suite prompt called a web tool. Results: [results/gemma_suite-vertex-gemma-4-26b-a4b-it-maas-web.json](results/gemma_suite-vertex-gemma-4-26b-a4b-it-maas-web.json). The failures were prompt 5 (an EQ frequency without a gain) and prompt 16 (`analyze_midi` instead of `fix_midi`).

Google Search grounding (`types.Tool(google_search=types.GoogleSearch())`), probed on Oct 25, 2026:

- Vertex `gemma-4-26b-a4b-it-maas`: the request succeeds, but the tool is silently ignored. There's no grounding metadata, and the model answers from memory, alone or alongside function declarations.
- AI Studio `gemma-4-26b-a4b-it`: it grounds when used alone. With function declarations it returns 400 unless `tool_config.include_server_side_tool_invocations=true`. That flag is AI Studio only (the SDK refuses it on Vertex). With the flag set, it worked, but it returned intermittent 500s.
- AI Studio `gemma-4-31b-it`: returned 500.
- Ollama: no equivalent.

So grounding is not used. The agent's own `web_search` works the same on all three providers, shows up in the trace and goes through the URL and untrusted-content guards.

## MCP checks (spec §8.1)

MCP Inspector CLI (`@modelcontextprotocol/inspector`, run via `npx -y`):

```bash
npx -y @modelcontextprotocol/inspector --cli .venv/bin/python -m mixassist --fake --method tools/list
```

This lists all 10 tools with their verbatim §3.2 input schemas; the output is saved in [results/mcp_inspector_tools_list.json](results/mcp_inspector_tools_list.json). Two calls were also made:

- `--method tools/call --tool-name fl_set_track --tool-arg track=Kick --tool-arg volume_change_db=-2` returned `isError: false` with `after.volume_db = -2`, `verified: true`, plus `structuredContent`.
- `--tool-arg volume_db=9` returned `isError: true` and the text `{"error": {"code": "OUT_OF_RANGE", "message": "fl_set_track: volume_db: 9 is greater than the maximum of 5", ...}}`. The server didn't crash.

The scripted MCP client is `tests/test_mcp_server.py`, using the official `mcp` SDK. It spawns `python -m mixassist --fake` over stdio, lists the tools, checks every schema with `Draft202012Validator.check_schema`, and calls each tool once with valid and once with invalid arguments, plus an unknown tool. Every invalid call returns a §3.1 error with `isError: true`. 23 passed.

## Real-FL probe checklist status

Source: [internal/probes.md](../internal/probes.md). None of these has been run. They need a person with FL Studio and the IAC Driver or loopMIDI ports set up.

| Check | Status |
| --- | --- |
| P1 script loads, hello < 100 ms, api ≥ 35 | not run |
| P2 1024-byte reply intact | not run |
| P3 fader curve monotonic, 0.8 → 0.0 ±0.1 dB | not run (volume table in `mixassist/fl/curves.py` is a placeholder) |
| P4 EQ 3 bands, low < mid < high Hz | not run (EQ tables are placeholders) |
| P5 next-op readback | not run (FakeFL models a 1-op delay; the client syncs before reading back) |
| P6 peaks > 0 while playing, ≥ 20 polls/s × 32 | not run |
| P7 MCP_Apply then Ctrl+Alt+Y re-run | not run |
| P8 notes reach the selected channel and record (focused / unfocused) | not run |
| §8.3 F1 volume_db vs mixer hint ±0.1 dB on 3 tracks | not run |
| §8.3 F2 −2 dB ±0.25, undo ±0.1 dB | not run |
| §8.3 F8 recorded onsets p95 ±20 ms | not run |
| §8.4 Youlean ±0.3 LU / ±0.3 dBTP on 3 mixes | not run |
| §8 blind listening test (≥ 5 producers) | not run |

## How to reproduce

```bash
.venv/bin/pytest -q
.venv/bin/python -m evals.gemma_suite --llm vertex
.venv/bin/python -m evals.gemma_suite --llm ollama            # gemma4:31b-cloud
.venv/bin/python -m evals.gemma_suite --llm aistudio          # GEMINI_API_KEY from the git-ignored .env
.venv/bin/python -m evals.run_demo tracer --llm vertex        # or --llm ollama / aistudio; also: bright
```
