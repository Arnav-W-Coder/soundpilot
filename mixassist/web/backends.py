"""Search backends behind one interface: ddgs (default, no key), Ollama, Brave and Tavily (keyed).

SOUNDPILOT_WEB_BACKEND = auto (default) | ddgs | ollama | brave | tavily. ``auto`` uses the first key it finds
(OLLAMA_API_KEY, BRAVE_API_KEY, TAVILY_API_KEY) and otherwise ddgs. A failing keyed backend falls back to ddgs.
"""
from __future__ import annotations

import concurrent.futures
import logging
import os
import time

import httpx

from mixassist.errors import ToolError

log = logging.getLogger(__name__)

BACKENDS = ("ddgs", "ollama", "brave", "tavily")
KEY_ENV = {"ollama": "OLLAMA_API_KEY", "brave": "BRAVE_API_KEY", "tavily": "TAVILY_API_KEY"}


class SearchBackend:
    name = "base"

    def search(self, query: str, max_results: int, timeout: float) -> list[dict]:
        """Raw hits as [{title, url, snippet}]; raises ToolError or httpx errors."""
        raise NotImplementedError

    def fetch(self, url: str, timeout: float) -> dict | None:
        """Optional remote page fetch {title, text}; None when the backend has none."""
        return None


class DDGSBackend(SearchBackend):
    """Keyless metasearch through the ``ddgs`` package (DuckDuckGo, Bing, Brave, Yahoo, ... in shuffled order).

    Single engines often answer "no results" to scrapers, so an empty answer is retried while time is left.
    """

    name = "ddgs"
    attempts = 3

    def search(self, query, max_results, timeout):
        try:
            from ddgs import DDGS
        except ImportError as exc:
            raise ToolError("UNSUPPORTED", f"web search needs the ddgs package ({exc})") from None

        deadline = time.monotonic() + timeout
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="ddgs")
        hits: list = []
        problem = None
        try:
            for attempt in range(1, self.attempts + 1):
                left = deadline - time.monotonic()
                if left < 1.0:
                    break
                engine_timeout = max(1, int(min(left, 5)))
                future = pool.submit(lambda: DDGS(timeout=engine_timeout).text(query, max_results=max_results))
                try:
                    hits = future.result(timeout=left) or []
                except concurrent.futures.TimeoutError:
                    problem = f"took longer than {timeout:g} s"
                    break
                except Exception as exc:  # noqa: BLE001 - ddgs raises its own types (no results, rate limit)
                    if "no results" not in str(exc).lower():
                        problem = f"failed: {type(exc).__name__}: {exc}"
                    hits = []
                if hits:
                    break
                log.info("ddgs: no results for %r on attempt %d", query, attempt)
        finally:
            pool.shutdown(wait=False)
        if not hits and problem:
            raise ToolError("NOT_FOUND", f"web search {problem}")
        return [{"title": h.get("title"), "url": h.get("href") or h.get("url"), "snippet": h.get("body")}
                for h in hits or []]


class _Keyed(SearchBackend):
    def __init__(self, key: str, transport: httpx.BaseTransport | None = None):
        self.key = key
        self.transport = transport

    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(timeout=httpx.Timeout(timeout), transport=self.transport)

    def _check(self, resp: httpx.Response) -> dict:
        if resp.status_code in (401, 403):
            raise ToolError("UNSUPPORTED", f"{self.name} web search rejected the API key (HTTP {resp.status_code})")
        if resp.status_code != 200:
            raise ToolError("NOT_FOUND", f"{self.name} web search answered HTTP {resp.status_code}")
        return resp.json()


class OllamaBackend(_Keyed):
    """https://docs.ollama.com/capabilities/web-search (POST /api/web_search and /api/web_fetch, Bearer key)."""

    name = "ollama"
    base = "https://ollama.com/api"

    def search(self, query, max_results, timeout):
        with self._client(timeout) as c:
            data = self._check(c.post(f"{self.base}/web_search", json={"query": query, "max_results": max_results},
                                      headers={"Authorization": f"Bearer {self.key}"}))
        return [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")}
                for r in data.get("results") or []]

    def fetch(self, url, timeout):
        with self._client(timeout) as c:
            data = self._check(c.post(f"{self.base}/web_fetch", json={"url": url},
                                      headers={"Authorization": f"Bearer {self.key}"}))
        return {"title": data.get("title") or "", "text": data.get("content") or ""}


class BraveBackend(_Keyed):
    """Brave Search API: GET /res/v1/web/search with X-Subscription-Token."""

    name = "brave"
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def search(self, query, max_results, timeout):
        with self._client(timeout) as c:
            data = self._check(c.get(self.endpoint, params={"q": query, "count": max_results},
                                     headers={"X-Subscription-Token": self.key, "Accept": "application/json"}))
        return [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("description")}
                for r in (data.get("web") or {}).get("results") or []]


class TavilyBackend(_Keyed):
    """Tavily: POST /search with a Bearer key."""

    name = "tavily"
    endpoint = "https://api.tavily.com/search"

    def search(self, query, max_results, timeout):
        with self._client(timeout) as c:
            data = self._check(c.post(self.endpoint, json={"query": query, "max_results": max_results,
                                                           "search_depth": "basic"},
                                      headers={"Authorization": f"Bearer {self.key}"}))
        return [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")}
                for r in data.get("results") or []]


_KEYED = {"ollama": OllamaBackend, "brave": BraveBackend, "tavily": TavilyBackend}


def backend_name() -> str:
    wanted = (os.environ.get("SOUNDPILOT_WEB_BACKEND") or "auto").strip().lower()
    if wanted in BACKENDS:
        return wanted
    if wanted != "auto":
        log.warning("SOUNDPILOT_WEB_BACKEND=%r is unknown; using auto", wanted)
    return next((name for name in ("ollama", "brave", "tavily") if os.environ.get(KEY_ENV[name])), "ddgs")


def get_backend(name: str | None = None) -> SearchBackend:
    name = name or backend_name()
    if name == "ddgs":
        return DDGSBackend()
    key = os.environ.get(KEY_ENV[name])
    if not key:
        log.warning("web backend %s needs %s; using ddgs", name, KEY_ENV[name])
        return DDGSBackend()
    return _KEYED[name](key)
