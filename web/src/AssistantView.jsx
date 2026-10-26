import React, { useEffect, useRef, useState } from "react";
import { api, isError } from "./api.js";
import { fixed } from "./format.js";
import RecordMark from "./RecordMark.jsx";
import ErrorNote from "./ErrorNote.jsx";
import { Linkified, WebSources } from "./Linkify.jsx";

function summarize(tool, res) {
  if (!res || typeof res !== "object") return res === undefined ? "" : String(res);
  if (res.error) return res.error.code + ": " + res.error.message;
  if (res.proposal_id) return "queued for your approval";
  switch (tool) {
    case "fl_get_mix_overview": return `${res.tracks?.length ?? "?"} tracks · ${res.bpm ?? "?"} BPM`;
    case "fl_watch_peaks": return `${res.polls} polls · ${res.clipping?.length || 0} clipping · ${res.suggestions?.length || 0} suggestions`;
    case "analyze_audio": return `${fixed(res.integrated_lufs)} LUFS · ${fixed(res.true_peak_dbtp)} dBTP · crest ${fixed(res.crest_db)} dB`;
    case "diagnose_complaint": return res.needs_clarification ? "needs clarification" : `${res.findings?.length || 0} findings · ${res.proposed_changes?.length || 0} proposed changes`;
    case "verify_fix": return `${res.passed ? "passed" : "did not pass"} · ${res.checks?.length || 0} checks`;
    case "analyze_midi": return `${res.key ? res.key.tonic + " " + res.key.mode : "?"} · ${res.note_count} notes · ${res.flags?.length || 0} flags`;
    case "fl_play_midi": return `${res.mode} · ${res.notes_sent} notes sent`;
    case "web_search": return `searched the web for “${res.query}” · ${res.results?.length || 0} sources`;
    case "web_fetch": return `read ${res.text?.length ?? 0} characters${res.truncated ? " (truncated)" : ""}`;
    default: {
      const s = JSON.stringify(res);
      return s.length > 110 ? s.slice(0, 110) + "…" : s;
    }
  }
}

function repeatsQuestion(text, question) {
  const core = question.trim().replace(/[\s.?!:]+$/, "").toLowerCase();
  return !!text && text.toLowerCase().includes(core);
}

function ToolTrace({ trace }) {
  if (!trace?.length) return null;
  return <details className="trace">
    <summary>What I measured{trace.some(t => t.tool?.startsWith("web_")) ? " & looked up" : ""} · {trace.length} tool call{trace.length === 1 ? "" : "s"}</summary>
    <ol>{trace.map((t, i) => <li key={i} className={t.result?.error ? "traceitem failed" : "traceitem"}>
      <div><span className="tracetool">{t.tool}</span><code className="traceargs">{JSON.stringify(t.args ?? {})}</code></div>
      <p>{summarize(t.tool, t.result)}</p>
      <WebSources tool={t.tool} result={t.result}/>
      <details className="rawtoggle"><summary>raw result</summary><pre className="rawjson">{JSON.stringify(t.result, null, 2)}</pre></details>
    </li>)}</ol>
  </details>;
}

function suggestionsFor(tracks) {
  const names = tracks.filter(t => t.index !== 0).map(t => t.name);
  const kick = names.find(n => /kick/i.test(n)) || names[0];
  const rest = names.filter(n => n !== kick);
  return [
    `Turn the ${kick || "kick"} down 2 dB`,
    rest[0] && `Pan the ${rest[0]} 20% left`,
    rest[1] && `Widen the ${rest[1]} by 20%`,
    "Give me an overview of the mix",
  ].filter(Boolean);
}

function Working({ step, since }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, []);
  const seconds = Math.max(0, Math.floor((now - since) / 1000));
  const waiting = step?.type === "retry" || step?.type === "fallback";
  return <p className={waiting ? "typing waiting" : "typing"}>
    {step?.text || "Measuring and thinking"}<span>.</span><span>.</span><span>.</span>
    {seconds > 0 && <small className="elapsed">{seconds} s</small>}
  </p>;
}

