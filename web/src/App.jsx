import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, isError, newSessionId, OfflineError, API_BASE } from "./api.js";
import { time } from "./format.js";
import RecordMark from "./RecordMark.jsx";
import SessionView from "./SessionView.jsx";
import AssistantView from "./AssistantView.jsx";
import ProposalPanel from "./ProposalPanel.jsx";
import MixView from "./MixView.jsx";
import AnalysisPanel from "./AnalysisPanel.jsx";
import MidiView from "./MidiView.jsx";

const SESSION_ID = newSessionId();
const START_COMMAND = ".venv/bin/uvicorn mixassist.api:app --port 8000";
const TOOL_LABELS = { fl_set_track: "Mixer change", fix_midi: "MIDI fix", fl_play_midi: "MIDI recording", fl_undo: "Undo" };

export function describeStatus(status, offline) {
  if (offline) return { name: "SoundPilot", fl: "Backend offline", tag: "OFFLINE", llm: "LLM unavailable", provider: "–", model: "–", live: false };
  if (!status) return { name: "SoundPilot", fl: "Connecting…", tag: "CONNECTING", llm: "…", provider: "…", model: "…", live: false };
  const fake = status.fl_backend === "fake";
  const name = fake ? "FakeFL" : "FL Studio";
  const fl = status.fl_connected ? (fake ? "FakeFL connected" : "FL connected") : (fake ? "FakeFL not connected" : "FL not connected");
  const model = status.model || "unknown model";
  const unavailable = status.model_available === false ? " (unavailable)" : "";
  return {
    name, fl, tag: fake ? "FAKEFL" : "FL BRIDGE",
    llm: (status.llm_provider || "llm") + " · " + model + unavailable,
    provider: status.llm_provider || "llm", model: model + unavailable,
    message: status.model_message, live: !!status.fl_connected,
  };
}

function proposalLabel(p) {
  return p.preview?.summary || (p.preview?.track?.name ? p.preview.track.name + " · " : "") + (TOOL_LABELS[p.tool] || p.tool);
}

