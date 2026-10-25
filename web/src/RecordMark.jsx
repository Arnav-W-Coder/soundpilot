import React from "react";

export default function RecordMark({ large = false }) {
  return <svg className={large ? "recordmark large" : "recordmark"} viewBox="0 0 72 72" role="img" aria-label="SoundPilot vinyl record and waveform logo"><defs><linearGradient id="amber" x1="0" y1="0" x2="1" y2="1"><stop stopColor="#ffd39a"/><stop offset=".55" stopColor="#f0a356"/><stop offset="1" stopColor="#b96b38"/></linearGradient></defs><circle cx="36" cy="36" r="32" fill="#221a16" stroke="url(#amber)" strokeWidth="2.5"/><circle cx="36" cy="36" r="25" fill="none" stroke="#a4744d" strokeOpacity=".45"/><circle cx="36" cy="36" r="19" fill="none" stroke="#a4744d" strokeOpacity=".45"/><circle cx="36" cy="36" r="12" fill="none" stroke="#a4744d" strokeOpacity=".45"/><path d="M10 37h10l4-13 6 25 7-33 7 37 5-24 4 8h9" fill="none" stroke="url(#amber)" strokeWidth="3.4" strokeLinecap="round" strokeLinejoin="round"/><circle cx="36" cy="36" r="3.5" fill="#f2b57b"/></svg>;
}
