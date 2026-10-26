"""Transport changes with FL's "Operation unsafe at current time" handling, confirmed by readback.

Real FL (API 45) raised that error for transport.record() inside the device script's OnSysEx.
The current device script queues refused changes for OnIdle and reports ``queued``; older installed
scripts reply ``err``. Either way the server only trusts a later ``transport pos`` readback.
"""
from __future__ import annotations

import time

from mixassist.errors import ToolError

UNSAFE_RETRY_WAIT_S = 0.1
CONFIRM_POLL_S = 0.02

SCRIPT_UPDATE = ("re-copy fl_script/MixAssist/device_MixAssist.py into FL's Settings/Hardware/MixAssist folder "
                 "and reload the script")
UNSAFE_HINT = (
    "FL refused the transport change from the MixAssist script ('Operation unsafe at current time'). "
    f"The installed script is outdated: {SCRIPT_UPDATE} (the current one runs transport changes from FL's idle "
    "callback). Until then: click FL's window, close any open dialog or menu, arm recording yourself with FL's "
    "Record button and retry, or play live with record=false."
)
NOT_CONFIRMED_HINT = (
    "Click FL's window so it has focus and close any open dialog or menu (FL refuses transport changes while a "
    "modal window is open), or arm recording yourself with FL's Record button, then retry. If it keeps "
    f"happening, {SCRIPT_UPDATE}."
)


def is_unsafe(exc: ToolError) -> bool:
    return exc.code == "FL_ERROR" and "unsafe" in exc.message.lower()


def _sleep(fl, seconds: float) -> None:
    getattr(fl, "sleep", time.sleep)(seconds)


def transport(fl, cmd: str, pos_ticks: int | None = None) -> dict:
    """One transport op. If FL calls it unsafe, wait briefly and retry once, then raise with UNSAFE_HINT."""
    args: dict = {"cmd": cmd}
    if pos_ticks is not None:
        args["pos_ticks"] = int(pos_ticks)
    try:
        return fl.request("transport", args)
    except ToolError as exc:
        if not is_unsafe(exc):
            raise
    _sleep(fl, UNSAFE_RETRY_WAIT_S)
    try:
        return fl.request("transport", args)
    except ToolError as exc:
        if not is_unsafe(exc):
            raise
        raise ToolError("FL_ERROR", f"{exc.message} (also after one retry)", hint=UNSAFE_HINT) from exc


def wait_transport(fl, timeout_s: float, **want: bool) -> tuple[bool, dict]:
    """Poll `transport pos` until every wanted flag (playing, recording) matches; (matched, last reply)."""
    now = getattr(fl, "now", time.monotonic)
    deadline = now() + timeout_s
    while True:
        reply = fl.request("transport", {"cmd": "pos"})
        if all(bool(reply.get(k)) == v for k, v in want.items()):
            return True, reply
        if now() >= deadline:
            return False, reply
        _sleep(fl, CONFIRM_POLL_S)


def describe(reply: dict) -> str:
    """Readback summary for error messages, including the device script's queue state."""
    text = f"recording={bool(reply.get('recording'))}, playing={bool(reply.get('playing'))}"
    if reply.get("queued"):
        text += (f"; {reply['queued']} transport command(s) are still queued in the MixAssist script for FL's "
                 "idle callback, which FL has not run")
    if reply.get("qerr"):
        text += f"; FL refused a queued command: {reply['qerr']}"
    return text
