import React, { useState } from "react";
import { api, isError } from "./api.js";
import { duration, fixed, hz, signed } from "./format.js";
import ErrorNote from "./ErrorNote.jsx";
import { ToolIntro } from "./MixView.jsx";

const BANDS = ["20-60", "60-150", "150-500", "500-2k", "2k-5k", "5k-10k", "10k-20k"];
const PRESETS = ["too bright", "muddy", "harsh", "boomy", "thin", "too quiet"];

export default function AnalysisPanel({ run, tracks, offline, info, onProposals }) {
  const [path, setPath] = useState("");
  const [refPath, setRefPath] = useState("");
  const [analysis, setAnalysis] = useState(null);
  const [complaint, setComplaint] = useState("");
  const [track, setTrack] = useState("");
  const [diagnosis, setDiagnosis] = useState(null);
  const [diagnosedFor, setDiagnosedFor] = useState("");
  const [afterPath, setAfterPath] = useState("");
  const [verification, setVerification] = useState(null);
  const [busy, setBusy] = useState("");

  const analysisId = analysis && !isError(analysis) ? analysis.analysis_id : null;

  async function analyze(e) {
    e.preventDefault();
    setBusy("analyze");
    setDiagnosis(null);
    setVerification(null);
    setAnalysis(await run(() => api.analyzeAudio(path.trim(), refPath.trim() || undefined)));
    setBusy("");
  }

  async function diagnose(text) {
    text = (text ?? complaint).trim();
    if (!text || !analysisId) return;
    setComplaint(text);
    setBusy("diagnose");
    const r = await run(() => api.diagnose(text, analysisId, track || undefined));
    setBusy("");
    setDiagnosis(r);
    setDiagnosedFor(text);
    setVerification(null);
    if (!isError(r)) onProposals(r.proposals, "audio");
  }

  async function verify(e) {
    e.preventDefault();
    setBusy("verify");
    setVerification(await run(() => api.verify(diagnosedFor || complaint.trim(), analysisId, afterPath.trim())));
    setBusy("");
  }

  const a = analysisId ? analysis : null;
  const ref = a?.reference;

  return <section className="toolPage" aria-label="audio workspace">
    <ToolIntro overline="AUDIO LAB / MEASURE" title="Audio analysis" text="Inspect loudness and tonal balance before changing the sound." info={info}/>
    <div className="toolColumns">
      <div className="surface toolsurface">
        <span className="overline">01 / AUDIO MEASUREMENT</span><h3>Know what you're hearing.</h3>
        <p className="toolHint">Enter absolute paths to files on the machine running the SoundPilot server (browsers can't share real file paths).</p>
        <form onSubmit={analyze}>
          <label className="fieldLabel" htmlFor="audio-path">Mix WAV (absolute path)</label>
          <input id="audio-path" className="fieldSelect" placeholder="/Users/me/renders/mix.wav" value={path} onChange={e => setPath(e.target.value)}/>
          <label className="fieldLabel" htmlFor="ref-path">Reference WAV (optional)</label>
          <input id="ref-path" className="fieldSelect" placeholder="/Users/me/refs/reference.wav" value={refPath} onChange={e => setRefPath(e.target.value)}/>
          <button className="accentButton wide" disabled={!path.trim() || busy === "analyze" || offline}>{busy === "analyze" ? "Analyzing…" : "Analyze audio ↗"}</button>
        </form>
        {analysis && isError(analysis) && <ErrorNote error={analysis}/>}
        {a && <div className="report">
          <b>{a.analysis_id} · {duration(a.duration_s)} · {(a.sample_rate / 1000).toFixed(1)} kHz</b>
          <div className="metricTiles">
            <div><small>INTEGRATED</small><strong>{fixed(a.integrated_lufs)} LUFS</strong></div>
            <div><small>TRUE PEAK</small><strong>{fixed(a.true_peak_dbtp)} dBTP</strong></div>
            <div><small>LOUDNESS RANGE</small><strong>{fixed(a.lra_lu)} LU</strong></div>
            <div><small>CREST FACTOR</small><strong>{fixed(a.crest_db)} dB</strong></div>
            <div><small>CORRELATION</small><strong>{fixed(a.correlation_mean, 2)}</strong></div>
            <div><small>CORRELATION P5</small><strong>{fixed(a.correlation_p5, 2)}</strong></div>
          </div>
          <div className="metricrow"><span>Short-term max</span><strong>{fixed(a.short_term_max_lufs)} LUFS</strong></div>
          <div className="metricrow"><span>Sample peak</span><strong>{fixed(a.sample_peak_dbfs)} dBFS</strong></div>
          <div className="metricrow"><span>Side / mid</span><strong>{fixed(a.side_mid_db)} dB</strong></div>
          <div className="metricrow"><span>Spectral centroid</span><strong>{hz(a.centroid_hz)}</strong></div>
          {ref && <div className="metricrow"><span>Loudness vs reference</span><strong className={Math.abs(ref.lufs_diff) >= 1 ? "warning" : ""}>{signed(ref.lufs_diff)} LU</strong></div>}
          <table className="dataTable">
            <thead><tr><th>BAND (Hz)</th><th>LEVEL</th>{ref && <th>VS REFERENCE</th>}</tr></thead>
            <tbody>{BANDS.map(k => <tr key={k}><td>{k}</td><td>{fixed(a.bands_db?.[k])} dB</td>{ref && <td className={Math.abs(ref.band_diff_db?.[k] ?? 0) >= 2 ? "warning" : ""}>{signed(ref.band_diff_db?.[k])} dB</td>}</tr>)}</tbody>
          </table>
        </div>}
      </div>
      <aside className="surface toolsurface">
        <span className="overline">02 / DIAGNOSE A COMPLAINT</span><h3>Turn a feeling into a plan.</h3>
        {!analysisId && <p className="toolHint">Analyze a mix first; the diagnosis compares its measurements against targets (and the reference, if given).</p>}
        <form onSubmit={e => { e.preventDefault(); diagnose(); }}>
          <label className="fieldLabel" htmlFor="complaint">What sounds wrong?</label>
          <input id="complaint" className="fieldSelect" placeholder="e.g. my mix is too bright" value={complaint} onChange={e => setComplaint(e.target.value)}/>
          <div className="chipRow">{PRESETS.map(p => <button type="button" key={p} className="chip" onClick={() => setComplaint(p)}>{p}</button>)}</div>
          <label className="fieldLabel" htmlFor="target">Track to adjust (optional)</label>
          <select id="target" className="fieldSelect" value={track} onChange={e => setTrack(e.target.value)}><option value="">Let SoundPilot choose</option>{tracks.map(t => <option key={t.index} value={t.name}>{t.name}</option>)}</select>
          <button className="accentButton wide" disabled={!analysisId || !complaint.trim() || busy === "diagnose" || offline}>{busy === "diagnose" ? "Diagnosing…" : "Diagnose ↗"}</button>
        </form>
        {diagnosis && (isError(diagnosis) ? <ErrorNote error={diagnosis}/> : <div className="report">
          {diagnosis.needs_clarification ? <>
            <span className="overline">NEEDS CLARIFICATION</span>
            <p>{diagnosis.question}</p>
            <div className="chipRow">{(diagnosis.options || []).map(o => <button key={o} className="chip" disabled={!!busy} onClick={() => diagnose(o)}>{o}</button>)}</div>
          </> : <>
            <span className="overline">FINDINGS · “{diagnosedFor}”</span>
            {diagnosis.findings?.length ? <table className="dataTable">
              <thead><tr><th>DESCRIPTOR</th><th>VALUE</th><th>TARGET</th><th>DELTA</th></tr></thead>
              <tbody>{diagnosis.findings.map((f, i) => <tr key={i}><td>{f.descriptor}</td><td>{fixed(f.value)} {f.unit}</td><td>{fixed(f.target)} {f.unit}</td><td className="warning">{signed(f.delta)} {f.unit}</td></tr>)}</tbody>
            </table> : <p>No measurable problem found for this complaint.</p>}
            {diagnosis.proposed_changes?.map((c, i) => <p key={i}>Proposed: {c.why}</p>)}
            {diagnosis.proposals?.length > 0 && <p>{diagnosis.proposals.length} change{diagnosis.proposals.length === 1 ? "" : "s"} added to the proposed edits below. Approve, then render and verify.</p>}
          </>}
        </div>)}
        <span className="overline effectslabel">03 / VERIFY THE FIX</span>
        <form onSubmit={verify}>
          <label className="fieldLabel" htmlFor="after-path">After-edit render (absolute path)</label>
          <input id="after-path" className="fieldSelect" placeholder="/Users/me/renders/mix_fixed.wav" value={afterPath} onChange={e => setAfterPath(e.target.value)}/>
          <button className="subtleButton wide" disabled={!analysisId || !(diagnosedFor || complaint.trim()) || !afterPath.trim() || busy === "verify" || offline}>{busy === "verify" ? "Verifying…" : "Verify against the original ↗"}</button>
        </form>
        {verification && (isError(verification) ? <ErrorNote error={verification}/> : <div className="report">
          <b>{verification.passed ? "✓ Fix verified" : "⚠ Fix did not pass"}</b> <span className={"tag " + (verification.passed ? "ok" : "warn")}>{verification.passed ? "PASSED" : "FAILED"}</span>
          <table className="dataTable">
            <thead><tr><th>CHECK</th><th>BEFORE</th><th>AFTER</th><th></th></tr></thead>
            <tbody>{(verification.checks || []).map((c, i) => <tr key={i} title={c.rule}><td>{c.descriptor}<small>{c.rule}</small></td><td>{fixed(c.before)}</td><td>{fixed(c.after)}</td><td className={c.passed ? "" : "warning"}>{c.passed ? "✓" : "✗"}</td></tr>)}</tbody>
          </table>
        </div>)}
      </aside>
    </div>
  </section>;
}
