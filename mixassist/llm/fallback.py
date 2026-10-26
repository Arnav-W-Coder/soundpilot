"""Provider chain: switch to the next provider when one stays rate limited, instead of backing off for minutes."""
from __future__ import annotations

import logging
import threading
import time

from mixassist import progress
from mixassist.llm.base import LLMProvider, LLMResponse, RateLimitedError

log = logging.getLogger(__name__)

DEFAULT_COOLDOWN_S = 120.0


class FallbackProvider(LLMProvider):
    """Tries providers in order. After a switch, calls go to the fallback until ``cooldown_s`` has passed."""

    def __init__(self, providers: list[LLMProvider], cooldown_s: float = DEFAULT_COOLDOWN_S, clock=time.monotonic):
        if not providers:
            raise ValueError("FallbackProvider needs at least one provider")
        self.providers = providers
        self.cooldown_s = cooldown_s
        self.clock = clock
        self._active = 0
        self._since = 0.0
        self._lock = threading.Lock()

    @property
    def active(self) -> LLMProvider:
        return self.providers[self._active]

    @property
    def name(self) -> str:
        return self.active.name

    @property
    def model(self) -> str:
        return self.active.model

    def _start_index(self) -> int:
        with self._lock:
            if self._active and self.clock() - self._since >= self.cooldown_s:
                log.info("fallback: %.0f s since the switch; trying %s %s again", self.cooldown_s,
                         self.providers[0].name, self.providers[0].model)
                self._active = 0
            return self._active

    def chat(self, messages: list[dict], tools: list[dict] | None, temperature: float = 0.0) -> LLMResponse:
        start = self._start_index()
        for i in range(start, len(self.providers)):
            provider = self.providers[i]
            try:
                return provider.chat(messages, tools, temperature)
            except RateLimitedError as exc:
                if i + 1 >= len(self.providers):
                    raise
                nxt = self.providers[i + 1]
                log.warning("fallback: %s %s still failing (%s); switching to %s %s for %.0f s", provider.name,
                            provider.model, str(exc)[:120], nxt.name, nxt.model, self.cooldown_s)
                progress.emit("fallback", f"{provider.name} is rate limited; switching to {nxt.name} ({nxt.model})",
                              provider=nxt.name, model=nxt.model)
                with self._lock:
                    self._active, self._since = i + 1, self.clock()
        raise AssertionError("unreachable")

    def check_model(self) -> tuple[bool | None, str]:
        ok, message = self.providers[0].check_model()
        chain = ", then ".join(f"{p.name} {p.model}" for p in self.providers[1:])
        return ok, f"{message} (fallback on rate limits: {chain})"
