import React from "react";

// Renders a §3.1 tool error: {code, message, candidates?, hint?}.
export default function ErrorNote({ error, onCandidate }) {
  if (!error) return null;
  const e = error.error || error;
  return <div className="errorNote" role="alert">
    <span className="errorCode">{e.code || "ERROR"}</span>
    <p>{e.message || String(e)}</p>
    {e.candidates?.length > 0 && <div className="chipRow">
      {e.candidates.map(c => onCandidate
        ? <button key={c} className="chip" onClick={() => onCandidate(c)}>{c}</button>
        : <span key={c} className="chip static">{c}</span>)}
    </div>}
    {e.hint && <small>{e.hint}</small>}
  </div>;
}
