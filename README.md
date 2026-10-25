# SoundPilot

Open-source music production assistant prototype powered by a future Gemma 4 + MCP + REAPER integration.

## Frontend (demo mode)

The React + Vite frontend provides a charcoal/orange track dashboard, selectable track inspector, mute/solo controls, adjustable volume sliders, and an AI assistant workspace with a rule-based demo planner, reviewable proposals, apply/discard controls, and undo.

**This commit does not contain a real Gemma, MCP or DAW integration.** The demo does not edit audio or communicate with a DAW yet.

## Local startup

Install Node.js 20.19+ and run:

```bash
npm install
npm run dev
```

Open http://127.0.0.1:5173. To build:

```bash
npm run build
npm run preview
```

## Future integration

React frontend → FastAPI → Ollama Cloud (Gemma 4) → MCP Python server → local REAPER Lua bridge. Keep model credentials server-side and never embed them into the Vite frontend.

## Team workflow

Develop features in separate branches and merge via reviewed pull requests. Open issues for the frontend, agent planner, MCP server and DAW bridge.

## Studio visual refresh

The UI now uses espresso-charcoal surfaces, warm amber highlights, a custom scalable vinyl/waveform SVG brand mark, and an illustrated vinyl record hero. The track overview is intentionally **read-only** (no sliders or mixer mute/solo controls): select a track to inspect level, pan, FX chain, and illustrative clip arrangement. A dedicated assistant view can propose volume, mute, and effect edits. You can approve/discard plans and undo applied demo changes.

The arrangement clips are decorative placeholders, not live audio waveforms. All project values remain simulated; this build does not yet connect FL Studio, REAPER, MCP, or Gemma.

To refresh an existing clone, run `git pull`, then `npm.cmd install` and `npm.cmd run dev` in PowerShell.
