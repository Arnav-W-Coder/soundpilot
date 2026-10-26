"""web_fetch: SSRF-guarded HTTP(S) GET with manual redirects, a byte cap and readable-text extraction."""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from mixassist.errors import ToolError

log = logging.getLogger(__name__)

MAX_REDIRECTS = 4
MAX_BYTES = 1_500_000
ALLOWED_PORTS = (None, 80, 443)
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "text/markdown", "application/json", "text/xml",
              "application/xml")
USER_AGENT = "Mozilla/5.0 (compatible; SoundPilot/0.1; +https://github.com/Arnav-W-Coder/soundpilot)"
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".intranet", ".corp")


def _resolve(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def _ip_allowed(text: str) -> bool:
    try:
        ip = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return ip.is_global and not ip.is_multicast


def check_url(url: str) -> str:
    """Raise ToolError unless ``url`` is http(s) on a public host (every resolved address must be global)."""
    if not isinstance(url, str) or not url.strip():
        raise ToolError("OUT_OF_RANGE", "url must be a non-empty http(s) URL")
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        raise ToolError("UNSUPPORTED", f"only http(s) URLs can be fetched, not {parts.scheme or 'a bare path'!r}")
    if parts.username or parts.password:
        raise ToolError("UNSUPPORTED", "URLs with credentials are not fetched")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise ToolError("OUT_OF_RANGE", "url has no host")
    try:
        port = parts.port
    except ValueError:
        raise ToolError("OUT_OF_RANGE", "url has an invalid port") from None
    if port not in ALLOWED_PORTS:
        raise ToolError("UNSUPPORTED", f"port {port} is not allowed (only 80 and 443)")
    if host == "localhost" or host.endswith(_BLOCKED_SUFFIXES):
        raise ToolError("UNSUPPORTED", f"{host} is a local address and is never fetched")
    try:
        addresses = _resolve(host, port or (443 if parts.scheme.lower() == "https" else 80))
    except (OSError, UnicodeError) as exc:
        raise ToolError("NOT_FOUND", f"could not resolve {host}: {exc}") from None
    if not addresses:
        raise ToolError("NOT_FOUND", f"could not resolve {host}")
    bad = [a for a in addresses if not _ip_allowed(a)]
    if bad:
        raise ToolError("UNSUPPORTED", f"{host} resolves to a private, loopback or link-local address; not fetched")
    return url


def _check_peer(resp: httpx.Response, url: str) -> None:
    """Re-check the address we actually connected to (guards against DNS rebinding between check and connect)."""
    stream = resp.extensions.get("network_stream")
    try:
        peer = stream.get_extra_info("server_addr") if stream is not None else None
    except Exception:  # noqa: BLE001 - not every transport exposes it
        peer = None
    if isinstance(peer, tuple) and peer and not _ip_allowed(str(peer[0])):
        raise ToolError("UNSUPPORTED", f"{urlsplit(url).hostname} connected to a non-public address; not fetched")


# ------------------------------------------------------------- extraction
_SKIP = {"script", "style", "noscript", "template", "svg", "iframe", "canvas", "button", "select", "form", "object"}
_CHROME = {"nav", "header", "footer", "aside", "menu"}
_BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "main", "blockquote", "pre",
          "dd", "dt", "dl", "h1", "h2", "h3", "h4", "h5", "h6", "figcaption", "hr", "td", "th"}
_VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"}
_CHROME_ROLES = {"navigation", "banner", "contentinfo", "complementary", "search", "menu", "menubar"}


class _Extractor(HTMLParser):
    def __init__(self, skip_chrome: bool):
        super().__init__(convert_charrefs=True)
        self.skip_chrome = skip_chrome
        self.title = ""
        self._in_title = False
        self._skip_stack: list[str] = []
        self._main_depth = 0
        self.parts: list[str] = []
        self.main_parts: list[str] = []

    def _emit(self, text: str) -> None:
        self.parts.append(text)
        if self._main_depth:
            self.main_parts.append(text)

    def handle_starttag(self, tag, attrs):
        if tag in _VOID:
            if tag in _BLOCK and not self._skip_stack:
                self._emit("\n")
            return
        attrs = dict(attrs)
        role = (attrs.get("role") or "").lower()
        hidden = attrs.get("aria-hidden") == "true" or "hidden" in attrs
        if self._skip_stack or tag in _SKIP or hidden or (self.skip_chrome and (tag in _CHROME or role in _CHROME_ROLES)):
            self._skip_stack.append(tag)
            return
        if tag == "title":
            self._in_title = not self.title
            return
        if tag in ("main", "article") or role == "main":
            self._main_depth += 1
        if tag in _BLOCK:
            self._emit("\n- " if tag == "li" else "\n")

    def handle_endtag(self, tag):
        if self._skip_stack:
            if tag in self._skip_stack:
                while self._skip_stack and self._skip_stack.pop() != tag:
                    pass
            return
        if tag == "title":
            self._in_title = False
            return
        if tag in _BLOCK:
            self._emit("\n")
        if tag in ("main", "article") and self._main_depth:
            self._main_depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if not self._skip_stack:
            self._emit(data)


