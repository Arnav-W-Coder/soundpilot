# MixAssist v1 evaluation report

Date: Oct 25, 2026. Machine: macOS (this repo's dev Mac). FL backend for every run here: **FakeFL** (`mixassist/fl/fake.py`). Nothing in this report was run on real FL Studio.

## Summary

| Check | Result |
| --- | --- |
| Gemma tool-call suite, Vertex `gemma-4-26b-a4b-it-maas` (location `global`) | **18/20**, 0 invented tools, 0 writes after clarification: **PASS** |
| Gemma tool-call suite, Ollama cloud `gemma4:31b-cloud` | **20/20**, 0 invented tools, 0 writes after clarification: **PASS** |
| Gemma tool-call suite, AI Studio `gemma-4-26b-a4b-it` (free tier) | **16/20**, 0 invented tools, 0 writes after clarification: **PASS**. All 4 misses were free-tier `429` quota errors, not wrong calls. The 4 passed on a re-run. |
| Chat latency, six-turn benchmark on FakeFL ([Latency](#latency)) | Vertex 15.3 → 10.7 s, Ollama 15.2 → 7.7 s, AI Studio 102 s (one turn failed) → 77 s (no failures), or 10.9 s with fallback to Ollama |
| Theory/mix suite (`evals/theory_suite.py`, 10 prompts, 3 runs each) with the music theory reference in the prompt | Vertex mean 6.3 → **8.0/10**, Ollama cloud 6.3 → **7.7/10**; main suite unchanged or better (Vertex 18 → 19/20, Ollama 20/20) |
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

## Music theory and mixing reference in the system prompt

`mixassist/agent.py` now appends [`mixassist/knowledge/music_theory.md`](../mixassist/knowledge/music_theory.md) and [`mixing.md`](../mixassist/knowledge/mixing.md) (package data) to the system prompt, plus two rules: compute `fl_play_midi` notes from the reference with the first note at `start_beat` 0, live unless the user says record, and check every pitch and the note and repeat counts before calling; follow the mixing reference for track-specific mix requests. The reference covers MIDI numbers (C4 = 60, which FL's piano roll labels C5), scale, mode and chord formulas, diatonic chords, common progressions, inversions, arpeggio patterns, beat values, melody rules, ranges and velocities, plus instrument and problem frequency bands, level and loudness targets, panning, small relative moves, and the built-in EQ's limits. `tests/test_knowledge.py` re-derives every formula and pitch number in it from first principles.

Prompt cost: +3,688 characters (922 tokens at chars/4). Gemma's tokenizer splits digits one by one, so the real cost is higher: **+1,583 tokens** (Ollama `gemma4:31b-cloud` `prompt_eval_count`: 382 tokens for the old system prompt, 1,965 for the new one). The `fl_play_midi` and `fl_set_track` description hints add 301 characters to the tool declarations.

Harness: [`evals/theory_suite.py`](theory_suite.py), 10 prompts with deterministic checks. Seven are music prompts, checked on the `fl_play_midi` notes: pitch classes, order, repetitions, chord spans, span in beats, varied velocities, record flag and `start_bar`, first note at beat 0. Three are mix prompts: no proposal widens or pans kick, bass or sub, moves more than 6 dB (4 dB for the vocal prompt) or sets an absolute level for a relative request, and the prompt with a WAV must go through `analyze_audio` → `diagnose_complaint`. Before = HEAD `e7bc950` plus only the suite; after = the same plus this change. Each ran 3 times per model, because temperature 0 still varies between runs.

| Model | Before (3 runs) | After (3 runs) |
| --- | --- | --- |
| Vertex `gemma-4-26b-a4b-it-maas` | 6, 7, 6 (mean 6.3/10) | 7, 8, 9 (mean **8.0/10**) |
| Ollama `gemma4:31b-cloud` | 6, 7, 6 (mean 6.3/10) | 8, 8, 7 (mean **7.7/10**) |

Passes per prompt, out of 3 runs, before → after (Vertex; Ollama):

| # | Prompt | Vertex | Ollama |
| --- | --- | --- | --- |
| 1 | Play an E major arpeggio 3 times | 3 → 3 | 0 → 3 (before: G instead of G#) |
| 2 | Play a C major scale | 3 → 3 | 3 → 3 |
| 3 | Play a D dorian scale | 3 → 3 | 1 → 2 (before: Bb; after, 1 run had a stray D#) |
| 4 | Write a 4-bar melody in A minor and play it | 1 → 1 (before: used record=true; after: 17–20 beats long) | 3 → 3 |
| 5 | Play a ii-V-I in C | 0 → 2 | 0 → 0 (builds ii as F A C F, without the D) |
| 6 | Play a G major chord in first inversion | 0 → 0 | 0 → 0 (root position, or the wrong chord) |
| 7 | Record a C major arpeggio into the piano roll starting at bar 3 | 0 → 3 (before: played live) | 3 → 3 |
| 8 | The bass and kick are fighting (no file) | 3 → 3 | 3 → 3 |
| 9 | muddy.wav: the bass and kick are fighting | 3 → 3 | 3 → 3 |
| 10 | The vocals are getting buried, make them sit better | 3 → 3 | 3 → 3 |

The mix prompts passed before and after. After the change, Ollama's answers to 8 and 10 are carving moves from the reference: Bass mid band −3 dB at 80 Hz, and Lead Synth mid −2 dB at 3 kHz plus Vocal +1.5 dB, all three runs. Vertex asks for the exported WAV. Remaining misses: neither model applies "first inversion", even with a worked inversion example, and Ollama keeps dropping the root of the ii chord. One Vertex run put all ii-V-I notes at beat 0. In an earlier run, before the rule said that `start_bar` alone places a recording, Vertex offset a bar-3 recording to `start_beat` 8, which would have landed at bar 5. The judge now requires the first note at beat 0.

Regression check, main suite, same before/after worktrees: Vertex **18/20 → 19/20**, Ollama **20/20 → 20/20**, 0 invented tools and 0 writes after clarification in all four runs. Vertex's remaining miss is prompt 5 (an EQ frequency without a gain). Results: `results/theory_suite-*-{before,after}-r{1,2,3}.json` and `results/gemma_suite-*-theory-{before,after}.json`. AI Studio wasn't run, to stay inside its 16k-tokens/min free tier.

## MCP checks (spec §8.1)

MCP Inspector CLI (`@modelcontextprotocol/inspector`, run via `npx -y`):

```bash
npx -y @modelcontextprotocol/inspector --cli .venv/bin/python -m mixassist --fake --method tools/list
```

This lists all 10 tools with their verbatim §3.2 input schemas; the output is saved in [results/mcp_inspector_tools_list.json](results/mcp_inspector_tools_list.json). Two calls were also made:

- `--method tools/call --tool-name fl_set_track --tool-arg track=Kick --tool-arg volume_change_db=-2` returned `isError: false` with `after.volume_db = -2`, `verified: true`, plus `structuredContent`.
- `--tool-arg volume_db=9` returned `isError: true` and the text `{"error": {"code": "OUT_OF_RANGE", "message": "fl_set_track: volume_db: 9 is greater than the maximum of 5", ...}}`. The server didn't crash.

The scripted MCP client is `tests/test_mcp_server.py`, using the official `mcp` SDK. It spawns `python -m mixassist --fake` over stdio, lists the tools, checks every schema with `Draft202012Validator.check_schema`, and calls each tool once with valid and once with invalid arguments, plus an unknown tool. Every invalid call returns a §3.1 error with `isError: true`. 23 passed.

## Latency

Harness: [`evals/latency_bench.py`](latency_bench.py). It runs six turns, each in a fresh session on a fresh FakeFL: "turn the kick down 2 dB", "what's on my mixer?", "pan the hats 30% left", "analyze bright.wav against pink.wav, it's too bright", "play an E major arpeggio", "is anything clipping?". For each turn it records:

- wall time;
- each LLM call, with its time and tokens (input, output and thinking);
- time spent sleeping between retries;
- tool time;
- SysEx round trips to FL.

FakeFL runs on a virtual clock, so playback and peak watching take no real time. "Before" is commit `27b9c25`; "after" is `2d557fa`. Same day, temperature 0. Raw results: `results/latency-<provider>-<label>.json`.

Wall time per turn in seconds, with LLM calls in parentheses:

| Provider | Run | kick down | overview | pan hats | bright | arpeggio | clipping | Total | LLM calls | Input tokens | Retry wait |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Vertex `gemma-4-26b-a4b-it-maas` | before | 2.2 (2) | 2.1 (2) | 1.7 (2) | 4.2 (3) | 3.8 (2) | 1.3 (2) | 15.3 s | 13 | 28,971 | 0 s |
| | after | 1.1 (1) | 1.5 (2) | 1.6 (2) | 3.5 (2) | 2.4 (1) | 0.6 (1) | **10.7 s** | 9 | 18,091 | 0 s |
| | after, `SOUNDPILOT_FASTPATH=1` | 0.0 (0) | 2.1 (2) | 1.3 (2) | 3.3 (2) | 1.6 (1) | 0.5 (1) | 8.8 s | 8 | | 0 s |
| Ollama `gemma4:31b-cloud` | before | 1.0 (2) | 1.0 (2) | 1.3 (3) | 5.4 (3) | 3.5 (2) | 3.0 (2) | 15.2 s | 14 | 44,687 | 0 s |
| | after | 0.5 (1) | 2.2 (2) | 1.4 (3) | 2.5 (2) | 0.7 (1) | 0.4 (1) | **7.7 s** | 10 | 29,521 | 0 s |
| AI Studio `gemma-4-26b-a4b-it` (free tier) | before | 4.5 (2) | 4.3 (2) | 10.4 (3) | 31.7, **failed** (429) | 44.9 (2) | 6.3 (2) | 102.1 s | 12 | 36,999 | 62 s |
| | after | 1.5 (1) | 2.6 (2) | 64.1 (3) | 4.9 (2) | 2.9 (1) | 0.9 (1) | 76.9 s, no failures | 10 | 31,519 | 60.5 s |
| | after, `SOUNDPILOT_LLM_FALLBACK=ollama` | 1.3 (1) | 2.7 (2) | 2.8 (3) | 2.5 (2) | 1.0 (1) | 0.5 (1) | **10.9 s** | 10 | | 0 s |

What the numbers showed:

- **LLM calls are the cost.** Tool time was under 0.1 s per turn, except the 1.4 s audio analysis. Every turn made 2–3 calls, and the last one only restated what the tools had already returned. That summary call is now skipped when the turn ends in proposals, a clarification question, a peak report or a live playback. The reply is built from the tool results ("Proposed for your approval: Kick: volume 0.0 dB -> -2.0 dB."). Questions, errors and multi-part requests still go back to the model. Result: 13 → 9 calls on Vertex and 14 → 10 on Ollama.
- **Rate limits are the long tail.** On AI Studio the old 1, 2, 4, 8, 16 s backoff gave up after 31 s, while the server's RetryInfo asked for 42–59 s. That turn failed, and the next turn waited another 31 s. Retries now wait for the server's delay, so the "after" run had no failures but one 59 s wait. The fallback chain gives up on the primary after 4 s of waiting, switches to Ollama for 120 s, and finished the same six turns in 10.9 s. The Vertex 429s seen in daily use carry no retry hint, so they still back off 1, 2, 4… s. With a fallback set, they switch after 1 + 2 s.
- **Thinking.** On AI Studio, Gemma 4 thought for 92–466 tokens per turn (1,129 in total). It now sends `thinking_level=MINIMAL`, which brought that to 0 (`thinking_budget` and `LOW` return 400 for Gemma). Vertex MaaS reports no thinking tokens, and Ollama gets `think: false`.
- **Input tokens fell 15–38%.** The mix overview sent to the model now drops flat EQ bands, false mute/solo flags and plugin slot numbers. One overview went from 4,613 to 2,035 characters. The UI still gets the full result. Output is capped at 2,048 tokens per call.
- **FL round trips.** A proposal preview used to read the whole mixer, including empty tracks: 274 SysEx round trips, about 1.7 s at 5 ms each on the real bridge. It now reads only the target track, which takes 16 round trips (0.1 s). For comparison: `fl_get_mix_overview` takes 159, applying "Kick −2 dB" takes 20, and a 10 s peak watch makes about 2,400 polls in FL time. Run `latency_bench --fl-only --rt-ms 5` to reproduce these counts.
- "pan the hats" still makes 2–3 calls. There is no "Hats" track (the fixture has "Hi-Hat"), so the agent reads the mixer and asks which track was meant. That is the intended behavior.

Accuracy after the changes. The Gemma suite runs used the same 20 prompts; the theory suite came from the other worker's in-progress `evals/theory_suite.py`:

- Gemma suite, Vertex: **19/20**, 0 invented tools, 0 writes after clarification (PASS). It missed prompt 5: `volume_change_db` instead of `eq_gain_db`. That reproduced with thinking on and off.
- Gemma suite, Ollama cloud: **20/20**, 0 invented tools, 0 writes after clarification (PASS).
- Theory suite: 8/10 on Ollama and 9/10 on Vertex. That's the same as or better than the other worker's own run without these changes (8/10 on both).

Raw results: [Vertex](results/gemma_suite-vertex-gemma-4-26b-a4b-it-maas-latency.json), [Ollama](results/gemma_suite-ollama-gemma4_31b-cloud-latency.json).

The UI now calls `POST /api/chat/stream`. It shows what the assistant is doing ("Asking Gemma (vertex)", "Reading the mixer", "vertex rate limit…; retrying in 7 s") with an elapsed-seconds counter, instead of a bare "thinking".

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
.venv/bin/python -m evals.theory_suite --llm vertex --tag after-r1   # or --llm ollama --model gemma4:31b-cloud
```
