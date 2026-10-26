import React from "react";

const LINK = /\[([^\]\n]{1,200})\]\((https?:\/\/[^\s)]+)\)|https?:\/\/[^\s<>"'`]+/g;

function trimUrl(url) {
  let end = url.length;
  while (end > 0 && /[.,;:!?'"\]]/.test(url[end - 1])) end--;
  if (url[end - 1] === ")" && !url.slice(0, end).includes("(")) end--;
  return [url.slice(0, end), url.slice(end)];
}

function host(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; }
}

function short(url) {
  const bare = url.replace(/^https?:\/\/(www\.)?/i, "").replace(/\/$/, "");
  return bare.length > 48 ? bare.slice(0, 47) + "…" : bare;
}

export function ExternalLink({ href, children }) {
  if (!/^https?:\/\//i.test(href)) return <>{children}</>;
  return <a className="weblink" href={href} target="_blank" rel="noopener noreferrer nofollow">{children}</a>;
}

export function Linkified({ text }) {
  if (!text) return null;
  const out = [];
  let last = 0;
  for (const m of text.matchAll(LINK)) {
    out.push(text.slice(last, m.index));
    if (m[1]) {
      out.push(<ExternalLink key={m.index} href={m[2]}>{m[1]}</ExternalLink>);
    } else {
      const [url, rest] = trimUrl(m[0]);
      out.push(<ExternalLink key={m.index} href={url}>{short(url)}</ExternalLink>, rest);
    }
    last = m.index + m[0].length;
  }
  out.push(text.slice(last));
  return <>{out}</>;
}

export function WebSources({ tool, result }) {
  if (!result || result.error) return null;
  if (tool === "web_search" && result.results?.length) {
    return <ul className="websources">{result.results.map(r => <li key={r.url}>
      <ExternalLink href={r.url}>{r.title || host(r.url)}</ExternalLink> <span className="webhost">{host(r.url)}</span>
    </li>)}</ul>;
  }
  if (tool === "web_fetch" && result.url) {
    return <ul className="websources"><li>
      <ExternalLink href={result.url}>{result.title || host(result.url)}</ExternalLink> <span className="webhost">{host(result.url)}</span>
    </li></ul>;
  }
  return null;
}
