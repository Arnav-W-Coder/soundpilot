import React from "react";
import { fieldLabel, fieldValue, fixed } from "./format.js";
import RecordMark from "./RecordMark.jsx";
import ErrorNote from "./ErrorNote.jsx";

const TOOL_TITLES = { fl_set_track: "Mixer change", fix_midi: "MIDI fix", fl_play_midi: "Record MIDI into FL", fl_undo: "Undo a change" };
const STATUS_TAGS = { pending: ["PENDING", "pending"], applying: ["APPLYING…", "pending"], applied: ["APPLIED", "ok"], failed: ["FAILED", "warn"], undone: ["UNDONE", "muted"], discarded: ["DISCARDED", "muted"] };

function ChangeList({ preview, why }) {
  const changes = preview?.changes || [];
  if (!changes.length) return preview?.summary && preview.summary !== why ? <small className="changeline">{preview.summary}</small> : null;
  return <>{changes.map((c, i) => <small key={i} className="changeline">{fieldLabel(c.field)} {fieldValue(c.field, c.before, c.unit)} → {fieldValue(c.field, c.after, c.unit)}</small>)}</>;
}

function Verified({ value }) {
  if (value === undefined || value === null) return null;
  return <span className={"tag " + (value ? "ok" : "warn")}>{value ? "✓ VERIFIED" : "⚠ NOT VERIFIED"}</span>;
}

// fl_set_track before/after (and fl_undo restored) carry the full track state; show only what moved.
function Readback({ before, after }) {
  const keys = [...new Set([...Object.keys(before || {}), ...Object.keys(after || {})])].filter(k => k !== "eq_band");
  if (!keys.length) return null;
  const changed = before ? keys.filter(k => JSON.stringify(before[k]) !== JSON.stringify(after?.[k])) : keys;
  const band = after?.eq_band || before?.eq_band;
  const label = k => (band && /^eq_/.test(k) ? band + " " : "") + fieldLabel(k);
  return <div className="readback">
    {(changed.length ? changed : keys).map(k => <div className="metricrow" key={k}><span>{label(k)}</span><strong>{before ? fieldValue(k, before[k]) + " → " : ""}{fieldValue(k, after?.[k])}</strong></div>)}
    {before && !changed.length && <p className="userAction">Readback shows no change.</p>}
  </div>;
}

export function ApplyResult({ tool, result }) {
  if (!result) return null;
  if (result.error) return <ErrorNote error={result}/>;
  if (tool === "fix_midi") {
    return <div className="readback">
      <div className="metricrow"><span>Notes changed</span><strong>{result.changed?.length ?? 0}</strong></div>
      {result.reanalysis?.key && <div className="metricrow"><span>Re-analysis</span><strong>{result.reanalysis.key.tonic} {result.reanalysis.key.mode} · {result.reanalysis.flags?.length ? result.reanalysis.flags.join(", ") : "no flags"}</strong></div>}
      <div className="metricrow"><span>Fixed MIDI</span><strong className="pathtext">{result.fixed_mid_path}</strong></div>
      <div className="metricrow"><span>Piano roll script</span><strong className="pathtext">{result.pyscript_path}</strong></div>
      {result.user_action && <p className="userAction"><b>Next step in FL:</b> {result.user_action}</p>}
    </div>;
  }
  if (tool === "fl_play_midi") {
    return <div className="readback">
      <div className="metricrow"><span>Mode</span><strong>{result.mode}</strong></div>
      <div className="metricrow"><span>Notes sent</span><strong>{result.notes_sent}</strong></div>
      {result.channel && <div className="metricrow"><span>Channel</span><strong>{typeof result.channel.index === "number" ? result.channel.index + " · " : ""}{result.channel.name}</strong></div>}
      {result.timing_lateness_ms && <div className="metricrow"><span>Lateness p95 / max</span><strong>{fixed(result.timing_lateness_ms.p95)} / {fixed(result.timing_lateness_ms.max)} ms</strong></div>}
      {result.warnings?.map((w, i) => <p key={i} className="userAction">{w}</p>)}
    </div>;
  }
  if (tool === "fl_undo") return <><div className="resulthead"><span>Restored{result.track?.name ? " · " + result.track.name : ""}</span><Verified value={result.verified}/></div><Readback before={result.undoneAfter} after={result.restored}/></>;
  return <>
    <div className="resulthead">{result.track?.name && <span>{result.track.name}</span>}<Verified value={result.verified}/></div>
    <Readback before={result.before} after={result.after}/>
  </>;
}