export default function App() {
  const [tab, setTab] = useState("session");
  const [status, setStatus] = useState(null);
  const [offline, setOffline] = useState(false);
  const [mix, setMix] = useState(null);
  const [mixError, setMixError] = useState(null);
  const [mixAt, setMixAt] = useState(null);
  const [selected, setSelected] = useState(null);
  const [proposals, setProposals] = useState([]);
  const [changes, setChanges] = useState([]);
  const [undoResult, setUndoResult] = useState(null);
  const [undoing, setUndoing] = useState(false);
  const [log, setLog] = useState([]);
  const [draft, setDraft] = useState("");
  const wasOffline = useRef(false);

  const addLog = useCallback(message => setLog(l => [{ message, at: time() }, ...l].slice(0, 40)), []);

  // Wraps an API call so an unreachable backend flips the UI into its offline state
  // and callers always receive a value (data or a §3.1-shaped error).
  const run = useCallback(async thunk => {
    try {
      const r = await thunk();
      setOffline(false);
      return r;
    } catch (e) {
      if (e instanceof OfflineError) {
        setOffline(true);
        return { error: { code: "BACKEND_OFFLINE", message: e.message, hint: "Start it from the repo root: " + START_COMMAND } };
      }
      return { error: { code: "CLIENT_ERROR", message: String(e?.message || e) } };
    }
  }, []);

  const refreshStatus = useCallback(async () => {
    const r = await run(() => api.status());
    if (!isError(r)) setStatus(r);
  }, [run]);

  const refreshMix = useCallback(async () => {
    const r = await run(() => api.mix());
    if (isError(r)) {
      if (r.error.code !== "BACKEND_OFFLINE") setMixError(r.error);
      return;
    }
    setMix(r);
    setMixError(null);
    setMixAt(time());
  }, [run]);

  useEffect(() => {
    refreshStatus();
    refreshMix();
    const id = setInterval(refreshStatus, 5000);
    return () => clearInterval(id);
  }, [refreshStatus, refreshMix]);

  useEffect(() => {
    if (wasOffline.current && !offline) {
      refreshMix();
      addLog("Backend reconnected");
    }
    wasOffline.current = offline;
  }, [offline, refreshMix, addLog]);

  const tracks = mix?.tracks || [];
  useEffect(() => {
    if (tracks.length && !tracks.some(t => t.index === selected)) {
      setSelected((tracks.find(t => t.index !== 0) || tracks[0]).index);
    }
  }, [tracks, selected]);

  const addProposals = useCallback((list, source) => {
    if (!list?.length) return;
    setProposals(ps => {
      const known = new Set(ps.map(p => p.proposal_id));
      const fresh = list.filter(p => !known.has(p.proposal_id)).map(p => ({ ...p, source, status: "pending", result: null }));
      return [...ps, ...fresh];
    });
    addLog(`${list.length} proposal${list.length === 1 ? "" : "s"} drafted (${source})`);
  }, [addLog]);

  const patchProposal = (id, patch) => setProposals(ps => ps.map(p => (p.proposal_id === id ? { ...p, ...patch } : p)));

  async function applyProposal(p) {
    patchProposal(p.proposal_id, { status: "applying" });
    const r = await run(() => api.apply(p.proposal_id));
    const result = isError(r) ? r : (r.result ?? r);
    if (isError(result)) {
      patchProposal(p.proposal_id, { status: "failed", result });
      addLog(`Apply failed · ${proposalLabel(p)} · ${result.error.code}`);
      if (result.error.code !== "BACKEND_OFFLINE") refreshMix();
      return;
    }
    patchProposal(p.proposal_id, { status: "applied", result });
    if (result.change_id) {
      setChanges(cs => [{ change_id: result.change_id, proposal_id: p.proposal_id, label: proposalLabel(p) }, ...cs]);
    }
    const verified = result.verified === undefined ? "" : result.verified ? " · verified" : " · NOT verified";
    addLog(`Applied · ${proposalLabel(p)}${verified}`);
    if (result.user_action) addLog("Next step in FL: " + result.user_action);
    refreshMix();
  }

  async function applyAll(list) {
    for (const p of list) await applyProposal(p);
  }

  async function discardProposal(p) {
    const r = await run(() => api.discard(p.proposal_id));
    if (isError(r) && r.error.code === "BACKEND_OFFLINE") return;
    patchProposal(p.proposal_id, { status: "discarded" });
    addLog(`Discarded · ${proposalLabel(p)}`);
  }

  async function discardAll(list) {
    for (const p of list) await discardProposal(p);
  }

  async function undoLast() {
    const last = changes[0];
    if (!last || undoing) return;
    setUndoing(true);
    const r = await run(() => api.undo(last.change_id));
    setUndoing(false);
    const applied = proposals.find(p => p.proposal_id === last.proposal_id);
    setUndoResult({ ...r, label: last.label, undoneAfter: applied?.result?.after });
    if (isError(r)) {
      addLog(`Undo failed · ${r.error.code}: ${r.error.message}`);
      return;
    }
    setChanges(cs => cs.slice(1));
    patchProposal(last.proposal_id, { status: "undone" });
    addLog(`Undid · ${last.label}${r.verified === false ? " · NOT verified" : r.verified ? " · verified" : ""}`);
    refreshMix();
  }

  function discussTrack(track) {
    setDraft(track.name + ": ");
    setTab("assistant");
  }

  const info = describeStatus(status, offline);
  const pending = proposals.filter(p => p.status === "pending").length;
  const panelProps = { proposals, onApply: applyProposal, onApplyAll: applyAll, onDiscard: discardProposal, onDiscardAll: discardAll, undoResult, log };
  const toolProps = { run, tracks, mix, offline, info, onProposals: addProposals };
  const nav = [
    ["session", "◫", "Session overview"],
    ["assistant", "✳", "Studio assistant"],
    ["mix", "◉", "Mix intelligence"],
    ["audio", "⌁", "Audio analysis"],
    ["midi", "♫", "MIDI workbench"],
  ];

  return <div className="shell">
    <aside className="rail">
      <div className="identity"><RecordMark/><div className="identitytext"><strong>SOUNDPILOT</strong><span>THE CREATIVE CONTROL ROOM</span></div></div>
      <div className="railcaption">THE WORKSPACE</div>
      <nav aria-label="Main navigation">{nav.map(([id, glyph, label]) => <button key={id} className={"railitem " + (tab === id ? "chosen" : "")} title={label} aria-label={label} aria-current={tab === id ? "page" : undefined} onClick={() => setTab(id)}><span className="navglyph">{glyph}</span>{label} {id === "assistant" && pending > 0 && <span className="railcount">{pending}</span>}</button>)}</nav>
      <div className="railend"><div className="vinyl"><div className="vinylcenter">SP</div></div><span className="railendtext">MAKE ROOM<br/>FOR THE MUSIC.</span><div className="localstate"><span className={"livepin " + (info.live ? "" : "off")}/> {info.tag}<span className="substate">{offline ? "Backend offline" : info.fl + (mix ? " · " + tracks.length + " tracks" : "")}</span></div></div>
    </aside>
    <div className="mainstage">
      <div className="topbar"><span>SOUNDPILOT / STUDIO 01</span><div className="topright"><span className={"glowdot " + (info.live ? "" : "off")}/> {info.fl.toUpperCase()} <span className="topdivider"/> {info.provider.toUpperCase()} · {info.model}</div></div>
      {offline && <div className="offlineBanner" role="status"><div><strong>Backend offline.</strong> SoundPilot can't reach <code>{API_BASE}</code>. Start it from the repo root with <code>{START_COMMAND}</code>.</div><button className="subtleButton" onClick={() => { refreshStatus(); refreshMix(); }}>Retry ↻</button></div>}
      <header className="hero"><div className="heroCopy"><div className="kicker"><span className="kickerline"/> THE STUDIO IS YOURS</div><h1>{tab === "session" ? <>Every track.<br/><i>In the picture.</i></> : tab === "assistant" ? <>Your vision.<br/><i>In the mix.</i></> : <>The details.<br/><i>Behind the sound.</i></>}</h1><p>{tab === "session" ? "A clearer view of your entire session. Stay focused on the music — leave the faders to your DAW." : tab === "assistant" ? "An assistant for the decisions behind the sound. Describe the change, preview the plan, and stay in control." : "Mix observations, audio measurements, and MIDI ideas—designed to complement your DAW, not replace it."}</p></div><div className="heroVisual" aria-hidden="true"><div className="heroHalo"/><div className="heroRecord"><div className="recordLabel"><RecordMark large/></div></div><div className="heroNeedle"/><span className="heroMicro">SIDE A &nbsp; • &nbsp; CREATIVE SESSION</span></div></header>
      <div className="sectiontop"><div><span className="overline">CURRENT PROJECT</span><h2>{info.name} session <span className="projecttag">{info.tag}</span></h2></div><div className="sectionactions"><button className="subtleButton" disabled={!changes.length || undoing} onClick={undoLast} title={changes[0] ? "Undo " + changes[0].label : "Nothing to undo"}>{undoing ? "Undoing…" : "↶ Undo last plan"}</button><button className="accentButton" onClick={() => setTab("assistant")}>✳ Ask the assistant <span>↗</span></button></div></div>
      <div className="sessionStats">
        <div><span>TRACKS</span><strong>{mix ? String(tracks.length).padStart(2, "0") : "–"}</strong><small>{tracks.some(t => t.index === 0) ? "MIXER TRACKS INCL. MASTER" : "MIXER TRACKS"}</small></div>
        <div><span>TEMPO</span><strong>{mix?.bpm ? <>{Math.round(mix.bpm * 100) / 100} <em>BPM</em></> : "–"}</strong><small>FROM FL</small></div>
        <div><span>FL LINK</span><strong className="statSmall">{info.fl}</strong><small>{mix?.fl_api ? "FL API " + mix.fl_api : info.tag}</small></div>
        <div title={info.message}><span>ASSISTANT</span><strong className="statSmall">{info.model}</strong><small>{info.provider.toUpperCase()}</small></div>
      </div>
      <div hidden={tab !== "session"}>
        <SessionView tracks={tracks} mix={mix} mixError={mixError} mixAt={mixAt} offline={offline} info={info} selected={selected} onSelect={setSelected} onRefresh={refreshMix} onDiscuss={discussTrack}/>
      </div>
      <div hidden={tab !== "assistant"}>
        <div className="studioGrid assistantGrid">
          <AssistantView sessionId={SESSION_ID} run={run} tracks={tracks} info={info} offline={offline} draft={draft} setDraft={setDraft} onProposals={addProposals} pending={pending}/>
          <ProposalPanel {...panelProps}/>
        </div>
      </div>
      <div hidden={tab !== "mix"}><MixView {...toolProps} onAsk={text => { setDraft(text); setTab("assistant"); }}/>{proposals.some(p => p.source === "mix") && <ProposalPanel {...panelProps} source="mix" inline/>}</div>
      <div hidden={tab !== "audio"}><AnalysisPanel {...toolProps}/>{proposals.some(p => p.source === "audio") && <ProposalPanel {...panelProps} source="audio" inline/>}</div>
      <div hidden={tab !== "midi"}><MidiView {...toolProps}/>{proposals.some(p => p.source === "midi") && <ProposalPanel {...panelProps} source="midi" inline/>}</div>
      <footer><span>SOUNDPILOT <span className="footerstar">✳</span> MADE FOR THE MAKERS</span><span>{info.fl.toUpperCase()} · LLM {info.llm.toUpperCase()}</span></footer>
    </div>
  </div>;
}
