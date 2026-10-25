import React, { useMemo, useState } from "react";
import { db, pan, trackColor } from "./format.js";
import ErrorNote from "./ErrorNote.jsx";
import TrackInspector from "./TrackInspector.jsx";

const MIN_DB = -60, MAX_DB = 5;
export const levelPos = v => Math.max(0, Math.min(100, ((v - MIN_DB) / (MAX_DB - MIN_DB)) * 100));
const SCALE = [-60, -48, -36, -24, -12, 0];

const FILTERS = {
  all: ["All tracks", () => true],
  active: ["Active", t => !t.muted],
  muted: ["Muted", t => t.muted],
  solo: ["Soloed", t => t.solo],
  fx: ["With plugins", t => t.plugins?.length > 0],
};

export function trackTags(t) {
  return [t.index === 0 ? "MASTER" : "TRACK " + t.index, t.muted && "MUTED", t.solo && "SOLO", t.plugins?.length ? t.plugins.length + " FX" : null].filter(Boolean).join(" · ");
}

export default function SessionView({ tracks, mix, mixError, mixAt, offline, info, selected, onSelect, onRefresh, onDiscuss }) {
  const [filter, setFilter] = useState("");
  const [kind, setKind] = useState("all");
  const visible = useMemo(() => tracks.filter(t => FILTERS[kind][1](t) && t.name.toLowerCase().includes(filter.toLowerCase())), [tracks, filter, kind]);
  const focused = tracks.find(t => t.index === selected);

  let body;
  if (offline && !mix) body = <div className="empty">Backend offline — no mix to show yet.</div>;
  else if (mixError) body = <div className="empty"><ErrorNote error={mixError}/></div>;
  else if (!mix) body = <div className="empty">Reading the mix from {info.name}…</div>;
  else if (!visible.length) body = <div className="empty">{tracks.length ? "No tracks match your search." : "The mixer has no tracks in use."}</div>;
  else body = visible.map(t => {
    const color = trackColor(t.index);
    return <button key={t.index} className={"trackrow " + (selected === t.index ? "picked" : "")} onClick={() => onSelect(t.index)}>
      <div className="trackmeta"><span className="tracknumber">{String(t.index).padStart(2, "0")}</span><span className="trackstripe" style={{ background: color }}/><div><strong>{t.name}</strong><small>{trackTags(t)}</small></div></div>
      <div className={"levelcell " + (t.muted ? "dim" : "")}>
        <div className="leveltrack"><div className="levelfill" style={{ width: levelPos(t.volume_db) + "%" }}/><span className="zeromark" style={{ left: levelPos(0) + "%" }}/></div>
        <span className="levelvalue">{db(t.volume_db)}<em>{pan(t.pan_pct)}</em></span>
      </div>
    </button>;
  });

  return <div className="studioGrid">
    <section className="surface timelinePanel">
      <div className="surfaceHeading"><div><span className="overline">01 / MIXER</span><h3>Inside the session</h3><p>Live from {info.name}. Mix settings are read-only here; changes go through an approved plan.</p></div><div className="filterline"><select aria-label="Filter tracks" value={kind} onChange={e => setKind(e.target.value)}>{Object.entries(FILTERS).map(([k, [label]]) => <option key={k} value={k}>{label}</option>)}</select><input placeholder="Find a track" aria-label="Find a track" value={filter} onChange={e => setFilter(e.target.value)}/></div></div>
      <div className="ruler"><span>TRACK / MIXER</span><div className="levelscale">{SCALE.map(d => <span key={d} style={{ left: levelPos(d) + "%" }}>{d === 0 ? "0 dB" : d}</span>)}</div></div>
      <div className="trackrows">{body}</div>
      <div className="timelinefooter"><span><span className="legenddot"/> FADER LEVEL · −60 TO +5 dB</span><span>{mixAt ? "READ FROM " + info.name.toUpperCase() + " AT " + mixAt : "NOT YET READ"} <button className="linkButton" onClick={onRefresh} disabled={offline}>↻ REFRESH</button></span></div>
    </section>
    <TrackInspector track={focused} onDiscuss={onDiscuss}/>
  </div>;
}
