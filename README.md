# SoundPilot

A warm, vinyl-studio-inspired interface for the **FL Mixing Assistant** open-source AI/MCP project.

## Running the frontend

Requires Node.js 20.19+.

```powershell
npm.cmd install
npm.cmd run dev
```

Open http://127.0.0.1:5173. For a production build:

```powershell
npm.cmd run build
npm.cmd run preview
```

## Implemented UI

- Read-only session/track overview with a DAW-style arrangement mock, selected-track inspector, insert list, level, pan, stereo separation and EQ metadata.
- AI assistant with a **rule-based demo planner**, reviewable before/after track changes, approval, discard, and undo of simulated changes.
- Mix intelligence panel: mixer overview and a sample peak-watch report.
- Audio analysis panel: audio and reference selection, illustrative loudness/true-peak/dynamic-range results, complaint diagnosis, and a verification-file affordance.
- MIDI workbench: MIDI file selection, illustrative note/key/timing analysis, quantize/humanize/snap-to-scale parameters and live audition/recording request previews.
- Custom SVG record/waveform branding; reduced logo shadow and roomier wordmark.

**No audio files are processed, no MIDI is parsed, no audio is played, and no external app is modified.** Every analysis metric is fixture data. Filenames are not uploaded. The app does not yet connect to FL Studio, Gemma 4, or MCP. The UI is not proof of the underlying features.

## Target architecture

Ollama + Gemma 4 (model access), MCP over stdio, Python 3.11 server with `numpy`, `scipy`, `soundfile`, `pyloudnorm`, `mido`, and `python-rtmidi`; loopMIDI device-script bridge to FL Studio. The FL bridge protocol and future tool contracts are specified separately in the team's **FL Mixing Assistant v1 build spec**. Do not substitute the previous REAPER/Lua architecture.

### UI-to-tool mapping (planned only)

| UI | MCP interface |
| --- | --- |
| Track overview | `fl_get_mix_overview` |
| Assistant changes / undo | `fl_set_track`, `fl_undo` |
| Peak watch | `fl_watch_peaks` |
| Audio measurement | `analyze_audio` |
| Diagnosis / follow-up | `diagnose_complaint`, `verify_fix` |
| MIDI workbench | `analyze_midi`, `fix_midi`, `fl_play_midi` |

Keep approval for all writes. Never present example analysis or a simulated change as verified FL Studio state. Track references may be case-insensitive names or integer indices; ambiguous matches must require disambiguation before any live edit.

## Collaboration

Work in feature branches and merge through pull requests. Keep all API keys out of browser code.
