"""FL access: the process-wide active FLClient (FakeFL by default, or the SysEx bridge)."""
from __future__ import annotations

import logging
import os
import threading

from mixassist.errors import ToolError
from mixassist.fl.client import DisconnectedTransport, FLClient

log = logging.getLogger(__name__)

_active: FLClient | None = None
_lock = threading.Lock()


def make_client(backend: str | None = None) -> FLClient:
    backend = (backend or os.environ.get("SOUNDPILOT_FL") or "fake").lower()
    if backend == "fake":
        from mixassist.fl.fake import FakeTransport, SkipClock, default_fake

        return FLClient(FakeTransport(default_fake(clock=SkipClock())), "fake")
    if backend == "bridge":
        try:
            from mixassist.fl.bridge import BridgeTransport

            return FLClient(BridgeTransport(), "bridge")
        except ToolError as exc:
            log.warning("FL bridge not connected: %s", exc.message)
            return FLClient(DisconnectedTransport(exc), "bridge")
        except ImportError as exc:
            err = ToolError("FL_NOT_CONNECTED", f"bridge unavailable: {exc}", hint="use --fake")
            return FLClient(DisconnectedTransport(err), "bridge")
    raise ValueError(f"unknown FL backend {backend!r} (use 'fake' or 'bridge')")


def get_fl() -> FLClient:
    global _active
    with _lock:
        if _active is None:
            _active = make_client()
        return _active


def set_fl(client: FLClient | None) -> None:
    global _active
    with _lock:
        _active = client


__all__ = ["FLClient", "get_fl", "set_fl", "make_client"]
