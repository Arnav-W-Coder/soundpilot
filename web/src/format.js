const isNum = n => typeof n === "number" && Number.isFinite(n);

export const fixed = (n, digits = 1) => (isNum(n) ? n.toFixed(digits) : "–");
export const signed = (n, digits = 1) => (isNum(n) ? (n > 0 ? "+" : "") + n.toFixed(digits) : "–");
export const db = (n, unit = "dB") => (isNum(n) ? signed(n) + " " + unit : "–");

export function pan(pct) {
  if (!isNum(pct)) return "–";
  const r = Math.round(pct);
  if (r === 0) return "C";
  return (r < 0 ? "L " : "R ") + Math.abs(r);
}

export const pct = n => (isNum(n) ? Math.round(n) + "%" : "–");

export function hz(n) {
  if (!isNum(n)) return "–";
  return n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1).replace(/\.0$/, "") + " kHz" : Math.round(n) + " Hz";
}

export function duration(s) {
  if (!isNum(s)) return "–";
  const m = Math.floor(s / 60);
  return String(m).padStart(2, "0") + ":" + String(Math.round(s % 60)).padStart(2, "0");
}

const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
export const pitchName = p => (isNum(p) ? NOTE_NAMES[p % 12] + (Math.floor(p / 12) - 1) : "–");

export const time = () => new Date().toLocaleTimeString();

// Track colors cycle through the original vinyl-studio palette.
const PALETTE = ["#eeaa71", "#d2a5a0", "#c6a878", "#9eac8e", "#b2a3c7", "#d18d68", "#8aa2a4"];
export const trackColor = index => (index === 0 ? "#f1b273" : PALETTE[(index - 1) % PALETTE.length]);

const FIELD_LABELS = {
  volume_db: "volume",
  volume_change_db: "volume change",
  pan_pct: "pan",
  stereo_width_pct: "width",
  eq_band: "EQ band",
  eq_gain_db: "EQ gain",
  eq_freq_hz: "EQ freq",
  muted: "mute",
  solo: "solo",
};
export const fieldLabel = f => FIELD_LABELS[f] || f.replace(/_/g, " ");

// Formats a value of a mixer field (as used in fl_set_track before/after and preview.changes).
export function fieldValue(field, value, unit) {
  if (value === null || value === undefined) return "–";
  if (typeof value === "boolean") return value ? "on" : "off";
  if (typeof value === "object") return value.name ?? JSON.stringify(value);
  if (typeof value !== "number") return String(value);
  if (field === "pan_pct") return pan(value);
  if (field === "stereo_width_pct") return pct(value);
  if (field === "eq_freq_hz" || unit === "Hz") return hz(value);
  if (unit === "%") return pct(value);
  if (unit) return fixed(value) + " " + unit;
  if (/_db$/.test(field)) return fixed(value) + " dB";
  return String(value);
}
