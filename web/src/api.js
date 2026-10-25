export const API_BASE = (import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

export class OfflineError extends Error {
  constructor(cause) {
    super("Can't reach the SoundPilot backend at " + API_BASE);
    this.name = "OfflineError";
    this.cause = cause;
  }
}

// Tool errors come back as HTTP 200 with {error:{code,message,candidates?,hint?}}.
// Non-2xx responses are normalised into the same shape so every caller handles one format.
async function request(method, path, body) {
  let res;
  try {
    res = await fetch(API_BASE + path, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    throw new OfflineError(e);
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (res.ok) return data ?? {};
  if (data && data.error) return data;
  const detail = data && data.detail;
  const message = Array.isArray(detail)
    ? detail.map(d => [...(d.loc || []).slice(1), d.msg].join(": ")).join("; ")
    : typeof detail === "string"
      ? detail
      : res.statusText || "Request failed";
  return { error: { code: res.status === 422 ? "BAD_REQUEST" : "HTTP_" + res.status, message } };
}

export const isError = x => !!(x && typeof x === "object" && x.error);

const clean = obj => Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== undefined && v !== null && v !== ""));

export const api = {
  status: () => request("GET", "/api/status"),
  mix: (includeEmpty = false) => request("GET", "/api/mix" + (includeEmpty ? "?include_empty=true" : "")),
  chat: (session_id, message) => request("POST", "/api/chat", { session_id, message }),
  apply: id => request("POST", `/api/proposals/${encodeURIComponent(id)}/apply`),
  discard: id => request("POST", `/api/proposals/${encodeURIComponent(id)}/discard`),
  undo: change_id => request("POST", "/api/undo", clean({ change_id })),
  analyzeAudio: (path, reference_path) => request("POST", "/api/analyze/audio", clean({ path, reference_path })),
  analyzeMidi: path => request("POST", "/api/analyze/midi", { path }),
  diagnose: (complaint, analysis_id, track) => request("POST", "/api/diagnose", clean({ complaint, analysis_id, track })),
  verify: (complaint, before_analysis_id, after_path) => request("POST", "/api/verify", { complaint, before_analysis_id, after_path }),
  watchPeaks: (seconds, from_bar) => request("POST", "/api/watch_peaks", clean({ seconds, from_bar })),
  fixMidi: args => request("POST", "/api/midi/fix", clean(args)),
  playMidi: args => request("POST", "/api/midi/play", clean(args)),
};

export function newSessionId() {
  if (globalThis.crypto?.randomUUID) return "web-" + crypto.randomUUID();
  return "web-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
}
