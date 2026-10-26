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
  return readResponse(res);
}

async function readResponse(res) {
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

// POST /api/chat/stream: server-sent `step` events ({type, text}) while the turn runs, then `result` (the
// /api/chat payload) or `error`. Falls back to /api/chat on a backend without the stream endpoint.
async function chatStream(session_id, message, onStep) {
  const body = JSON.stringify({ session_id, message });
  let res;
  try {
    res = await fetch(API_BASE + "/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body,
    });
  } catch (e) {
    throw new OfflineError(e);
  }
  if (res.status === 404 || res.status === 405 || !res.body) return request("POST", "/api/chat", { session_id, message });
  if (!res.ok) return readResponse(res);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let final = null;
  for (;;) {
    let chunk;
    try {
      chunk = await reader.read();
    } catch (e) {
      throw new OfflineError(e);
    }
    if (chunk.done) break;
    buffer += decoder.decode(chunk.value, { stream: true });
    let end;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (!data) continue;
      const payload = JSON.parse(data);
      if (event === "step") onStep?.(payload);
      else if (event === "result" || event === "error") final = payload;
    }
  }
  return final ?? { error: { code: "STREAM_ENDED", message: "The chat stream ended before a reply arrived" } };
}

export const isError = x => !!(x && typeof x === "object" && x.error);

const clean = obj => Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== undefined && v !== null && v !== ""));

export const api = {
  status: () => request("GET", "/api/status"),
  mix: (includeEmpty = false) => request("GET", "/api/mix" + (includeEmpty ? "?include_empty=true" : "")),
  chat: (session_id, message) => request("POST", "/api/chat", { session_id, message }),
  chatStream,
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
