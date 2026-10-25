import React from "react";
import { db, hz, pan, pct, trackColor } from "./format.js";
import RecordMark from "./RecordMark.jsx";

const BANDS = ["low", "mid", "high"];

export default function TrackInspector({ track, onDiscuss }) {
  if (!track) {
    return <aside className="surface detailPanel"><span className="overline">02 / TRACK NOTES</span><div className="planempty"><div className="emptygroove"><RecordMark large/></div><strong>No track selected.</strong><p>Pick a mixer track to see its level, pan, EQ and effects chain.</p></div></aside>;
  }
  const color = trackColor(track.index);
  const eq = BANDS.map(band => track.eq?.find(b => b.band === band) || { band });
  const plugins = [...(track.plugins || [])].sort((a, b) => a.slot - b.slot);
  return <aside className="surface detailPanel">
    <span className="overline">02 / TRACK NOTES</span>
    <div className="detailtop"><div className="detaildisc" style={{ borderColor: color }}><span style={{ background: color }}/></div><span className="detailtype">{track.index === 0 ? "MASTER" : "MIXER TRACK " + String(track.index).padStart(2, "0")}</span></div>
    <h3>{track.name}</h3>
    <p className="detailcopy">Signal and routing overview. Make actual edits inside FL or through an approved AI plan.</p>
    <div className="detailRows">
      <div><span>CHANNEL LEVEL</span><strong>{db(track.volume_db)}</strong></div>
      <div><span>PAN POSITION</span><strong>{pan(track.pan_pct)}</strong></div>
      <div><span>STEREO WIDTH</span><strong>{pct(track.stereo_width_pct)}</strong></div>
      <div><span>STATE</span><strong>{[track.muted ? "MUTED" : "ACTIVE", track.solo && "SOLO"].filter(Boolean).join(" · ")}</strong></div>
      <div><span>INSERTS</span><strong>{String(plugins.length).padStart(2, "0")}</strong></div>
    </div>
    <span className="overline effectslabel">MIXER EQ</span>
    <div className="eqgrid">{eq.map(b => <div key={b.band}><small>{b.band.toUpperCase()}</small><strong>{db(b.gain_db)}</strong><span>{hz(b.freq_hz)}</span></div>)}</div>
    <span className="overline effectslabel">EFFECTS CHAIN</span>
    <div className="fxlist">{plugins.length ? plugins.map((p, i) => <div key={p.slot} title={"Mixer slot " + p.slot}><span>{String(i + 1).padStart(2, "0")}</span>{p.name}<span>↗</span></div>) : <div><span>–</span>No plugins on this track</div>}</div>
    <button className="detailcta" onClick={() => onDiscuss(track)}>Discuss this track with AI <span>↗</span></button>
  </aside>;
}
