"""Live progress events for a chat turn (LLM calls, rate-limit waits, provider switches, tools).

``with listen(callback):`` routes every ``emit(...)`` made in that context (the agent loop, the LLM providers,
the tool dispatcher in the agent) to ``callback({"type": ..., "text": ..., **data})``. Without a listener,
``emit`` does nothing. The streaming chat endpoint uses it to show what the assistant is doing right now.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable

log = logging.getLogger(__name__)

_listener: ContextVar[Callable[[dict], None] | None] = ContextVar("mixassist_progress", default=None)


@contextmanager
def listen(callback: Callable[[dict], None]):
    token = _listener.set(callback)
    try:
        yield
    finally:
        _listener.reset(token)


def emit(kind: str, text: str, **data) -> None:
    callback = _listener.get()
    if callback is None:
        return
    try:
        callback({"type": kind, "text": text, **data})
    except Exception:  # noqa: BLE001 - a broken listener must never break the turn
        log.exception("progress listener failed")
