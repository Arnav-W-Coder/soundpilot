import React, { useState } from "react";
import { api, isError } from "./api.js";
import { db, fixed, pan, pct, trackColor } from "./format.js";
import ErrorNote from "./ErrorNote.jsx";

export function ToolIntro({ overline, title, text, info }) {
  return <div className="toolPageIntro"><div><span className="overline">{overline}</span><h2>{title}</h2><p>{text}</p></div><span className="demoStamp">{info.fl.toUpperCase()}</span></div>;
}

export default function MixView({ run, tracks, mix, offline, info, onProposals, onAsk }) {
  const [seconds, setSeconds] = useState(10);
  const [fromBar, setFromBar] = useState(1);
  const [busy, setBusy] = useState(false);
  const [peaks, setPeaks] = useState(null);

  async function watch() {
    setBusy(true);
    setPeaks(null);
    const r = await run(() => api.watchPeaks(Number(seconds) || undefined, Number(fromBar) || undefined));
    setBusy(false);
    setPeaks(r);
    if (!isError(r)) onProposals(r.proposals, "mix");
  }

  const clipping = new Set(peaks?.clipping || []);
  const peakRows = [...(peaks?.tracks || [])].sort((a, b) => (b.max_peak_dbfs ?? -999) - (a.max_peak_dbfs ?? -999));

  return <section className="toolPage" aria-label="mix workspace">
    <ToolIntro overline="MIX CONTROL / OBSERVE" title="Mix intelligence" text="Understand your mix, identify trouble spots, and create reversible editing plans." info={info}/>
    <div className="toolColumns">
      <div className="surface toolsurface">
        <span className="overline">01 / MIXER SNAPSHOT</span><h3>Track levels at a glance</h3>
        <p className="toolHint">Read live from {info.name}. All values are read-only; AI edits require explicit approval.</p>
        {mix ? <div className="mixTable five"><div className="mixHeader"><span>TRACK</span><span>LEVEL</span><span>PAN</span><span>WIDTH</span><span>FX</span></div>{tracks.map(t => <div className="mixRow" key={t.index}><span><i style={{ background: trackColor(t.index) }}/>{t.name}{t.muted && <em className="rowflag">M</em>}{t.solo && <em className="rowflag">S</em>}</span><strong>{db(t.volume_db)}</strong><span>{pan(t.pan_pct)}</span><span>{pct(t.stereo_width_pct)}</span><span>{t.plugins?.length || 0}</span></div>)}</div>
          : <p className="toolHint">{offline ? "Backend offline — no mix to show." : "Reading the mix…"}</p>}
        <button className="detailcta" onClick={() => onAsk("Is anything in the mix too loud or clipping?")}>Suggest a mix adjustment ↗</button>
      </div>
      <aside className="surface toolsurface">
        <span className="overline">02 / PEAK WATCH</span><h3>Find possible clipping</h3>
        <p className="toolHint">Starts playback and samples mixer peaks for the chosen time. Suggested level cuts become proposals you can approve.</p>
        <div className="fieldRow">
          <div><label className="fieldLabel" htmlFor="peak-seconds">Seconds (2–300)</label><input id="peak-seconds" className="fieldSelect" type="number" min="2" max="300" value={seconds} onChange={e => setSeconds(e.target.value)}/></div>
          <div><label className="fieldLabel" htmlFor="peak-bar">From bar</label><input id="peak-bar" className="fieldSelect" type="number" min="1" value={fromBar} onChange={e => setFromBar(e.target.value)}/></div>
        </div>
        <button className="accentButton wide" disabled={busy || offline} onClick={watch}>{busy ? `Listening for ${seconds} s…` : "Watch peaks ↗"}</button>
        {peaks && (isError(peaks) ? <ErrorNote error={peaks}/> : <div className="report">
          <b>{peaks.polls} polls · every {fixed(peaks.poll_interval_ms, 0)} ms</b>
          {peakRows.map(t => <div className="metricrow" key={t.index}><span>{t.name}</span><strong className={clipping.has(t.index) || t.clip_count > 0 ? "warning" : ""}>{t.max_peak_dbfs === null ? "no signal" : db(t.max_peak_dbfs, "dBFS")}{t.clip_count > 0 ? ` · ${t.clip_count} clip${t.clip_count === 1 ? "" : "s"}` : ""}</strong></div>)}
          {peaks.suggestions?.length ? peaks.suggestions.map((s, i) => <p key={i}>Track {s.track}: {db(s.volume_change_db)} — {s.reason}</p>) : <p>No clipping found — no level changes suggested.</p>}
          {peaks.proposals?.length > 0 && <p>{peaks.proposals.length} suggested fix{peaks.proposals.length === 1 ? "" : "es"} added to the proposed edits below.</p>}
        </div>)}
      </aside>
    </div>
  </section>;
}
