import React, { useState } from "react";
import { api, isError } from "./api.js";
import { fixed, pitchName } from "./format.js";
import ErrorNote from "./ErrorNote.jsx";
import { ApplyResult } from "./ProposalPanel.jsx";
import { ToolIntro } from "./MixView.jsx";

const GRIDS = [[0.125, "1/32 note (0.125 beat)"], [0.25, "1/16 note (0.25 beat)"], [0.5, "1/8 note (0.5 beat)"], [1, "1/4 note (1 beat)"]];

export default function MidiView({ run, offline, info, onProposals }) {
  const [path, setPath] = useState("");
  const [analysis, setAnalysis] = useState(null);
  const [fix, setFix] = useState("quantize");
  const [key, setKey] = useState("");
  const [grid, setGrid] = useState(0.25);
  const [strength, setStrength] = useState(65);
  const [fixResult, setFixResult] = useState(null);
  const [channel, setChannel] = useState("");
  const [record, setRecord] = useState(false);
  const [playResult, setPlayResult] = useState(null);
  const [busy, setBusy] = useState("");

  const m = analysis && !isError(analysis) ? analysis : null;

  async function analyze(e) {
    e.preventDefault();
    setBusy("analyze");
    setFixResult(null);
    const r = await run(() => api.analyzeMidi(path.trim()));
    setAnalysis(r);
    if (!isError(r) && r.key) setKey(`${r.key.tonic} ${r.key.mode}`);
    if (!isError(r) && r.timing?.grid_beats && GRIDS.some(([g]) => g === r.timing.grid_beats)) setGrid(r.timing.grid_beats);
    setBusy("");
  }

  async function proposeFix() {
    setBusy("fix");
    const r = await run(() => api.fixMidi({
      path: path.trim(), fix, strength_pct: strength,
      key: fix === "snap_to_scale" ? key.trim() || undefined : undefined,
      grid_beats: fix === "quantize" ? grid : undefined,
    }));
    setBusy("");
    setFixResult(r);
    if (!isError(r)) onProposals(r.proposals, "midi");
  }

  async function play() {
    setBusy("play");
    const r = await run(() => api.playMidi({ path: path.trim(), channel: channel === "" ? undefined : Number(channel), record: record || undefined }));
    setBusy("");
    setPlayResult(r);
    if (!isError(r) && r.proposals) onProposals(r.proposals, "midi");
  }

  const hasPath = !!path.trim();
  return <section className="toolPage" aria-label="midi workspace">
    <ToolIntro overline="MIDI WORKBENCH / REFINE" title="MIDI workbench" text="Review note timing, key estimates, and performance feel before editing." info={info}/>
    <div className="toolColumns">
      <div className="surface toolsurface">
        <span className="overline">01 / MIDI DIAGNOSIS</span><h3>Make the performance feel human.</h3>
        <p className="toolHint">Enter the absolute path of a .mid file on the machine running the SoundPilot server.</p>
        <form onSubmit={analyze}>
          <label className="fieldLabel" htmlFor="midi-path">MIDI file (absolute path)</label>
          <input id="midi-path" className="fieldSelect" placeholder="/Users/me/midi/melody.mid" value={path} onChange={e => setPath(e.target.value)}/>
          <button className="accentButton wide" disabled={!hasPath || busy === "analyze" || offline}>{busy === "analyze" ? "Analyzing…" : "Analyze MIDI ↗"}</button>
        </form>
        {analysis && isError(analysis) && <ErrorNote error={analysis}/>}
        {m && <div className="report">
          <b>{path.split("/").pop()}</b>
          <div className="metricrow"><span>Estimated key</span><strong>{m.key.tonic} {m.key.mode} · {fixed(m.key.confidence, 2)} confidence</strong></div>
          <div className="metricrow"><span>Notes</span><strong>{m.note_count}</strong></div>
          <div className="metricrow"><span>Density</span><strong>{fixed(m.density_notes_per_beat, 2)} notes / beat</strong></div>
          <div className="metricrow"><span>Timing vs {m.timing?.grid_beats}-beat grid</span><strong>median {fixed(m.timing?.median_offset_ms)} ms · max {fixed(m.timing?.max_offset_ms)} ms</strong></div>
          <div className="metricrow"><span>Velocity</span><strong>{m.velocity?.min}–{m.velocity?.max} · σ {fixed(m.velocity?.std)}</strong></div>
          <div className="metricrow"><span>Out-of-key notes</span><strong className={m.out_of_key?.length ? "warning" : ""}>{m.out_of_key?.length || 0}</strong></div>
          {m.out_of_key?.length > 0 && <table className="dataTable">
            <thead><tr><th>NOTE #</th><th>PITCH</th><th>BEAT</th></tr></thead>
            <tbody>{m.out_of_key.slice(0, 24).map(n => <tr key={n.index}><td>{n.index}</td><td>{pitchName(n.pitch)} ({n.pitch})</td><td>{fixed(n.beat, 2)}</td></tr>)}</tbody>
          </table>}
          {m.out_of_key?.length > 24 && <p>…and {m.out_of_key.length - 24} more.</p>}
          <span className="overline">FLAGS</span>
          <div className="chipRow">{m.flags?.length ? m.flags.map(f => <span key={f} className="chip static">{f}</span>) : <span className="chip static">no flags</span>}</div>
        </div>}
      </div>
      <aside className="surface toolsurface">
        <span className="overline">02 / REPAIR PLAN</span><h3>Choose a musical adjustment.</h3>
        <label className="fieldLabel" htmlFor="midi-fix">Correction</label>
        <select id="midi-fix" className="fieldSelect" value={fix} onChange={e => setFix(e.target.value)}><option value="quantize">Quantize notes</option><option value="humanize">Humanize timing & velocity</option><option value="snap_to_scale">Snap notes to scale</option></select>
        {fix === "snap_to_scale" && <><label className="fieldLabel" htmlFor="midi-key">Key (e.g. A minor)</label><input id="midi-key" className="fieldSelect" value={key} placeholder="A minor" onChange={e => setKey(e.target.value)}/></>}
        {fix === "quantize" && <><label className="fieldLabel" htmlFor="midi-grid">Grid</label><select id="midi-grid" className="fieldSelect" value={grid} onChange={e => setGrid(Number(e.target.value))}>{GRIDS.map(([g, label]) => <option key={g} value={g}>{label}</option>)}</select></>}
        <label className="fieldLabel" htmlFor="strength">Strength · {strength}%</label>
        <input id="strength" type="range" min="0" max="100" value={strength} onChange={e => setStrength(Number(e.target.value))} className="strengthRange"/>
        <p className="toolHint">Writes a separate corrected .mid and a piano-roll script once you approve it. The original file is not modified.</p>
        <button className="accentButton wide" disabled={!hasPath || busy === "fix" || offline} onClick={proposeFix}>{busy === "fix" ? "Preparing…" : "Propose MIDI fix ↗"}</button>
        {fixResult && (isError(fixResult) ? <ErrorNote error={fixResult}/> : <p className="toolHint">{fixResult.proposals?.length || 0} MIDI fix proposal{fixResult.proposals?.length === 1 ? "" : "s"} added below. Approve it to write the files.</p>)}
        <div className="midiPlayback">
          <span className="overline">03 / LIVE NOTE PREVIEW</span><h3>Audition in FL Studio</h3>
          <p className="toolHint">Sends the file's notes to an FL channel. Recording into the pattern is a write and needs your approval.</p>
          <div className="fieldRow">
            <div><label className="fieldLabel" htmlFor="play-channel">Channel (optional)</label><input id="play-channel" className="fieldSelect" type="number" min="0" placeholder="selected" value={channel} onChange={e => setChannel(e.target.value)}/></div>
            <div><label className="fieldLabel" htmlFor="play-mode">Mode</label><select id="play-mode" className="fieldSelect" value={record ? "record" : "live"} onChange={e => setRecord(e.target.value === "record")}><option value="live">Audition only</option><option value="record">Record into FL</option></select></div>
          </div>
          <button className="subtleButton wide" disabled={!hasPath || busy === "play" || offline} onClick={play}>{busy === "play" ? "Sending…" : record ? "Propose recording ↗" : "Play in FL ↗"}</button>
          {playResult && (isError(playResult) ? <ErrorNote error={playResult}/> : playResult.proposals ? <p className="toolHint">Recording proposal added below. Approve it to record.</p> : <div className="report"><ApplyResult tool="fl_play_midi" result={playResult}/></div>)}
        </div>
      </aside>
    </div>
  </section>;
}
