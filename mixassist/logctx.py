"""One-line progress logs: a per-request tag plus elapsed time on every mixassist log line.

``with log_context("s=1a2b3c4d#2"):`` tags everything logged in that thread (chat turn, LLM calls,
retries, tools) as ``[s=1a2b3c4d#2 +3.4s]``. ``configure()`` attaches a stderr handler to the
``mixassist`` logger when nothing else is configured (uvicorn sets up only its own loggers).
"""
from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar

_ctx: ContextVar[tuple[str, float] | None] = ContextVar("mixassist_log_ctx", default=None)

FORMAT = "%(sp_level)s%(asctime)s %(sp_prefix)s%(sp_name)s: %(message)s"  # level column as uvicorn pads it
MAX_STR = 60
MAX_ITEMS = 6
MAX_CHARS = 400


@contextmanager
def log_context(tag: str):
    token = _ctx.set((tag, time.monotonic()))
    try:
        yield
    finally:
        _ctx.reset(token)


def elapsed() -> float | None:
    ctx = _ctx.get()
    return None if ctx is None else time.monotonic() - ctx[1]


def prefix() -> str:
    ctx = _ctx.get()
    if ctx is None:
        return ""
    return f"[{ctx[0]} +{time.monotonic() - ctx[1]:.1f}s] "


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.sp_level = (record.levelname + ":").ljust(10)
        record.sp_prefix = prefix()
        record.sp_name = record.name.removeprefix("mixassist.")
        return True


def make_handler(stream=None) -> logging.Handler:
    handler = logging.StreamHandler(stream)
    handler.addFilter(ContextFilter())
    handler.setFormatter(logging.Formatter(FORMAT, datefmt="%H:%M:%S"))
    return handler


def configure() -> None:
    """Log mixassist INFO lines to stderr unless the app (or a test runner) configured logging already."""
    pkg = logging.getLogger("mixassist")
    if pkg.handlers or logging.getLogger().handlers:
        return
    pkg.addHandler(make_handler())
    pkg.setLevel(os.environ.get("SOUNDPILOT_LOG", "INFO").upper())


def clip(text: str, limit: int) -> str:
    """Head of a message, for chat text, warnings and errors."""
    text = str(text)
    return text if len(text) <= limit else text[:limit - 3] + "..."


def shorten(value, max_str: int = MAX_STR, max_items: int = MAX_ITEMS):
    """Shrink a JSON-like value for a log line: long strings keep their tail (file names), long lists their head."""
    if isinstance(value, str):
        return value if len(value) <= max_str else "..." + value[-(max_str - 3):]
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, dict):
        return {k: shorten(v, max_str, max_items) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        head = [shorten(v, max_str, max_items) for v in value[:max_items]]
        return head + [f"...+{len(value) - max_items}"] if len(value) > max_items else head
    return value


def compact(value, limit: int = MAX_CHARS, max_str: int = MAX_STR) -> str:
    text = json.dumps(shorten(value, max_str), default=str, separators=(",", ":"), ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + f"...(+{len(text) - limit} chars)"