export default function AssistantView({ sessionId, run, tracks, info, offline, draft, setDraft, onProposals, pending }) {
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState(null);
  const [since, setSince] = useState(0);
  const inputRef = useRef(null);
  const endRef = useRef(null);

  useEffect(() => { endRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [messages, busy, step]);
  useEffect(() => { if (draft) inputRef.current?.focus(); }, [draft]);

  async function send(text) {
    text = text.trim();
    if (!text || busy) return;
    setMessages(m => [...m, { role: "user", text }]);
    setDraft("");
    setBusy(true);
    setStep(null);
    setSince(Date.now());
    const r = await run(() => api.chatStream(sessionId, text, setStep));
    setBusy(false);
    setStep(null);
    if (isError(r)) {
      setMessages(m => [...m, { role: "error", error: r.error }]);
      return;
    }
    const proposals = r.proposals || [];
    onProposals(proposals, "assistant");
    setMessages(m => [...m, {
      role: "assistant",
      text: r.reply,
      trace: r.tool_trace || [],
      proposals: proposals.length,
      clarify: r.needs_clarification ? { question: r.question, options: r.options || [] } : null,
    }]);
  }

  const lastIndex = messages.length - 1;
  return <section className="surface conversation">
    <div className="surfaceHeading"><div><span className="overline">01 / CREATIVE DIRECTION</span><h3>Talk through the mix.</h3><p>Studio notes become reviewable changes, not invisible actions.</p></div><span className="assistTag">✳ {info.provider.toUpperCase()} · {info.model}</span></div>
    <div className="dialogue">
      <div className="aiBubble"><div className="speaker"><RecordMark/> <span>SOUNDPILOT</span></div><p>What are you hearing? Tell me what you want to change. I'll measure the mix and prepare a plan for your review before anything is applied.</p></div>
      {messages.map((m, i) => m.role === "user"
        ? <div key={i} className="humanBubble">{m.text}</div>
        : m.role === "error"
          ? <div key={i} className="aiBubble"><div className="speaker"><RecordMark/> <span>SOUNDPILOT</span></div><ErrorNote error={m.error} onCandidate={c => setDraft(c)}/></div>
          : <div key={i} className="aiBubble">
            <div className="speaker"><RecordMark/> <span>SOUNDPILOT</span></div>
            {m.text && <p className="replytext"><Linkified text={m.text}/></p>}
            {m.clarify && <>
              {m.clarify.question && !repeatsQuestion(m.text, m.clarify.question) && <p className="replytext"><b>{m.clarify.question}</b></p>}
              {m.clarify.options.length > 0 && <div className="chipRow">{m.clarify.options.map(o => <button key={o} className="chip" disabled={i !== lastIndex || busy} onClick={() => send(o)}>{o}</button>)}</div>}
            </>}
            {m.proposals > 0 && <p className="replynote">I've drafted {m.proposals} {m.proposals === 1 ? "change" : "changes"}. Review {m.proposals === 1 ? "it" : "them"} in the listening notes panel — nothing is applied until you approve.</p>}
            <ToolTrace trace={m.trace}/>
          </div>)}
      {busy && <div className="aiBubble"><div className="speaker"><RecordMark/> <span>SOUNDPILOT</span></div><Working step={step} since={since}/></div>}
      <div ref={endRef}/>
    </div>
    <div className="promptfooter">
      <span className="overline">TRY A STUDIO DIRECTION</span>
      <div className="promptchips">{suggestionsFor(tracks).map(s => <button key={s} disabled={busy || offline} onClick={() => send(s)}>{s} <span>↗</span></button>)}</div>
      <form onSubmit={e => { e.preventDefault(); send(draft); }}><input ref={inputRef} value={draft} onChange={e => setDraft(e.target.value)} placeholder={offline ? "Backend offline — start the API to chat" : "Describe the sound you're after…"} aria-label="Instruction to AI assistant"/><button type="submit" className="accentButton" disabled={busy || offline || !draft.trim()}>{busy ? "Working…" : "Send direction ↗"}</button></form>
      {pending > 0 && <small className="pendingnote">{pending} proposal{pending === 1 ? "" : "s"} waiting for approval.</small>}
    </div>
  </section>;
}