export default function ProposalPanel({ proposals, onApply, onApplyAll, onDiscard, onDiscardAll, undoResult, log, source, inline = false }) {
  const scoped = source ? proposals.filter(p => p.source === source) : proposals;
  const pending = scoped.filter(p => p.status === "pending" || p.status === "applying");
  const waiting = pending.filter(p => p.status === "pending");
  const done = scoped.filter(p => ["applied", "failed", "undone"].includes(p.status)).slice(-5).reverse();
  const busy = pending.some(p => p.status === "applying");
  const Tag = inline ? "section" : "aside";

  return <Tag className={"surface proposalPanel " + (inline ? "inlinePanel" : "")}>
    <span className="overline">{inline ? "PROPOSED EDITS / " + source.toUpperCase() : "02 / LISTENING NOTES"}</span>
    <h3>Proposed edits<span className="amberperiod">.</span></h3>
    {pending.length > 0 ? <>
      <p className="detailcopy">Nothing changes until you approve. Every write is read back from FL and checked.</p>
      <div className="planlist">{pending.map((p, i) => <div className="planitem" key={p.proposal_id}>
        <span className="planindex">{String(i + 1).padStart(2, "0")}</span>
        <div>
          <strong>{p.preview?.track?.name || TOOL_TITLES[p.tool] || p.tool}</strong> <span className={"tag " + STATUS_TAGS[p.status][1]}>{STATUS_TAGS[p.status][0]}</span>
          {p.why && <p>{p.why}</p>}
          <ChangeList preview={p.preview} why={p.why}/>
          <div className="planactions"><button className="linkButton" disabled={busy} onClick={() => onApply(p)}>Apply ↗</button><button className="linkButton" disabled={busy} onClick={() => onDiscard(p)}>Discard</button></div>
        </div>
      </div>)}</div>
      <button className="accentButton wide" disabled={busy || !waiting.length} onClick={() => onApplyAll(waiting)}>{busy ? "Applying…" : waiting.length > 1 ? `Approve & apply all ${waiting.length} ↗` : "Approve & apply plan ↗"}</button>
      <button className="subtleButton wide" disabled={busy || !waiting.length} onClick={() => onDiscardAll(waiting)}>Discard plan</button>
    </> : !done.length && !inline && <div className="planempty"><div className="emptygroove"><RecordMark large/></div><strong>A clear slate.</strong><p>Your editing plan will appear here when you give the assistant a direction, diagnose a mix, or prepare a MIDI fix.</p></div>}
    {done.length > 0 && <div className="results">
      <span className="overline">READBACK</span>
      {done.map(p => <div className="planitem result" key={p.proposal_id}>
        <div>
          <strong>{p.preview?.track?.name || TOOL_TITLES[p.tool] || p.tool}</strong> <span className={"tag " + STATUS_TAGS[p.status][1]}>{STATUS_TAGS[p.status][0]}</span>
          <small className="changeline">{p.preview?.summary}</small>
          <ApplyResult tool={p.tool} result={p.result}/>
        </div>
      </div>)}
    </div>}
    {!inline && undoResult && <div className="results">
      <span className="overline">LAST UNDO</span>
      <div className="planitem result"><div><strong>{undoResult.label}</strong><ApplyResult tool="fl_undo" result={undoResult}/></div></div>
    </div>}
    {!inline && <div className="activity"><span className="overline">RECENT ACTIVITY</span>{log.length ? log.slice(0, 6).map((e, i) => <p key={i}><small>{e.at}</small> {e.message}</p>) : <p>No edits made this session.</p>}</div>}
  </Tag>;
}