def _tidy(text: str) -> str:
    lines = [re.sub(r"[ \t\u00a0\r\f\v]+", " ", line).strip() for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if line in ("", "-"):
            if out and out[-1] != "":
                out.append("")
            continue
        out.append(line)
    return "\n".join(out).strip()


def html_to_text(html: str) -> tuple[str, str]:
    """(title, readable text): drops scripts, styles, forms and page chrome; prefers <main>/<article> when present."""
    def run(skip_chrome: bool):
        parser = _Extractor(skip_chrome)
        parser.feed(html)
        parser.close()
        return parser

    parser = run(skip_chrome=True)
    text, main = _tidy("".join(parser.parts)), _tidy("".join(parser.main_parts))
    if len(main) >= 300:
        text = main
    if len(text) < 100:
        loose = _tidy("".join(run(skip_chrome=False).parts))
        if len(loose) >= 200:
            text = loose
    return " ".join(parser.title.split()), text


def truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    cut = text[:max_chars]
    space = cut.rfind(" ", max(0, max_chars - 200))
    return (cut[:space] if space > 0 else cut).rstrip() + " …", True


# ------------------------------------------------------------------ fetch
def fetch_url(url: str, max_chars: int, timeout: float, transport: httpx.BaseTransport | None = None) -> dict:
    """GET a public page and return {url, title, text, truncated, content_type}. Raises ToolError."""
    deadline = time.monotonic() + timeout
    current = check_url(url)
    with httpx.Client(timeout=httpx.Timeout(timeout), follow_redirects=False, trust_env=False, transport=transport,
                      headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1"}) as client:
        for hop in range(MAX_REDIRECTS + 1):
            try:
                with client.stream("GET", current) as resp:
                    _check_peer(resp, current)
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location")
                        if not location:
                            raise ToolError("NOT_FOUND", f"redirect without a Location from {current}")
                        nxt = urljoin(current, location)
                        log.info("web_fetch redirect %d: %s -> %s", resp.status_code, current, nxt)
                        current = check_url(nxt)
                        continue
                    if resp.status_code >= 400:
                        raise ToolError("NOT_FOUND", f"{current} answered HTTP {resp.status_code}")
                    ctype = (resp.headers.get("content-type") or "text/html").split(";")[0].strip().lower()
                    if not ctype.startswith("text/") and ctype not in TEXT_TYPES:
                        raise ToolError("UNSUPPORTED", f"{current} is {ctype}, not a text page")
                    body = bytearray()
                    for chunk in resp.iter_bytes():
                        body += chunk
                        if len(body) >= MAX_BYTES:
                            del body[MAX_BYTES:]
                            break
                        if time.monotonic() > deadline:
                            raise ToolError("NOT_FOUND", f"{current} took longer than {timeout:g} s")
                    raw = bytes(body).decode(resp.charset_encoding or "utf-8", errors="replace")
                    break
            except httpx.TimeoutException:
                raise ToolError("NOT_FOUND", f"{current} took longer than {timeout:g} s") from None
            except httpx.HTTPError as exc:
                raise ToolError("NOT_FOUND", f"could not fetch {current}: {type(exc).__name__}") from None
        else:
            raise ToolError("NOT_FOUND", f"too many redirects (> {MAX_REDIRECTS}) from {url}")
    if ctype in ("text/html", "application/xhtml+xml") or raw.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        title, text = html_to_text(raw)
    else:
        title, text = "", _tidy(raw)
    text, cut = truncate(text, max_chars)
    return {"url": current, "title": title[:200], "text": text, "truncated": cut, "content_type": ctype}
